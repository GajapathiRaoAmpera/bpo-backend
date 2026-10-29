from datetime import date

from pydantic import BaseModel, ConfigDict

from app.core.masking import mask_email, mask_phone
from app.models import Customer
from app.models.enums import Language, PolicyStatus, Product


class PolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    policy_no: str
    product: Product
    plan_name: str
    sum_insured_inr: int
    premium_inr: int
    status: PolicyStatus
    start_date: date
    renewal_date: date
    vehicle_reg: str | None


class CustomerOut(BaseModel):
    id: int
    customer_code: str
    full_name: str
    phone: str
    email: str
    dob: date | None  # None for masked roles
    city: str
    state: str
    preferred_language: Language
    masked: bool


class CustomerRow(CustomerOut):
    policy_count: int
    next_renewal: date | None


class CustomerDetail(CustomerOut):
    policies: list[PolicyOut]


class CustomerPage(BaseModel):
    items: list[CustomerRow]
    total: int
    page: int
    page_size: int


def customer_fields(c: Customer, masked: bool) -> dict:
    return dict(
        id=c.id, customer_code=c.customer_code, full_name=c.full_name,
        phone=mask_phone(c.phone) if masked else c.phone,
        email=mask_email(c.email) if masked else c.email,
        dob=None if masked else c.dob,
        city=c.city, state=c.state, preferred_language=c.preferred_language, masked=masked,
    )
