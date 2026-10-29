from fastapi import Depends
from sqlalchemy import select

from app.core.rbac import current_user
from app.main import app
from app.models import AuditLog
from app.models.enums import AuditAction, Role


@app.patch("/api/v1/_probe/{item_id}", include_in_schema=False)
def _probe(item_id: int, user=Depends(current_user)):  # test-only write endpoint without explicit audit
    return {"ok": item_id}


def test_middleware_audits_unaudited_writes(login, db):
    before = db.scalar(select(AuditLog.id).order_by(AuditLog.id.desc()))
    assert login(Role.team_lead).patch("/api/v1/_probe/7").status_code == 200
    rows = db.scalars(select(AuditLog).where(AuditLog.id > before)).all()
    # login (explicit) + the patch (middleware); login must not be double-logged
    assert [r.action for r in rows] == [AuditAction.login, AuditAction.update]
    assert rows[-1].detail["path"] == "/api/v1/_probe/7"
    assert rows[-1].actor_role == Role.team_lead


def test_middleware_skips_anonymous_and_failed_writes(client, db):
    before = db.scalar(select(AuditLog.id).order_by(AuditLog.id.desc()))
    assert client.patch("/api/v1/_probe/7").status_code == 401
    assert db.scalar(select(AuditLog.id).where(AuditLog.id > before)) is None
