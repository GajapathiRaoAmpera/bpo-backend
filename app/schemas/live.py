from datetime import datetime

from pydantic import BaseModel

from app.models.enums import CallDirection, CallMode, Sentiment, Speaker


class LiveCallRow(BaseModel):
    call_id: int
    call_ref: str
    mode: CallMode
    direction: CallDirection
    started_at: datetime
    duration_sec: int
    customer_name: str | None
    customer_phone: str | None  # masked for masked roles
    policy_no: str | None
    agent_name: str | None
    verified: bool
    bot_connected: bool
    taken_over: bool
    sentiment: Sentiment | None  # set by post-call analysis; usually null while live
    last_speaker: Speaker | None
    last_text: str | None
