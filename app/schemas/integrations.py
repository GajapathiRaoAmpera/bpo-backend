from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import IntegrationStatus


class IntegrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    key: str
    name: str
    category: str
    status: IntegrationStatus
    description: str
    last_sync_at: datetime | None
