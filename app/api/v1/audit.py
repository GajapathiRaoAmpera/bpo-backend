import csv
import io
import json
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.db import get_db, utcnow
from app.core.formatting import IST
from app.core.rbac import Screen, require
from app.models import AuditLog, User
from app.models.enums import AuditAction
from app.schemas.audit import AuditPage, AuditRow

router = APIRouter(prefix="/audit", tags=["audit"])
can_read = require(Screen.audit)


class AuditFilters:
    def __init__(
        self,
        actor: str | None = Query(None, max_length=80, description="Actor name or email contains"),
        action: AuditAction | None = None,
        entity_type: str | None = Query(None, max_length=40),
        date_from: date | None = Query(None, description="IST calendar day, inclusive"),
        date_to: date | None = Query(None, description="IST calendar day, inclusive"),
    ):
        self.actor, self.action, self.entity_type = actor, action, entity_type
        self.date_from, self.date_to = date_from, date_to

    def apply(self, stmt: Select) -> Select:
        if self.actor:
            like = f"%{self.actor.strip()}%"
            stmt = stmt.where(or_(User.full_name.ilike(like), User.email.ilike(like)))
        if self.action:
            stmt = stmt.where(AuditLog.action == self.action)
        if self.entity_type:
            stmt = stmt.where(AuditLog.entity_type == self.entity_type)
        if self.date_from:
            stmt = stmt.where(AuditLog.ts >= datetime.combine(self.date_from, time.min, IST))
        if self.date_to:
            stmt = stmt.where(AuditLog.ts < datetime.combine(self.date_to + timedelta(days=1), time.min, IST))
        return stmt

    def as_dict(self) -> dict:
        return {k: str(v) for k, v in vars(self).items() if v is not None}


def _query(f: AuditFilters) -> Select:
    return f.apply(select(AuditLog, User.full_name).outerjoin(User, User.id == AuditLog.actor_user_id))


def _row(log: AuditLog, name: str | None) -> AuditRow:
    return AuditRow(id=log.id, ts=log.ts, actor_user_id=log.actor_user_id, actor_name=name,
                    actor_role=log.actor_role, action=log.action, entity_type=log.entity_type,
                    entity_id=log.entity_id, detail=log.detail, ip=log.ip)


@router.get("", response_model=AuditPage, dependencies=[Depends(can_read)])
def list_audit(f: AuditFilters = Depends(), page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
               db: Session = Depends(get_db)) -> AuditPage:
    stmt = _query(f)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return AuditPage(items=[_row(log, name) for log, name in rows], total=total, page=page, page_size=page_size)


@router.get("/export.csv", dependencies=[Depends(can_read)],
            response_class=StreamingResponse, responses={200: {"content": {"text/csv": {}}}})
def export_audit(request: Request, f: AuditFilters = Depends(), db: Session = Depends(get_db)) -> StreamingResponse:
    stmt = _query(f)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    # Audited before streaming, so the export is on record even if the download is interrupted.
    audit.record(db, request, AuditAction.export, entity_type="audit_log",
                 detail={"format": "csv", "rows": total, "filters": f.as_dict()})
    rows = db.execute(stmt.order_by(AuditLog.id)).all()

    def lines():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "timestamp_utc", "actor", "actor_role", "action", "entity_type", "entity_id", "detail", "ip"])
        for log, name in rows:
            w.writerow([log.id, log.ts.isoformat(), name or "", log.actor_role or "", log.action,
                        log.entity_type or "", log.entity_id or "", json.dumps(log.detail) if log.detail else "",
                        log.ip or ""])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()

    filename = f"assurevoice-audit-{utcnow().astimezone(IST):%Y%m%d-%H%M}.csv"
    return StreamingResponse(lines(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})
