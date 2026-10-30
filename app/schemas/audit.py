from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models.enums import AuditAction, Role


class AuditRow(BaseModel):
    id: int
    ts: datetime
    actor_user_id: int | None
    actor_name: str | None
    actor_role: Role | None
    action: AuditAction
    entity_type: str | None
    entity_id: str | None
    detail: dict[str, Any] | None
    ip: str | None


class AuditPage(BaseModel):
    items: list[AuditRow]
    total: int
    page: int
    page_size: int
