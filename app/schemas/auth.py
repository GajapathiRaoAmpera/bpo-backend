from pydantic import BaseModel, ConfigDict, EmailStr

from app.core.rbac import Level, Screen
from app.models.enums import Role


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    full_name: str
    job_title: str
    role: Role


class NavItem(BaseModel):
    screen: Screen
    level: Level


class MeOut(BaseModel):
    user: UserOut
    role_label: str
    landing: Screen
    nav: list[NavItem]
