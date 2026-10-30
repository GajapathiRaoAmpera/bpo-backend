from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ApprovalStatus, CallMode, Speaker

# Compliance checklist a checker ticks before approving a script (same keys as the seed data).
SCRIPT_CHECKS = ("ai_disclosure", "verification_before_details", "figures_from_tools_only",
                 "no_mis_selling", "escalation_path")


class ScriptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    mode: CallMode
    version: int
    system_prompt: str
    status: ApprovalStatus
    parent_id: int | None
    created_by: int
    created_by_name: str = ""
    created_at: datetime
    submitted_at: datetime | None
    reviewed_by: int | None
    reviewed_by_name: str | None = None
    reviewed_at: datetime | None
    review_note: str | None
    checklist: dict[str, bool] | None
    active: bool = False  # the script the bot currently runs for this mode


class ScriptIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    mode: CallMode
    system_prompt: str = Field(min_length=20, max_length=20_000)
    parent_id: int | None = None  # defaults to the active script for the mode


class ScriptPatch(BaseModel):
    name: str | None = Field(None, min_length=3, max_length=120)
    system_prompt: str | None = Field(None, min_length=20, max_length=20_000)


class ReviewIn(BaseModel):
    note: str | None = Field(None, max_length=2000)
    checklist: dict[str, bool] = Field(default_factory=dict)


class TestCallIn(BaseModel):
    customer_says: str = Field("I want to renew my policy.", min_length=1, max_length=500)


class TestTurn(BaseModel):
    speaker: Speaker
    text: str


class TestCallOut(BaseModel):
    checks: dict[str, bool]  # automatic pre-check of the prompt against the compliance checklist
    turns: list[TestTurn]
    simulated: bool = True
    note: str
