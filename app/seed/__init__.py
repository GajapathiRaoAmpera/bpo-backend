"""Deterministic synthetic data. Dates are relative to "now" so the sandbox always looks current;
everything else is fixed by the RNG seed."""

import random
from datetime import UTC, date, datetime, time, timedelta

from faker import Faker
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.formatting import IST, format_date, format_inr
from app.core.security import hash_password
from app.models import (
    AuditLog, Call, Campaign, CampaignTarget, Customer, Escalation, Followup, Integration,
    Policy, QAScore, Script, TranscriptTurn, User,
)
from app.models.enums import (
    ApprovalStatus, AuditAction, CallDirection, CallMode, CallOutcome, CallStatus, CampaignStatus,
    EscalationStatus, FollowupKind, FollowupStatus, IntegrationStatus, Language, PolicyStatus,
    Priority, Product, Role, Sentiment, Speaker, TargetStatus,
)
from app.seed import prompts

PERSONAS = [
    ("Ravi Shankar", Role.super_admin, "IT Administrator"),
    ("Anitha Raman", Role.ops_manager, "Head of Operations"),
    ("Karthik Subramanian", Role.team_lead, "Floor Supervisor"),
    ("Divya Krishnan", Role.human_agent, "Senior Service Executive"),
    ("Suresh Babu", Role.campaign_manager, "Renewals Manager"),
    ("Meera Iyer", Role.bot_designer, "Conversation Designer"),
    ("Prakash Natarajan", Role.qa_analyst, "Quality Analyst"),
    ("Lakshmi Venkatesh", Role.compliance_officer, "Compliance Officer"),
]

# (city, state, vehicle RTO prefix, weight)
CITIES = [
    ("Chennai", "Tamil Nadu", "TN 09", 18), ("Coimbatore", "Tamil Nadu", "TN 38", 9),
    ("Madurai", "Tamil Nadu", "TN 59", 7), ("Tiruchirappalli", "Tamil Nadu", "TN 45", 5),
    ("Salem", "Tamil Nadu", "TN 30", 4), ("Tirunelveli", "Tamil Nadu", "TN 72", 3),
    ("Erode", "Tamil Nadu", "TN 33", 3), ("Vellore", "Tamil Nadu", "TN 23", 3),
    ("Thoothukudi", "Tamil Nadu", "TN 69", 2), ("Bengaluru", "Karnataka", "KA 05", 8),
    ("Hyderabad", "Telangana", "TS 09", 6), ("Mumbai", "Maharashtra", "MH 02", 6),
    ("Pune", "Maharashtra", "MH 12", 4), ("New Delhi", "Delhi", "DL 3C", 5),
    ("Kochi", "Kerala", "KL 07", 3), ("Ahmedabad", "Gujarat", "GJ 01", 3),
    ("Kolkata", "West Bengal", "WB 06", 3), ("Jaipur", "Rajasthan", "RJ 14", 2),
    ("Lucknow", "Uttar Pradesh", "UP 32", 2),
]
NORTH = {"Delhi", "Uttar Pradesh", "Rajasthan", "Gujarat", "Maharashtra", "West Bengal"}

PLANS = {
    Product.motor: ["Private Car Comprehensive", "Two-Wheeler Comprehensive", "Private Car Third-Party"],
    Product.health: ["Family Floater Plus", "Senior Citizen Care", "Individual Health Shield"],
    Product.term_life: ["Term Shield", "Smart Term Plus", "Term Shield Return of Premium"],
}
PREFIX = {Product.motor: "MOT", Product.health: "HLT", Product.term_life: "TRM"}
VEHICLES = ["Maruti Suzuki Swift", "Hyundai Creta", "Tata Nexon", "Honda City", "Mahindra XUV700",
            "Toyota Innova Crysta", "Kia Seltos", "Maruti Suzuki Baleno"]
TWO_WHEELERS = ["Honda Activa 6G", "TVS Jupiter", "Royal Enfield Classic 350", "Bajaj Pulsar NS200",
                "TVS Apache RTR 160"]
PLATE_LETTERS = "ABCDEFGHJKLMNPRSTUVWXYZ"
TN_FIRST = ["Arun", "Karthik", "Senthil", "Murugan", "Saravanan", "Vignesh", "Prabhu", "Balaji", "Dinesh",
            "Ganesh", "Ramesh", "Selvam", "Manikandan", "Aravind", "Sathish", "Priya", "Kavitha", "Lakshmi",
            "Meenakshi", "Deepa", "Revathi", "Anitha", "Gayathri", "Saranya", "Divya", "Bhuvana", "Janani",
            "Nithya", "Sangeetha", "Malathi", "Kalaivani", "Thenmozhi", "Ilango", "Kumaresan", "Anbu"]
TN_LAST = ["Subramanian", "Ramasamy", "Krishnan", "Natarajan", "Venkatesan", "Sundaram", "Rajendran",
           "Palaniswamy", "Shanmugam", "Annamalai", "Chidambaram", "Govindan", "Murugesan", "Pandian",
           "Raman", "Srinivasan", "Thangavel", "Velu", "Arumugam", "Kannan"]

ESCALATION_REASONS = [
    "Disputes late fee of {fee} charged despite paying within the grace period",
    "Requests no-claim bonus correction after a claim-free year",
    "Wants to change nominee before renewal",
    "Cashless claim status query outside bot scope",
    "Asked to speak to a person after two verification attempts",
    "Bank auto-debit failed twice; wants payment reconciled",
    "Says premium increased without explanation; wants a breakdown from an officer",
    "Requests address change and new policy document",
]


def _ist(d: date, hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm, ss), IST).astimezone(UTC)


class _Gen:
    def __init__(self, db: Session, rng_seed: int):
        self.db = db
        self.rng = random.Random(rng_seed)
        self.fake = Faker("en_IN")
        self.fake.seed_instance(rng_seed)
        self.now = utcnow()
        self.today = self.now.astimezone(IST).date()
        self.users: dict[Role, User] = {}

    def ago(self, days: float) -> datetime:
        return self.now - timedelta(days=days)

    # --- users -------------------------------------------------------------
    def users_(self, password: str) -> None:
        pw = hash_password(password)
        for name, role, title in PERSONAS:
            first, last = name.lower().split(" ", 1)
            u = User(email=f"{first}.{last.replace(' ', '')}@sandbox.assurevoice.in", full_name=name,
                     job_title=title, role=role, password_hash=pw, created_at=self.ago(120))
            self.db.add(u)
            self.users[role] = u
        self.db.flush()

    # --- customers & policies ---------------------------------------------
    def customers_(self, n: int) -> list[Customer]:
        rng, fake = self.rng, self.fake
        weights = [c[3] for c in CITIES]
        counters = {p: rng.randrange(1000, 2000) for p in Product}
        out = []
        for i in range(n):
            city, state, rto, _ = rng.choices(CITIES, weights)[0]
            if state == "Tamil Nadu":
                lang = rng.choices([Language.tamil, Language.english, Language.hindi], [55, 40, 5])[0]
            elif state in NORTH:
                lang = rng.choices([Language.hindi, Language.english], [60, 40])[0]
            else:
                lang = rng.choices([Language.english, Language.hindi], [85, 15])[0]
            if state == "Tamil Nadu" and rng.random() < 0.8:
                first, last = rng.choice(TN_FIRST), rng.choice(TN_LAST)
            else:
                first, last = fake.first_name(), fake.last_name()
            c = Customer(
                customer_code=f"CUS-{100001 + i}", full_name=f"{first} {last}",
                phone=f"{rng.choice('6789')}{rng.randrange(10**9):09d}",
                email=f"{first.lower()}.{last.lower()}{rng.randrange(10, 99)}@example.com",
                dob=self.today - timedelta(days=rng.randrange(23 * 365, 70 * 365)),
                city=city, state=state, preferred_language=lang, created_at=self.ago(rng.randrange(200, 2000)),
            )
            for product in rng.sample(list(Product), rng.choices([1, 2, 3], [45, 40, 15])[0]):
                counters[product] += rng.randrange(1, 40)
                c.policies.append(self._policy(product, counters[product], rto))
            self.db.add(c)
            out.append(c)
        self.db.flush()
        return out

    def _policy(self, product: Product, seq: int, rto: str) -> Policy:
        rng = self.rng
        plan = rng.choice(PLANS[product])
        vehicle_reg = None
        if product == Product.motor:
            two_wheeler = plan.startswith("Two")
            si = rng.randrange(40, 160) * 1000 if two_wheeler else rng.randrange(300, 1400) * 1000
            premium = int(si * rng.uniform(0.018, 0.035)) if plan.endswith("Comprehensive") else rng.randrange(2100, 7900)
            plan = f"{plan} · {rng.choice(TWO_WHEELERS if two_wheeler else VEHICLES)}"
            vehicle_reg = f"{rto} {rng.choice(PLATE_LETTERS)}{rng.choice(PLATE_LETTERS)} {rng.randrange(1000, 9999)}"
        elif product == Product.health:
            si = rng.choice([3, 5, 10, 15, 25]) * 1_00_000
            premium = int(si * rng.uniform(0.025, 0.06))
        else:
            si = rng.choice([25, 50, 75, 100, 150, 200]) * 1_00_000
            premium = int(si * rng.uniform(0.0011, 0.0022))
        premium = round(premium, -1)

        roll = rng.random()
        if roll < 0.08:  # renewed during the last 30 days
            renewal = self.today + timedelta(days=rng.randrange(330, 365))
            status = PolicyStatus.renewed
        elif roll < 0.12:  # lapsed: grace period missed
            renewal = self.today - timedelta(days=rng.randrange(31, 60))
            status = PolicyStatus.lapsed
        else:
            renewal = self.today + timedelta(days=rng.randrange(0, 61))
            status = PolicyStatus.due if (renewal - self.today).days <= 15 else PolicyStatus.active
        return Policy(policy_no=f"{PREFIX[product]}-2026-{seq:06d}", product=product, plan_name=plan,
                      sum_insured_inr=si, premium_inr=premium, status=status,
                      start_date=renewal - timedelta(days=365), renewal_date=renewal, vehicle_reg=vehicle_reg)

    # --- scripts & campaigns ----------------------------------------------
    def scripts_(self) -> dict[str, Script]:
        meera, anitha, lakshmi = (self.users[r] for r in (Role.bot_designer, Role.ops_manager, Role.compliance_officer))
        ok = {"ai_disclosure": True, "verification_before_details": True, "figures_from_tools_only": True,
              "no_mis_selling": True, "escalation_path": True}
        inbound = Script(name="Inbound policy service", mode=CallMode.inbound, version=1,
                         system_prompt=prompts.INBOUND_V1, status=ApprovalStatus.approved, created_by=meera.id,
                         created_at=self.ago(45), submitted_at=self.ago(44), reviewed_by=lakshmi.id,
                         reviewed_at=self.ago(43), checklist=ok,
                         review_note="Disclosure and verification steps meet the call-recording requirements.")
        renewal = Script(name="Renewal reminder", mode=CallMode.renewal, version=1,
                         system_prompt=prompts.RENEWAL_V1, status=ApprovalStatus.approved, created_by=meera.id,
                         created_at=self.ago(40), submitted_at=self.ago(39), reviewed_by=anitha.id,
                         reviewed_at=self.ago(38), review_note="Approved for the October renewal wave.", checklist=ok)
        self.db.add_all([inbound, renewal])
        self.db.flush()
        # Pending v2: shows maker-checker; the bot keeps using v1 until it is approved.
        self.db.add(Script(name="Renewal reminder", mode=CallMode.renewal, version=2, parent_id=renewal.id,
                           system_prompt=prompts.RENEWAL_V2, status=ApprovalStatus.pending, created_by=meera.id,
                           created_at=self.ago(1), submitted_at=self.now - timedelta(hours=5)))
        self.db.flush()
        return {"inbound": inbound, "renewal": renewal}

    def campaigns_(self, renewal: Script, policies: list[Policy]) -> list[Campaign]:
        suresh, anitha, lakshmi = (self.users[r] for r in (Role.campaign_manager, Role.ops_manager, Role.compliance_officer))
        ok = {"calling_window": True, "dnd_scrubbed": True, "approved_script": True, "segment_reviewed": True}
        specs = [
            ("October Motor Renewal Wave", Product.motor, CampaignStatus.completed, 28, anitha),
            ("Health Renewal Reminders · November", Product.health, CampaignStatus.running, 12, lakshmi),
            ("Term Life Premium Due · 30 Days", Product.term_life, CampaignStatus.approved, 3, anitha),
            ("Two-Wheeler Renewal Recovery", Product.motor, CampaignStatus.pending, 0, None),
            ("Senior Citizen Health Outreach", Product.health, CampaignStatus.draft, 0, None),
        ]
        out = []
        for name, product, status, age, reviewer in specs:
            c = Campaign(name=name, product=product, script_id=renewal.id, status=status, created_by=suresh.id,
                         segment={"product": product.value, "renewal_within_days": 30,
                                  "languages": ["english", "tamil", "hindi"]},
                         created_at=self.ago(age + 2),
                         submitted_at=self.ago(age + 1) if status != CampaignStatus.draft else None,
                         reviewed_by=reviewer.id if reviewer else None,
                         reviewed_at=self.ago(age) if reviewer else None,
                         review_note="Segment and calling window verified." if reviewer else None,
                         checklist=ok if reviewer else None, scheduled_at=self.ago(age) if reviewer else None)
            self.db.add(c)
            out.append(c)
        self.db.flush()
        for c in out:
            pool = [p for p in policies if p.product == c.product and p.status != PolicyStatus.lapsed]
            targets = self.rng.sample(pool, min(len(pool), self.rng.randrange(60, 140)))
            self.db.add_all(CampaignTarget(campaign_id=c.id, policy_id=p.id) for p in targets)
            c.total_targets = len(targets)
        self.db.flush()
        return out

    # --- calls ---------------------------------------------------------------
    def calls_(self, n: int, days: int, customers: list[Customer], scripts: dict[str, Script],
               campaigns: list[Campaign]) -> None:
        rng = self.rng
        divya, karthik = self.users[Role.human_agent], self.users[Role.team_lead]
        live = {c.product: c for c in campaigns if c.status in (CampaignStatus.completed, CampaignStatus.running)}
        targets: dict[tuple[int, int], CampaignTarget] = {
            (t.campaign_id, t.policy_id): t
            for t in self.db.query(CampaignTarget).filter(CampaignTarget.campaign_id.in_([c.id for c in live.values()]))
        }
        day_list = [self.today - timedelta(days=d) for d in range(days - 1, -1, -1)]
        day_weights = [0.45 if d.weekday() == 6 else 0.8 if d.weekday() == 5 else 1.0 for d in day_list]
        by_campaign: dict[int, list[CampaignTarget]] = {}
        for t in targets.values():
            by_campaign.setdefault(t.campaign_id, []).append(t)
        policy_owner = {p.id: (c, p) for c in customers for p in c.policies}
        drawn = sorted(rng.choices(day_list, day_weights, k=n))
        seq_per_day: dict[date, int] = {}
        for d in drawn:
            seq_per_day[d] = seq_per_day.get(d, 0) + 1
            started = _ist(d, rng.randrange(9, 19), rng.randrange(60), rng.randrange(60))  # TRAI window
            if started > self.now:  # today's calls only up to now
                started = self.now - timedelta(minutes=rng.randrange(5, 240))
            mode = CallMode.renewal if rng.random() < 0.65 else CallMode.inbound
            outcome = self._outcome(mode)
            campaign = rng.choice(list(live.values())) if mode == CallMode.renewal and rng.random() < 0.6 else None
            if campaign and campaign.status == CampaignStatus.running and d < self.today - timedelta(days=12):
                campaign = None  # the running campaign started 12 days ago
            if campaign:
                cust, policy = policy_owner[rng.choice(by_campaign[campaign.id]).policy_id]
            else:
                cust = rng.choice(customers)
                policy = rng.choice(cust.policies)
            escalated = outcome == CallOutcome.escalated
            duration = rng.randrange(8, 35) if outcome == CallOutcome.no_answer else rng.randrange(95, 480)
            agent = (divya if rng.random() < 0.7 else karthik) if escalated or rng.random() < 0.06 else None
            call = Call(
                call_ref=f"CALL-{d:%Y%m%d}-{seq_per_day[d]:04d}",
                direction=CallDirection.outbound if mode == CallMode.renewal else CallDirection.inbound,
                mode=mode, customer_id=cust.id, policy_id=policy.id, agent_user_id=agent.id if agent else None,
                campaign_id=campaign.id if campaign else None, script_id=scripts[mode.value].id,
                status=CallStatus.escalated if escalated else CallStatus.completed,
                started_at=started, ended_at=started + timedelta(seconds=duration), duration_sec=duration,
                outcome=outcome, sentiment=self._sentiment(outcome),
                verified=outcome != CallOutcome.no_answer and rng.random() > 0.04,
                taken_over_at=started + timedelta(seconds=duration // 2) if escalated else None,
                is_simulated=True,
            )
            call.summary, call.next_action = self._summary(call, cust, policy)
            self.db.add(call)
            self.db.flush()
            if outcome != CallOutcome.no_answer:
                self._transcript(call, cust, policy)
                self._qa(call)
            self._side_effects(call, cust, policy, started, agent)
            t = targets.get((campaign.id, policy.id)) if campaign else None
            if t and t.status in (TargetStatus.pending, TargetStatus.failed):
                # progress counts targets, not attempts: a no-answer can be retried
                converted = outcome in (CallOutcome.renewed, CallOutcome.payment_link_sent)
                campaign.dialled += t.status == TargetStatus.pending
                if outcome == CallOutcome.no_answer:
                    t.status = TargetStatus.failed
                else:
                    campaign.connected += 1
                    campaign.converted += converted
                    t.status = TargetStatus.converted if converted else TargetStatus.called
                t.call_id = call.id
        self.db.flush()

    def _outcome(self, mode: CallMode) -> CallOutcome:
        o = CallOutcome
        if mode == CallMode.renewal:
            return self.rng.choices(
                [o.payment_link_sent, o.renewed, o.callback_scheduled, o.escalated, o.not_interested, o.no_answer],
                [30, 12, 22, 8, 13, 15])[0]
        return self.rng.choices(
            [o.info_provided, o.payment_link_sent, o.callback_scheduled, o.escalated, o.renewed],
            [45, 17, 12, 14, 12])[0]

    def _sentiment(self, outcome: CallOutcome) -> Sentiment:
        o = CallOutcome
        weights = {o.renewed: [80, 18, 2], o.payment_link_sent: [65, 30, 5], o.info_provided: [45, 48, 7],
                   o.callback_scheduled: [30, 60, 10], o.not_interested: [5, 55, 40], o.escalated: [3, 27, 70],
                   o.no_answer: [0, 100, 0]}[outcome]
        return self.rng.choices(list(Sentiment), weights)[0]

    @staticmethod
    def _summary(call: Call, c: Customer, p: Policy) -> tuple[str, str]:
        o = CallOutcome
        due = f"{format_inr(p.premium_inr)} due on {format_date(p.renewal_date)}"
        first = c.full_name.split()[0]
        return {
            o.payment_link_sent: (f"{first} was verified and agreed to renew {p.policy_no} ({p.plan_name}). "
                                  f"The premium of {due} was quoted from policy records and a payment link was "
                                  f"sent on WhatsApp.", "Check payment status in 24 hours; remind if unpaid."),
            o.renewed: (f"{first} renewed {p.policy_no} during the call using the payment link. "
                        f"Premium of {format_inr(p.premium_inr)} received.", "None. Renewal confirmation sent."),
            o.callback_scheduled: (f"{first} asked for a callback to discuss {p.policy_no} with family before paying "
                                   f"the premium of {due}.", "Call back at the time the customer chose."),
            o.escalated: (f"{first} raised an issue on {p.policy_no} that needed a person. "
                          f"The AI assistant escalated with full context.", "Human agent to resolve and update the customer."),
            o.not_interested: (f"{first} does not plan to renew {p.policy_no} and cited a lower quote elsewhere. "
                               f"No pressure was applied.", "Mark as retention risk; no further calls this cycle."),
            o.info_provided: (f"{first} asked about the renewal date and cover of {p.policy_no}. "
                              f"Details were shared after verification: {due}.", "No action needed."),
            o.no_answer: (f"No answer from {first} for {p.policy_no}.", "Retry in the next calling window."),
        }[call.outcome]

    def _transcript(self, call: Call, c: Customer, p: Policy) -> None:
        first = c.full_name.split()[0]
        lines: list[tuple[Speaker, str, str | None, dict | None]] = []

        def add(sp: Speaker, text: str, tool: str | None = None, payload: dict | None = None) -> None:
            lines.append((sp, text, tool, payload))

        if call.mode == CallMode.renewal:
            add(Speaker.bot, f"Hello, this is Vani, an automated AI assistant from AssureVoice Insurance. "
                             f"This call is recorded for quality and compliance. Am I speaking with {first}?")
            add(Speaker.customer, "Yes, speaking.")
            add(Speaker.bot, "I'm calling about an upcoming renewal. To protect your details, could you share "
                             "your policy number and date of birth?")
        else:
            add(Speaker.bot, "Welcome to AssureVoice Insurance. I'm Vani, an automated AI assistant, and this call "
                             "is recorded for quality and compliance. How can I help you today?")
            add(Speaker.customer, "I want to know when my policy renewal is due and how much I have to pay.")
            add(Speaker.bot, "I can help with that. Please share your policy number and date of birth.")
        add(Speaker.customer, f"It's {p.policy_no}, date of birth {format_date(c.dob)}.")
        add(Speaker.tool, "Customer verified", "verify_customer", {"policy_no": p.policy_no, "verified": True})
        add(Speaker.tool, "Policy details fetched", "get_policy_details",
            {"policy_no": p.policy_no, "premium_inr": p.premium_inr, "renewal_date": p.renewal_date.isoformat()})
        add(Speaker.bot, f"Thank you, {first}. Your {p.plan_name} policy is due for renewal on "
                         f"{format_date(p.renewal_date)} and the premium is {format_inr(p.premium_inr)}.")
        o = CallOutcome
        if call.outcome in (o.payment_link_sent, o.renewed):
            add(Speaker.customer, "Okay, please send me the link.")
            add(Speaker.tool, "Payment link sent on WhatsApp", "send_payment_link", {"policy_no": p.policy_no})
            add(Speaker.bot, "I've sent a secure payment link to your registered WhatsApp number.")
        elif call.outcome == o.callback_scheduled:
            add(Speaker.customer, "I need to check with my family. Can you call me tomorrow evening?")
            add(Speaker.tool, "Follow-up scheduled", "schedule_followup",
                {"policy_no": p.policy_no, "when": "tomorrow 18:00 IST", "reason": "Customer requested callback"})
            add(Speaker.bot, "Of course. I've scheduled a callback for tomorrow at 6 PM.")
        elif call.outcome == o.escalated:
            add(Speaker.customer, "Last time I was charged a late fee even though I paid on time. I want to talk to someone.")
            add(Speaker.tool, "Escalated to human agent", "escalate_to_human",
                {"policy_no": p.policy_no, "reason": "Disputed late fee"})
            add(Speaker.bot, "I understand. I'm connecting you to a service executive now with the details of this call.")
            add(Speaker.agent, f"Hello {first}, this is the AssureVoice service desk. I can see your concern and will look into it.")
        elif call.outcome == o.not_interested:
            add(Speaker.customer, "I've got a cheaper quote elsewhere, so I won't be renewing.")
            add(Speaker.bot, "Thank you for letting me know. If you change your mind, you can call us anytime.")
        else:
            add(Speaker.customer, "That's all I needed, thanks.")
        add(Speaker.bot, "Thank you for your time. Have a good day.")
        step = max(call.duration_sec * 1000 // len(lines), 1000)
        self.db.add_all(TranscriptTurn(call_id=call.id, seq=i + 1, speaker=sp, text=text, offset_ms=i * step,
                                       tool_name=tool, tool_payload=payload)
                        for i, (sp, text, tool, payload) in enumerate(lines))

    def _qa(self, call: Call) -> None:
        rng = self.rng
        checks = {"ai_disclosure": rng.random() > 0.02, "identity_verified": call.verified,
                  "correct_figures": rng.random() > 0.05, "no_mis_selling": rng.random() > 0.03,
                  "polite_close": rng.random() > 0.08}
        reviewed = rng.random() < 0.25
        self.db.add(QAScore(call_id=call.id, **checks, score=sum(checks.values()),
                            source="human" if reviewed else "ai",
                            reviewer_id=self.users[Role.qa_analyst].id if reviewed else None,
                            reviewer_note="Checked against the recording; matches AI score." if reviewed else None,
                            locked_at=call.ended_at + timedelta(days=1) if reviewed else None,
                            updated_at=call.ended_at))

    def _side_effects(self, call: Call, c: Customer, p: Policy, started: datetime, agent: User | None) -> None:
        rng, o = self.rng, CallOutcome
        first = c.full_name.split()[0]
        if call.outcome in (o.payment_link_sent, o.renewed):
            paid = call.outcome == o.renewed or rng.random() < 0.45
            self.db.add(Followup(
                policy_id=p.id, customer_id=c.id, call_id=call.id, kind=FollowupKind.payment_link,
                due_at=started + timedelta(minutes=2), reason="Payment link requested on call",
                status=FollowupStatus.done if paid else FollowupStatus.sent,
                message_text=f"Hi {first}, here is your secure link to renew {p.policy_no} "
                             f"({format_inr(p.premium_inr)}, due {format_date(p.renewal_date)}). – AssureVoice Insurance",
                payment_link_url=f"https://pay.sandbox.assurevoice.in/r/{p.policy_no.lower()}-{call.id}",
                sent_at=started + timedelta(minutes=2), created_at=started))
        elif call.outcome == o.callback_scheduled:
            due = started + timedelta(days=1, hours=rng.randrange(-2, 4))
            status = FollowupStatus.scheduled if due > self.now else rng.choice(
                [FollowupStatus.done, FollowupStatus.done, FollowupStatus.scheduled])
            self.db.add(Followup(
                policy_id=p.id, customer_id=c.id, call_id=call.id, kind=FollowupKind.callback, due_at=due,
                reason="Customer asked for a callback", created_at=started, status=status,
                assigned_to=self.users[Role.human_agent].id if rng.random() < 0.4 else None))
        elif call.outcome == o.escalated:
            recent = started > self.ago(3)
            status = rng.choice([EscalationStatus.open, EscalationStatus.assigned]) if recent else EscalationStatus.resolved
            resolved = status == EscalationStatus.resolved
            self.db.add(Escalation(
                call_id=call.id, customer_id=c.id, policy_id=p.id,
                reason=rng.choice(ESCALATION_REASONS).format(fee=format_inr(rng.choice([500, 750, 1200]))),
                priority=rng.choices(list(Priority), [10, 35, 35, 20])[0], status=status,
                assigned_to=agent.id if agent and status != EscalationStatus.open else None,
                created_at=started + timedelta(seconds=call.duration_sec // 2),
                resolved_at=started + timedelta(hours=rng.randrange(1, 20)) if resolved else None,
                resolution_note="Explained the charges and raised a reversal request." if resolved else None))

    # --- audit & integrations -----------------------------------------------
    def audit_(self, days: int, customers: list[Customer]) -> None:
        rng = self.rng
        for d in range(days, 0, -1):
            day = self.today - timedelta(days=d)
            if day.weekday() == 6:
                continue
            for role, u in self.users.items():
                if rng.random() < 0.85:
                    self.db.add(AuditLog(ts=_ist(day, rng.randrange(8, 11), rng.randrange(60)), actor_user_id=u.id,
                                         actor_role=role, action=AuditAction.login, entity_type="user",
                                         entity_id=str(u.id), ip=f"10.20.{rng.randrange(1, 9)}.{rng.randrange(2, 250)}",
                                         user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/141.0"))
            for _ in range(rng.randrange(3, 9)):
                role = rng.choice([Role.human_agent, Role.team_lead, Role.ops_manager, Role.qa_analyst])
                c = rng.choice(customers)
                self.db.add(AuditLog(ts=_ist(day, rng.randrange(9, 19), rng.randrange(60)),
                                     actor_user_id=self.users[role].id, actor_role=role,
                                     action=AuditAction.view_customer, entity_type="customer", entity_id=str(c.id),
                                     detail={"customer_code": c.customer_code,
                                             "masked": role in (Role.ops_manager, Role.qa_analyst)}))
        for model, kind in ((Script, "script"), (Campaign, "campaign")):
            for row in self.db.query(model).filter(model.reviewed_at.isnot(None)):
                reviewer = self.db.get(User, row.reviewed_by)
                self.db.add(AuditLog(ts=row.reviewed_at, actor_user_id=reviewer.id, actor_role=reviewer.role,
                                     action=AuditAction.approve, entity_type=kind, entity_id=str(row.id),
                                     detail={"name": row.name}))
        lakshmi = self.users[Role.compliance_officer]
        self.db.add(AuditLog(ts=self.ago(6), actor_user_id=lakshmi.id, actor_role=lakshmi.role,
                             action=AuditAction.export, entity_type="audit_log",
                             detail={"format": "csv", "rows": 412, "filters": {"action": "view_customer"}}))

    def integrations_(self) -> None:
        conn, pilot = IntegrationStatus.connected, IntegrationStatus.pilot
        for key, name, cat, status, desc in [
            ("gemini_live", "Google Gemini Live", "AI voice", conn, "Real-time speech model powering the voice agent."),
            ("gemini_flash", "Google Gemini Flash", "AI analytics", conn, "Post-call summaries, QA scoring and follow-up messages."),
            ("pipecat_webrtc", "Pipecat WebRTC transport", "Voice transport", conn, "Browser-to-bot audio for the agent console and test calls."),
            ("pas", "Policy Administration System", "Core insurance", pilot, "Sandbox uses the local policy database; production connects to PAS."),
            ("whatsapp", "WhatsApp Business API", "Messaging", pilot, "Messages are recorded as follow-ups; nothing is sent to real numbers."),
            ("payments", "Payment gateway (UPI / cards)", "Payments", pilot, "Generates sandbox payment links only."),
            ("sip", "SIP telephony trunk", "Telephony", pilot, "Production PSTN calling; the sandbox uses browser WebRTC."),
            ("crm", "CRM case sync", "CRM", pilot, "Escalations and follow-ups would sync as CRM cases."),
            ("bima_bharosa", "IRDAI Bima Bharosa", "Regulatory", pilot, "Grievance reference sync for complaints."),
        ]:
            self.db.add(Integration(key=key, name=name, category=cat, status=status, description=desc,
                                    last_sync_at=self.now - timedelta(minutes=7) if status == conn else None))


def seed(db: Session, password: str, *, customers: int = 500, calls: int = 1200, days: int = 30,
         rng_seed: int = 2026) -> None:
    g = _Gen(db, rng_seed)
    g.users_(password)
    custs = g.customers_(customers)
    scripts = g.scripts_()
    campaigns = g.campaigns_(scripts["renewal"], [p for c in custs for p in c.policies])
    g.calls_(calls, days, custs, scripts, campaigns)
    g.audit_(days, custs)
    g.integrations_()
    db.commit()
