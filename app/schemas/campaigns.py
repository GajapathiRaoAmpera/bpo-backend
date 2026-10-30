from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import CampaignStatus, Language, Product

CAMPAIGN_CHECKS = ("calling_window", "dnd_scrubbed", "approved_script", "segment_reviewed")


class Segment(BaseModel):
    renewal_within_days: int = Field(30, ge=1, le=90)
    languages: list[Language] = Field(default_factory=lambda: list(Language), min_length=1)


class CampaignIn(BaseModel):
    name: str = Field(min_length=3, max_length=160)
    product: Product
    segment: Segment = Field(default_factory=Segment)
    scheduled_at: datetime | None = None


class CampaignPatch(BaseModel):
    name: str | None = Field(None, min_length=3, max_length=160)
    product: Product | None = None
    segment: Segment | None = None
    scheduled_at: datetime | None = None


class ScriptRef(BaseModel):
    id: int
    name: str
    version: int


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    product: Product
    segment: dict
    script_id: int
    script: ScriptRef | None = None
    status: CampaignStatus
    created_by: int
    created_by_name: str = ""
    created_at: datetime
    submitted_at: datetime | None
    reviewed_by: int | None
    reviewed_by_name: str | None = None
    reviewed_at: datetime | None
    review_note: str | None
    checklist: dict[str, bool] | None
    scheduled_at: datetime | None
    total_targets: int
    dialled: int
    connected: int
    converted: int


class AudienceOut(BaseModel):
    total_targets: int
    script: ScriptRef | None  # active approved renewal script the campaign will use


class LaunchOut(BaseModel):
    campaign: CampaignOut
    call_ids: list[int]
