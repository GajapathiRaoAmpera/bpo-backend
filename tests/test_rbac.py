import pytest
from sqlalchemy import select

from app.core.rbac import MATRIX, Level, Screen
from app.models import AuditLog, Customer
from app.models.enums import AuditAction, Role

R = Role
CUSTOMER_ROLES = {R.team_lead, R.human_agent, R.ops_manager, R.campaign_manager, R.qa_analyst, R.compliance_officer}


@pytest.mark.parametrize("role", list(Role))
def test_customers_endpoint_403_for_roles_without_access(login, role):
    status = login(role).get("/api/v1/customers").status_code
    assert status == (200 if role in CUSTOMER_ROLES else 403)


@pytest.mark.parametrize("role", list(Role))
def test_permission_matrix_is_super_admin_only(login, role):
    status = login(role).get("/api/v1/rbac/matrix").status_code
    assert status == (200 if role == R.super_admin else 403)


@pytest.mark.parametrize("role", list(Role))
def test_menu_matches_matrix(login, role):
    nav = {n["screen"]: n["level"] for n in login(role).get("/api/v1/auth/me").json()["nav"]}
    assert nav == {s: lv for s, roles in MATRIX.items() for r, lv in roles.items() if r == role}


def test_matrix_matches_spec():
    # Spot-check the access matrix from the product brief.
    assert MATRIX[Screen.users] == {R.super_admin: Level.full}
    assert MATRIX[Screen.integrations] == {R.super_admin: Level.full}
    assert set(MATRIX[Screen.audit]) == {R.super_admin, R.compliance_officer}
    assert set(MATRIX[Screen.approvals]) == {R.ops_manager, R.compliance_officer}
    assert MATRIX[Screen.calls][R.human_agent] == Level.own
    assert MATRIX[Screen.campaigns] == {R.campaign_manager: Level.full, R.ops_manager: Level.approve,
                                        R.compliance_officer: Level.approve}
    assert set(MATRIX[Screen.agent_console]) == {R.team_lead, R.human_agent}
    assert R.super_admin not in MATRIX[Screen.customers]


def test_customer_view_is_audited(login, db):
    cid = db.scalar(select(Customer.id).limit(1))
    assert login(R.team_lead).get(f"/api/v1/customers/{cid}").status_code == 200
    last = db.scalars(select(AuditLog).order_by(AuditLog.id.desc())).first()
    assert (last.action, last.entity_id) == (AuditAction.view_customer, str(cid))
