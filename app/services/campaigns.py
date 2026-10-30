"""Campaign audience selection and the sandbox launch, which places 3 simulated calls."""

from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.formatting import IST
from app.models import Call, Campaign, CampaignTarget, Customer, Policy, QAScore
from app.models.enums import (
    CallDirection, CallMode, CallOutcome, CallStatus, CampaignStatus, PolicyStatus, TargetStatus,
)
from app.services.scripts import active_script

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
    targets = db.scalars(select(CampaignTarget).join(Policy, Policy.id == CampaignTarget.policy_id)
                         .where(CampaignTarget.campaign_id == c.id,
                                CampaignTarget.status.in_([TargetStatus.pending, TargetStatus.failed]))
                         .order_by(CampaignTarget.status.desc(), Policy.renewal_date, Policy.id)  # pending first
                         .limit(LAUNCH_CALLS)).all()
    gen = _Gen(db, rng_seed=c.id * 1000 + c.dialled)
    now = utcnow()
    day = f"CALL-{now.astimezone(IST):%Y%m%d}-"
    seq = db.scalar(select(func.count()).select_from(Call).where(Call.call_ref.like(f"{day}%"))) or 0
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
    c.status = CampaignStatus.running
    remaining = db.scalar(select(func.count()).select_from(CampaignTarget).where(
        CampaignTarget.campaign_id == c.id, CampaignTarget.status == TargetStatus.pending))
    if not remaining:
        c.status = CampaignStatus.completed
    db.commit()
    return call_ids
