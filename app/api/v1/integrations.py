from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rbac import Screen, require
from app.models import Integration
from app.schemas.integrations import IntegrationOut

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("", response_model=list[IntegrationOut], dependencies=[Depends(require(Screen.integrations))])
def list_integrations(db: Session = Depends(get_db)) -> list[IntegrationOut]:
    rows = db.scalars(select(Integration).order_by(Integration.status, Integration.name))
    return [IntegrationOut.model_validate(r) for r in rows]
