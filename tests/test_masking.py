import re

import pytest
from sqlalchemy import select

from app.core.masking import mask_phone
from app.models import Customer
from app.models.enums import Role

MASKED_PHONE = re.compile(r"^\d{2}X{6}\d{2}$")


def test_mask_phone_format():
    assert mask_phone("9812345621") == "98XXXXXX21"
    assert mask_phone("+91 98123 45621") == "98XXXXXX21"


@pytest.mark.parametrize("role", [Role.ops_manager, Role.campaign_manager, Role.qa_analyst, Role.compliance_officer])
def test_masked_roles_get_masked_phone_and_no_dob(login, db, role):
    c = login(role)
    for row in c.get("/api/v1/customers").json()["items"]:
        assert MASKED_PHONE.match(row["phone"]) and row["dob"] is None and row["masked"]
    cust = db.scalars(select(Customer)).first()
    detail = c.get(f"/api/v1/customers/{cust.id}").json()
    assert detail["phone"] == mask_phone(cust.phone)
    assert detail["dob"] is None
    assert cust.email not in str(detail)


@pytest.mark.parametrize("role", [Role.team_lead, Role.human_agent])
def test_full_roles_see_real_phone_and_dob(login, db, role):
    cust = db.scalars(select(Customer)).first()
    detail = login(role).get(f"/api/v1/customers/{cust.id}").json()
    assert detail["phone"] == cust.phone
    assert detail["dob"] == cust.dob.isoformat()
    assert not detail["masked"]


def test_masked_roles_cannot_search_by_phone(login, db):
    cust = db.scalars(select(Customer)).first()
    assert login(Role.human_agent).get("/api/v1/customers", params={"q": cust.phone}).json()["total"] >= 1
    assert login(Role.ops_manager).get("/api/v1/customers", params={"q": cust.phone}).json()["total"] == 0
