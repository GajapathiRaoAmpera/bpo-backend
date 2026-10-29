from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import CallMode, CallOutcome, CallStatus, Language, Product, Sentiment, Speaker
from app.schemas.customer import CustomerOut, PolicyOut


class QueueItem(BaseModel):
    policy_id: int
    policy_no: str
    product: Product
    plan_name: str
    premium_inr: int
    renewal_date: date
    customer_id: int
    customer_name: str
    city: str
    preferred_language: Language
    last_called_at: datetime | None


class SessionIn(BaseModel):
    mode: CallMode
    policy_id: int | None = None  # required for renewal


class TurnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    seq: int
    speaker: Speaker
    text: str
    offset_ms: int
    tool_name: str | None
    tool_payload: dict[str, Any] | None


class QAOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    ai_disclosure: bool
    identity_verified: bool
    correct_figures: bool
    no_mis_selling: bool
    polite_close: bool
    score: int
    source: str


class SessionOut(BaseModel):
    call_id: int
    call_ref: str
    mode: CallMode
    status: CallStatus
    started_at: datetime
    ended_at: datetime | None
    duration_sec: int
    verified: bool
    live: bool  # bot currently connected
    taken_over: bool
    analysis_pending: bool
    outcome: CallOutcome | None
    sentiment: Sentiment | None
    summary: str | None
    next_action: str | None
    customer: CustomerOut | None
    policy: PolicyOut | None
    turns: list[TurnOut]
    qa: QAOut | None


class OfferIn(BaseModel):
    sdp: str
    type: str
    pc_id: str | None = None
    restart_pc: bool | None = None
    request_data: Any | None = None


class IceCandidateIn(BaseModel):
    candidate: str
    sdp_mid: str
    sdp_mline_index: int


class IcePatchIn(BaseModel):
    pc_id: str
    candidates: list[IceCandidateIn]


class EndIn(BaseModel):
    outcome: CallOutcome | None = None  # set by a human agent after takeover
    note: str | None = None
