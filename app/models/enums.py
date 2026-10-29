from enum import StrEnum

# Member names equal values: SQLAlchemy persists enum names.


class Role(StrEnum):
    super_admin = "super_admin"
    ops_manager = "ops_manager"
    team_lead = "team_lead"
    human_agent = "human_agent"
    campaign_manager = "campaign_manager"
    bot_designer = "bot_designer"
    qa_analyst = "qa_analyst"
    compliance_officer = "compliance_officer"


class Language(StrEnum):
    english = "english"
    tamil = "tamil"
    hindi = "hindi"


class Product(StrEnum):
    motor = "motor"
    health = "health"
    term_life = "term_life"


class PolicyStatus(StrEnum):
    active = "active"
    due = "due"
    lapsed = "lapsed"
    renewed = "renewed"


class ApprovalStatus(StrEnum):
    draft = "draft"
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class CampaignStatus(StrEnum):
    draft = "draft"
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    running = "running"
    completed = "completed"


class TargetStatus(StrEnum):
    pending = "pending"
    called = "called"
    converted = "converted"
    failed = "failed"


class CallMode(StrEnum):
    inbound = "inbound"
    renewal = "renewal"


class CallDirection(StrEnum):
    inbound = "inbound"
    outbound = "outbound"


class CallStatus(StrEnum):
    in_progress = "in_progress"
    completed = "completed"
    escalated = "escalated"
    failed = "failed"


class CallOutcome(StrEnum):
    payment_link_sent = "payment_link_sent"
    callback_scheduled = "callback_scheduled"
    escalated = "escalated"
    renewed = "renewed"
    not_interested = "not_interested"
    info_provided = "info_provided"
    no_answer = "no_answer"


class Sentiment(StrEnum):
    positive = "positive"
    neutral = "neutral"
    negative = "negative"


class Speaker(StrEnum):
    bot = "bot"
    customer = "customer"
    agent = "agent"
    tool = "tool"


class Priority(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class EscalationStatus(StrEnum):
    open = "open"
    assigned = "assigned"
    resolved = "resolved"


class FollowupKind(StrEnum):
    payment_link = "payment_link"
    callback = "callback"
    reminder = "reminder"


class FollowupStatus(StrEnum):
    scheduled = "scheduled"
    sent = "sent"
    done = "done"
    cancelled = "cancelled"


class AuditAction(StrEnum):
    login = "login"
    login_failed = "login_failed"
    logout = "logout"
    view_customer = "view_customer"
    create = "create"
    update = "update"
    delete = "delete"
    submit = "submit"
    approve = "approve"
    reject = "reject"
    launch = "launch"
    export = "export"
    takeover = "takeover"
    call_start = "call_start"
    call_end = "call_end"


class IntegrationStatus(StrEnum):
    connected = "connected"
    pilot = "pilot"
