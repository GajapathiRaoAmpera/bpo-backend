from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import CallMode, CallOutcome
from app.schemas.voice import TurnOut


class QAChecksIn(BaseModel):
    ai_disclosure: bool
    identity_verified: bool
    correct_figures: bool
    no_mis_selling: bool
    polite_close: bool


class QAReviewIn(QAChecksIn):
    note: str | None = Field(None, max_length=2000)
    lock: bool = False


class QARow(BaseModel):
    call_id: int
    call_ref: str
    mode: CallMode
    started_at: datetime
    duration_sec: int
    customer_name: str | None
    outcome: CallOutcome | None
    score: int | None  # None = not scored automatically
    source: str | None
    ai_disclosure: bool | None
    identity_verified: bool | None
    correct_figures: bool | None
    no_mis_selling: bool | None
    polite_close: bool | None
    reviewer_name: str | None
    reviewer_note: str | None
    locked_at: datetime | None


class QAPage(BaseModel):
    items: list[QARow]
    total: int
    page: int
    page_size: int


class QADetail(QARow):
    summary: str | None
    turns: list[TurnOut]
