from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Script
from app.models.enums import ApprovalStatus, CallMode


def active_script(db: Session, mode: CallMode) -> Script | None:
    """Maker-checker: the bot only ever runs the most recently APPROVED script."""
    return db.scalar(select(Script)
                     .where(Script.mode == mode, Script.status == ApprovalStatus.approved)
                     .order_by(Script.reviewed_at.desc(), Script.version.desc())
                     .limit(1))
