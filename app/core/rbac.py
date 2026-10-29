"""Single source of truth for role access. The UI menu and the permission-matrix
screen both read this through the API; never duplicate it in the frontend."""

from dataclasses import dataclass
from enum import StrEnum

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import decode_token
from app.models import User
from app.models.enums import Role


class Level(StrEnum):
    full = "full"
    view = "view"
    approve = "approve"
    masked = "masked"
    own = "own"  # full access, but only to the user's own records


class Screen(StrEnum):
    dashboard = "dashboard"
    agent_console = "agent_console"
    escalations = "escalations"
    live_monitor = "live_monitor"
    calls = "calls"
    customers = "customers"
    campaigns = "campaigns"
    followups = "followups"
    bot_studio = "bot_studio"
    approvals = "approvals"
    qa = "qa"
    audit = "audit"
    users = "users"
    integrations = "integrations"


R = Role
L = Level

MATRIX: dict[Screen, dict[Role, Level]] = {
    Screen.dashboard: {R.super_admin: L.full, R.ops_manager: L.full, R.team_lead: L.view,
                       R.campaign_manager: L.view, R.qa_analyst: L.view, R.compliance_officer: L.view},
    Screen.agent_console: {R.team_lead: L.full, R.human_agent: L.full},
    Screen.escalations: {R.team_lead: L.full, R.human_agent: L.full, R.ops_manager: L.view},
    Screen.live_monitor: {R.team_lead: L.full, R.ops_manager: L.view},
    Screen.calls: {R.ops_manager: L.full, R.team_lead: L.full, R.qa_analyst: L.full, R.human_agent: L.own,
                   R.super_admin: L.view, R.campaign_manager: L.view, R.bot_designer: L.view,
                   R.compliance_officer: L.view},
    Screen.customers: {R.team_lead: L.full, R.human_agent: L.full, R.ops_manager: L.masked,
                       R.campaign_manager: L.masked, R.qa_analyst: L.masked, R.compliance_officer: L.masked},
    Screen.campaigns: {R.campaign_manager: L.full, R.ops_manager: L.approve, R.compliance_officer: L.approve},
    Screen.followups: {R.team_lead: L.full, R.human_agent: L.full, R.campaign_manager: L.full,
                       R.ops_manager: L.view},
    Screen.bot_studio: {R.bot_designer: L.full, R.compliance_officer: L.view},
    Screen.approvals: {R.ops_manager: L.approve, R.compliance_officer: L.approve},
    Screen.qa: {R.qa_analyst: L.full, R.ops_manager: L.view, R.team_lead: L.view, R.compliance_officer: L.view},
    Screen.audit: {R.super_admin: L.full, R.compliance_officer: L.full},
    Screen.users: {R.super_admin: L.full},
    Screen.integrations: {R.super_admin: L.full},
}

LANDING: dict[Role, Screen] = {
    R.super_admin: Screen.dashboard,
    R.ops_manager: Screen.dashboard,
    R.team_lead: Screen.live_monitor,
    R.human_agent: Screen.agent_console,
    R.campaign_manager: Screen.campaigns,
    R.bot_designer: Screen.bot_studio,
    R.qa_analyst: Screen.qa,
    R.compliance_officer: Screen.approvals,
}

ROLE_LABELS: dict[Role, str] = {
    R.super_admin: "Super Admin",
    R.ops_manager: "Operations Manager",
    R.team_lead: "Team Lead",
    R.human_agent: "Human Agent",
    R.campaign_manager: "Campaign Manager",
    R.bot_designer: "Bot Designer",
    R.qa_analyst: "QA Analyst",
    R.compliance_officer: "Compliance Officer",
}


def nav_for(role: Role) -> dict[Screen, Level]:
    return {screen: roles[role] for screen, roles in MATRIX.items() if role in roles}


@dataclass
class Access:
    user: User
    level: Level

    @property
    def masked(self) -> bool:
        return self.level == Level.masked


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(get_settings().cookie_name)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    try:
        user_id = int(decode_token(token)["sub"])
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired, please sign in again")
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account is not active")
    request.state.user = user  # read by the audit middleware
    return user


def require(screen: Screen, *levels: Level):
    """Dependency: 403 unless the user's role has access to `screen`
    (and, if given, one of `levels`)."""

    def dep(user: User = Depends(current_user)) -> Access:
        level = MATRIX[screen].get(user.role)
        if level is None or (levels and level not in levels):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role does not have access to this action")
        return Access(user, level)

    return dep
