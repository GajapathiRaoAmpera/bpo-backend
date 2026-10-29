from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, utcnow
from app.models.enums import (
    ApprovalStatus,
    AuditAction,
    CallDirection,
    CallMode,
    CallOutcome,
    CallStatus,
    CampaignStatus,
    EscalationStatus,
    FollowupKind,
    FollowupStatus,
    IntegrationStatus,
    Language,
    PolicyStatus,
    Priority,
    Product,
    Role,
    Sentiment,
    Speaker,
    TargetStatus,
)

FK_USER = "users.id"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    job_title: Mapped[str] = mapped_column(String(120))
    role: Mapped[Role]
    password_hash: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(default=True)
    last_login_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    customer_code: Mapped[str] = mapped_column(String(20), unique=True)
    full_name: Mapped[str] = mapped_column(String(120), index=True)
    phone: Mapped[str] = mapped_column(String(15), index=True)
    email: Mapped[str] = mapped_column(String(200))
    dob: Mapped[date]
    city: Mapped[str] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(80))
    preferred_language: Mapped[Language]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    policies: Mapped[list["Policy"]] = relationship(back_populates="customer", order_by="Policy.renewal_date")


class Policy(Base):
    __tablename__ = "policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    policy_no: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    product: Mapped[Product]
    plan_name: Mapped[str] = mapped_column(String(120))
    sum_insured_inr: Mapped[int]
    premium_inr: Mapped[int]
    status: Mapped[PolicyStatus]
    start_date: Mapped[date]
    renewal_date: Mapped[date] = mapped_column(index=True)
    vehicle_reg: Mapped[str | None] = mapped_column(String(20))
    customer: Mapped[Customer] = relationship(back_populates="policies")


class Script(Base):
    __tablename__ = "scripts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    mode: Mapped[CallMode]
    version: Mapped[int]
    system_prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[ApprovalStatus] = mapped_column(default=ApprovalStatus.draft, index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("scripts.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey(FK_USER))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    submitted_at: Mapped[datetime | None]
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey(FK_USER))
    reviewed_at: Mapped[datetime | None]
    review_note: Mapped[str | None] = mapped_column(Text)
    checklist: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    product: Mapped[Product]
    segment: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    script_id: Mapped[int] = mapped_column(ForeignKey("scripts.id"))
    status: Mapped[CampaignStatus] = mapped_column(default=CampaignStatus.draft, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey(FK_USER))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    submitted_at: Mapped[datetime | None]
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey(FK_USER))
    reviewed_at: Mapped[datetime | None]
    review_note: Mapped[str | None] = mapped_column(Text)
    checklist: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    scheduled_at: Mapped[datetime | None]
    total_targets: Mapped[int] = mapped_column(default=0)
    dialled: Mapped[int] = mapped_column(default=0)
    connected: Mapped[int] = mapped_column(default=0)
    converted: Mapped[int] = mapped_column(default=0)


class CampaignTarget(Base):
    __tablename__ = "campaign_targets"
    __table_args__ = (UniqueConstraint("campaign_id", "policy_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("policies.id"))
    status: Mapped[TargetStatus] = mapped_column(default=TargetStatus.pending)
    call_id: Mapped[int | None] = mapped_column(ForeignKey("calls.id"))


class Call(Base):
    __tablename__ = "calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    call_ref: Mapped[str] = mapped_column(String(32), unique=True)
    direction: Mapped[CallDirection]
    mode: Mapped[CallMode]
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), index=True)
    policy_id: Mapped[int | None] = mapped_column(ForeignKey("policies.id"), index=True)
    agent_user_id: Mapped[int | None] = mapped_column(ForeignKey(FK_USER), index=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"))
    script_id: Mapped[int | None] = mapped_column(ForeignKey("scripts.id"))
    status: Mapped[CallStatus] = mapped_column(index=True)
    started_at: Mapped[datetime] = mapped_column(index=True)
    ended_at: Mapped[datetime | None]
    duration_sec: Mapped[int] = mapped_column(default=0)
    outcome: Mapped[CallOutcome | None]
    sentiment: Mapped[Sentiment | None]
    summary: Mapped[str | None] = mapped_column(Text)
    next_action: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(default=False)
    taken_over_at: Mapped[datetime | None]
    is_simulated: Mapped[bool] = mapped_column(default=False)
    turns: Mapped[list["TranscriptTurn"]] = relationship(order_by="TranscriptTurn.seq")


class TranscriptTurn(Base):
    __tablename__ = "transcript_turns"
    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id"), index=True)
    seq: Mapped[int]
    speaker: Mapped[Speaker]
    text: Mapped[str] = mapped_column(Text)
    offset_ms: Mapped[int] = mapped_column(default=0)
    tool_name: Mapped[str | None] = mapped_column(String(60))
    tool_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class QAScore(Base):
    __tablename__ = "qa_scores"
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id"), primary_key=True)
    ai_disclosure: Mapped[bool]
    identity_verified: Mapped[bool]
    correct_figures: Mapped[bool]
    no_mis_selling: Mapped[bool]
    polite_close: Mapped[bool]
    score: Mapped[int]
    source: Mapped[str] = mapped_column(String(10), default="ai")  # ai | human
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey(FK_USER))
    reviewer_note: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Escalation(Base):
    __tablename__ = "escalations"
    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int | None] = mapped_column(ForeignKey("calls.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    policy_id: Mapped[int | None] = mapped_column(ForeignKey("policies.id"))
    reason: Mapped[str] = mapped_column(Text)
    priority: Mapped[Priority]
    status: Mapped[EscalationStatus] = mapped_column(default=EscalationStatus.open, index=True)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey(FK_USER))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    resolved_at: Mapped[datetime | None]
    resolution_note: Mapped[str | None] = mapped_column(Text)


class Followup(Base):
    __tablename__ = "followups"
    id: Mapped[int] = mapped_column(primary_key=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("policies.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    call_id: Mapped[int | None] = mapped_column(ForeignKey("calls.id"))
    kind: Mapped[FollowupKind]
    due_at: Mapped[datetime] = mapped_column(index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[FollowupStatus] = mapped_column(default=FollowupStatus.scheduled, index=True)
    message_text: Mapped[str | None] = mapped_column(Text)
    payment_link_url: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[int | None] = mapped_column(ForeignKey(FK_USER))  # null = created by AI
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey(FK_USER))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    sent_at: Mapped[datetime | None]


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey(FK_USER), index=True)
    actor_role: Mapped[Role | None]
    action: Mapped[AuditAction] = mapped_column(index=True)
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(60))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ip: Mapped[str | None] = mapped_column(String(60))
    user_agent: Mapped[str | None] = mapped_column(String(300))


class Integration(Base):
    __tablename__ = "integrations"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(60))
    status: Mapped[IntegrationStatus]
    description: Mapped[str] = mapped_column(Text)
    last_sync_at: Mapped[datetime | None]
