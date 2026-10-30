from typing import Literal

from pydantic import BaseModel

from app.schemas.campaigns import CampaignOut
from app.schemas.scripts import ScriptOut


class DiffRow(BaseModel):
    tag: Literal["equal", "replace", "delete", "insert"]
    left: str | None  # last approved script line
    right: str | None  # pending script line


class ScriptApproval(BaseModel):
    script: ScriptOut
    base: ScriptOut | None  # last approved script for the mode
    diff: list[DiffRow]


class ApprovalsOut(BaseModel):
    scripts: list[ScriptApproval]
    campaigns: list[CampaignOut]
    script_checks: list[str]
    campaign_checks: list[str]
