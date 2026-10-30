from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.db import get_db, utcnow
from app.core.rbac import Access, Level, Screen, require
from app.models import Script, User
from app.models.enums import ApprovalStatus, AuditAction
from app.schemas.scripts import (
    SCRIPT_CHECKS, ReviewIn, ScriptIn, ScriptOut, ScriptPatch, TestCallIn, TestCallOut,
)
from app.services.scripts import active_script, simulate_test_call

router = APIRouter(prefix="/scripts", tags=["scripts"])
write = require(Screen.bot_studio, Level.full)
checker = require(Screen.approvals, Level.approve)


def script_out(db: Session, s: Script) -> ScriptOut:
    active = active_script(db, s.mode)
    return ScriptOut.model_validate(s).model_copy(update=dict(
        created_by_name=db.get(User, s.created_by).full_name,
        reviewed_by_name=db.get(User, s.reviewed_by).full_name if s.reviewed_by else None,
        active=active is not None and active.id == s.id))


def get_script(db: Session, script_id: int) -> Script:
    s = db.get(Script, script_id)
    if not s:
        raise HTTPException(404, "Script not found")
    return s


def _draft(db: Session, script_id: int) -> Script:
    s = get_script(db, script_id)
    if s.status != ApprovalStatus.draft:
        raise HTTPException(409, "Only draft scripts can be changed. Create a new version instead.")
    return s


@router.get("", response_model=list[ScriptOut])
def list_scripts(access: Access = Depends(require(Screen.bot_studio)), db: Session = Depends(get_db)) -> list[ScriptOut]:
    rows = db.scalars(select(Script).order_by(Script.mode, Script.version.desc())).all()
    return [script_out(db, s) for s in rows]


@router.post("", response_model=ScriptOut, status_code=status.HTTP_201_CREATED)
def create_script(body: ScriptIn, request: Request, access: Access = Depends(write),
                  db: Session = Depends(get_db)) -> ScriptOut:
    """New version = new draft row; parent defaults to the script the bot runs for this mode."""
    parent = get_script(db, body.parent_id) if body.parent_id else active_script(db, body.mode)
    if parent and parent.mode != body.mode:
        raise HTTPException(422, "The parent script is for a different call mode")
    version = (db.scalar(select(func.max(Script.version)).where(Script.mode == body.mode)) or 0) + 1
    s = Script(name=body.name, mode=body.mode, version=version, system_prompt=body.system_prompt,
               status=ApprovalStatus.draft, parent_id=parent.id if parent else None, created_by=access.user.id)
    db.add(s)
    db.commit()
    audit.record(db, request, AuditAction.create, entity_type="script", entity_id=s.id,
                 detail={"mode": s.mode, "version": s.version, "parent_id": s.parent_id})
    return script_out(db, s)


@router.patch("/{script_id}", response_model=ScriptOut)
def update_script(script_id: int, body: ScriptPatch, request: Request, access: Access = Depends(write),
                  db: Session = Depends(get_db)) -> ScriptOut:
    s = _draft(db, script_id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    for k, v in changes.items():
        setattr(s, k, v)
    db.commit()
    audit.record(db, request, AuditAction.update, entity_type="script", entity_id=s.id,
                 detail={"fields": sorted(changes)})
    return script_out(db, s)


@router.post("/{script_id}/submit", response_model=ScriptOut)
def submit_script(script_id: int, request: Request, access: Access = Depends(write),
                  db: Session = Depends(get_db)) -> ScriptOut:
    s = _draft(db, script_id)
    s.status, s.submitted_at = ApprovalStatus.pending, utcnow()
    db.commit()
    audit.record(db, request, AuditAction.submit, entity_type="script", entity_id=s.id,
                 detail={"mode": s.mode, "version": s.version})
    return script_out(db, s)


@router.post("/{script_id}/test-call", response_model=TestCallOut)
def test_call(script_id: int, body: TestCallIn, request: Request, access: Access = Depends(write),
              db: Session = Depends(get_db)) -> TestCallOut:
    s = get_script(db, script_id)
    request.state.audited = True  # read-only preview, nothing changes
    return TestCallOut(**simulate_test_call(s.system_prompt, body.customer_says))


def review(db: Session, request: Request, s: Script, user: User, body: ReviewIn, approve: bool) -> Script:
    if s.status != ApprovalStatus.pending:
        raise HTTPException(409, "Only scripts awaiting approval can be reviewed")
    if s.created_by == user.id:
        raise HTTPException(403, "You can't review a script you created")
    if approve and not all(body.checklist.get(k) for k in SCRIPT_CHECKS):
        raise HTTPException(422, "Tick every compliance check before approving")
    if not approve and not (body.note or "").strip():
        raise HTTPException(422, "Add a note explaining what needs to change")
    s.status = ApprovalStatus.approved if approve else ApprovalStatus.rejected
    s.reviewed_by, s.reviewed_at, s.review_note = user.id, utcnow(), body.note
    s.checklist = {k: bool(body.checklist.get(k)) for k in SCRIPT_CHECKS}
    db.commit()
    audit.record(db, request, AuditAction.approve if approve else AuditAction.reject, entity_type="script",
                 entity_id=s.id, detail={"mode": s.mode, "version": s.version, "checklist": s.checklist})
    return s


@router.post("/{script_id}/approve", response_model=ScriptOut)
def approve_script(script_id: int, body: ReviewIn, request: Request, access: Access = Depends(checker),
                   db: Session = Depends(get_db)) -> ScriptOut:
    return script_out(db, review(db, request, get_script(db, script_id), access.user, body, approve=True))


@router.post("/{script_id}/reject", response_model=ScriptOut)
def reject_script(script_id: int, body: ReviewIn, request: Request, access: Access = Depends(checker),
                  db: Session = Depends(get_db)) -> ScriptOut:
    return script_out(db, review(db, request, get_script(db, script_id), access.user, body, approve=False))
