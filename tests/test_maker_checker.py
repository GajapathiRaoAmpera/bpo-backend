from sqlalchemy import select

from app.core.db import utcnow
from app.models import Script, User
from app.models.enums import ApprovalStatus, CallMode, Role
from app.services.scripts import active_script


def test_bot_ignores_pending_newer_script(db):
    pending = db.scalar(select(Script).where(Script.mode == CallMode.renewal, Script.status == ApprovalStatus.pending))
    assert pending is not None and pending.version == 2
    active = active_script(db, CallMode.renewal)
    assert active.status == ApprovalStatus.approved and active.version == 1


def test_approval_switches_active_script(db):
    pending = db.scalar(select(Script).where(Script.mode == CallMode.renewal, Script.status == ApprovalStatus.pending))
    checker = db.scalar(select(User).where(User.role == Role.compliance_officer))
    pending.status, pending.reviewed_by, pending.reviewed_at = ApprovalStatus.approved, checker.id, utcnow()
    db.flush()
    assert active_script(db, CallMode.renewal).id == pending.id
    db.rollback()  # keep the shared test DB unchanged
    assert active_script(db, CallMode.renewal).version == 1
