from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.rbac import LANDING, MATRIX, ROLE_LABELS, Level, Screen, require
from app.models.enums import Role

router = APIRouter(prefix="/rbac", tags=["rbac"])


class RoleInfo(BaseModel):
    role: Role
    label: str
    landing: Screen


class MatrixOut(BaseModel):
    roles: list[RoleInfo]
    screens: list[Screen]
    matrix: dict[Screen, dict[Role, Level]]


@router.get("/matrix", response_model=MatrixOut, dependencies=[Depends(require(Screen.users))])
def matrix() -> MatrixOut:
    return MatrixOut(roles=[RoleInfo(role=r, label=ROLE_LABELS[r], landing=LANDING[r]) for r in Role],
                     screens=list(Screen), matrix=MATRIX)
