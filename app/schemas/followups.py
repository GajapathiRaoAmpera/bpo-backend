from datetime import datetime

from pydantic import AwareDatetime, BaseModel, Field

from app.models.enums import FollowupKind, FollowupStatus


class FollowupRow(BaseModel):
    id: int
    kind: FollowupKind
    status: FollowupStatus
    due_at: datetime
    reason: str
    message_text: str | None
    payment_link_url: str | None
    sent_at: datetime | None
    created_at: datetime
    customer_id: int
    customer_name: str
    policy_id: int
    policy_no: str
    call_id: int | None
    created_by_name: str | None  # None = created by the AI
    assigned_to_name: str | None


class FollowupPage(BaseModel):
    items: list[FollowupRow]
    total: int
    page: int
    page_size: int


class FollowupIn(BaseModel):
    policy_id: int
    kind: FollowupKind = FollowupKind.callback
    due_at: AwareDatetime
    reason: str = Field(min_length=3, max_length=500)


class FollowupPatch(BaseModel):
    status: FollowupStatus | None = None  # done or cancelled
    due_at: AwareDatetime | None = None
    reason: str | None = Field(None, min_length=3, max_length=500)
