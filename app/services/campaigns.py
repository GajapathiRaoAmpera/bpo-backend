"""Campaign audience selection and launch: 3 calls per launch, simulated in the sandbox or dialled via Plivo."""

import logging
import re
from datetime import timedelta

import plivo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.formatting import IST
from app.models import Call, Campaign, CampaignTarget, Customer, Policy, QAScore
from app.models.enums import (
    CallDirection, CallMode, CallOutcome, CallStatus, CampaignStatus, PolicyStatus, TargetStatus,
)
from app.services.scripts import active_script

log = logging.getLogger(__name__)

LAUNCH_CALLS = 3


def audience(product, segment: dict):
    today = utcnow().astimezone(IST).date()
    return (select(Policy.id).join(Customer, Customer.id == Policy.customer_id)
            .where(Policy.product == product, Policy.status.in_([PolicyStatus.active, PolicyStatus.due]),
                   Policy.renewal_date.between(today, today + timedelta(days=segment["renewal_within_days"])),
                   Customer.preferred_language.in_(segment["languages"])))


def audience_size(db: Session, product, segment: dict) -> int:
    return db.scalar(select(func.count()).select_from(audience(product, segment).subquery())) or 0


def set_targets(db: Session, c: Campaign) -> None:
    db.execute(delete(CampaignTarget).where(CampaignTarget.campaign_id == c.id))
    ids = db.scalars(audience(c.product, c.segment)).all()
    db.add_all(CampaignTarget(campaign_id=c.id, policy_id=pid) for pid in ids)
    c.total_targets = len(ids)


def launch(db: Session, c: Campaign) -> list[int]:
    """Place up to 3 simulated calls (synthetic transcript, AI QA score) and update progress.
    Progress counts targets, not attempts (docs/DATABASE.md)."""
    from app.seed import _Gen  # reuse the seed's transcript/summary/side-effect writers

    script = active_script(db, CallMode.renewal)
    targets = _next_targets(db, c)
    gen = _Gen(db, rng_seed=c.id * 1000 + c.dialled)
    now = utcnow()
    day, seq = _ref_seq(db, now)
    call_ids = []
    for i, t in enumerate(targets):
        policy = db.get(Policy, t.policy_id)
        customer = db.get(Customer, policy.customer_id)
        outcome = gen._outcome(CallMode.renewal)
        duration = gen.rng.randrange(8, 35) if outcome == CallOutcome.no_answer else gen.rng.randrange(95, 300)
        started = now - timedelta(seconds=duration + (LAUNCH_CALLS - i) * 5)
        call = Call(call_ref=f"{day}{seq + i + 1:04d}", direction=CallDirection.outbound, mode=CallMode.renewal,
                    customer_id=customer.id, policy_id=policy.id, campaign_id=c.id,
                    script_id=script.id if script else c.script_id,
                    status=CallStatus.escalated if outcome == CallOutcome.escalated else CallStatus.completed,
                    started_at=started, ended_at=started + timedelta(seconds=duration), duration_sec=duration,
                    outcome=outcome, sentiment=gen._sentiment(outcome), verified=outcome != CallOutcome.no_answer,
                    is_simulated=True)
        call.summary, call.next_action = gen._summary(call, customer, policy)
        db.add(call)
        db.flush()
        if outcome != CallOutcome.no_answer:
            gen._transcript(call, customer, policy)
            db.add(QAScore(call_id=call.id, ai_disclosure=True, identity_verified=True, correct_figures=True,
                           no_mis_selling=True, polite_close=True, score=5, source="ai"))
        gen._side_effects(call, customer, policy, started, None)

        c.dialled += t.status == TargetStatus.pending
        if outcome == CallOutcome.no_answer:
            t.status = TargetStatus.failed
        else:
            converted = outcome in (CallOutcome.renewed, CallOutcome.payment_link_sent)
            c.connected += 1
            c.converted += converted
            t.status = TargetStatus.converted if converted else TargetStatus.called
        t.call_id = call.id
        call_ids.append(call.id)
    _after_batch(db, c)
    return call_ids


def _next_targets(db: Session, c: Campaign, allow_prefix: str = "") -> list[CampaignTarget]:
    stmt = (select(CampaignTarget).join(Policy, Policy.id == CampaignTarget.policy_id)
            .where(CampaignTarget.campaign_id == c.id,
                   CampaignTarget.status.in_([TargetStatus.pending, TargetStatus.failed])))
    if allow_prefix:  # staging: never ring a stranger whose number Faker happened to generate
        stmt = stmt.join(Customer, Customer.id == Policy.customer_id).where(Customer.customer_code.startswith(allow_prefix))
    return db.scalars(stmt.order_by(CampaignTarget.status.desc(), Policy.renewal_date, Policy.id)  # pending first
                      .limit(LAUNCH_CALLS)).all()


def _ref_seq(db: Session, now) -> tuple[str, int]:
    day = f"CALL-{now.astimezone(IST):%Y%m%d}-"
    return day, db.scalar(select(func.count()).select_from(Call).where(Call.call_ref.like(f"{day}%"))) or 0


def _after_batch(db: Session, c: Campaign) -> None:
    c.status = CampaignStatus.running
    remaining = db.scalar(select(func.count()).select_from(CampaignTarget).where(
        CampaignTarget.campaign_id == c.id, CampaignTarget.status == TargetStatus.pending))
    if not remaining:
        c.status = CampaignStatus.completed
    db.commit()


def dial(db: Session, c: Campaign) -> list[int]:
    """Ring up to 3 customers through Plivo. When one answers, Plivo fetches our answer URL and the
    bot joins over a WebSocket (api/v1/telephony.py); the hangup URL closes the call."""
    s = get_settings()
    client = plivo.RestClient(s.plivo_auth_id, s.plivo_auth_token)
    hooks = f"{s.public_base_url}/api/v1/telephony/plivo"
    script = active_script(db, CallMode.renewal)
    now = utcnow()
    day, seq = _ref_seq(db, now)
    call_ids = []
    for i, t in enumerate(_next_targets(db, c, s.contact_allowlist_prefix)):
        policy = db.get(Policy, t.policy_id)
        customer = db.get(Customer, policy.customer_id)
        call = Call(call_ref=f"{day}{seq + i + 1:04d}", direction=CallDirection.outbound, mode=CallMode.renewal,
                    customer_id=customer.id, policy_id=policy.id, campaign_id=c.id,
                    script_id=script.id if script else c.script_id, status=CallStatus.in_progress,
                    started_at=now, is_simulated=False)
        db.add(call)
        db.flush()
        c.dialled += t.status == TargetStatus.pending
        t.call_id, t.status = call.id, TargetStatus.called
        db.commit()  # Plivo may request the answer URL as soon as the customer picks up
        try:
            client.calls.create(from_=s.plivo_from_number, to_="91" + re.sub(r"\D", "", customer.phone)[-10:],
                                answer_url=f"{hooks}/answer/{call.id}", hangup_url=f"{hooks}/hangup/{call.id}")
        except Exception:  # Plivo/network error: record a failed attempt, keep dialling the rest
            log.exception("Plivo could not place call %s", call.call_ref)
            call.status, call.ended_at, call.outcome = CallStatus.failed, utcnow(), CallOutcome.no_answer
            t.status = TargetStatus.failed
        call_ids.append(call.id)
    _after_batch(db, c)
    return call_ids
