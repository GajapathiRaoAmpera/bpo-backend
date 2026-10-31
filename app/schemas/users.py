from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import Role

MIN_PASSWORD_LEN = 12  # admin-set staff passwords; also enforced by app.bootstrap


class UserAdminRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    full_name: str
    job_title: str
    role: Role
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class UserCreateIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=120)
    job_title: str = Field(min_length=2, max_length=120)
    role: Role
    password: str = Field(min_length=MIN_PASSWORD_LEN, max_length=128)


class UserPatchIn(BaseModel):
    full_name: str | None = Field(None, min_length=2, max_length=120)
    job_title: str | None = Field(None, min_length=2, max_length=120)
    role: Role | None = None
    is_active: bool | None = None  # true reactivates; deactivate via DELETE or false
    password: str | None = Field(None, min_length=MIN_PASSWORD_LEN, max_length=128)
