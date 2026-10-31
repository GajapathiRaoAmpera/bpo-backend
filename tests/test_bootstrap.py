import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.bootstrap import main
from app.core.db import Base, make_engine
from app.core.security import verify_password
from app.models import AuditLog, Integration, Script, User
from app.models.enums import ApprovalStatus, Role

PW = "Long@Enough12"
ARGS = ["--email", "IT.Admin@Insurer.in", "--name", "Asha Rao"]


@pytest.fixture
def empty_db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/boot.db")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as s:
        yield s
    engine.dispose()


def _count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def test_bootstrap_creates_admin_scripts_integrations(empty_db, monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_PASSWORD", PW)
    assert main(ARGS, empty_db) == 0
    admin = empty_db.scalars(select(User)).one()
    assert (admin.email, admin.role, admin.job_title) == ("it.admin@insurer.in", Role.super_admin, "IT Administrator")
    assert verify_password(PW, admin.password_hash)
    assert _count(empty_db, Script) == 2  # only the approved defaults, no demo draft
    assert {s.mode for s in empty_db.scalars(select(Script).where(Script.status == ApprovalStatus.approved))} \
        == {"inbound", "renewal"}
    assert _count(empty_db, Integration) > 0
    entry = empty_db.scalars(select(AuditLog)).one()
    assert entry.entity_id == str(admin.id) and PW not in str(entry.detail)


def test_bootstrap_second_run_is_noop(empty_db, monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_PASSWORD", PW)
    main(ARGS, empty_db)
    before = [_count(empty_db, m) for m in (User, Script, Integration, AuditLog)]
    assert main(["--email", "other@insurer.in", "--name", "Other"], empty_db) == 0
    assert [_count(empty_db, m) for m in (User, Script, Integration, AuditLog)] == before


def test_bootstrap_rejects_short_password(empty_db, monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_PASSWORD", "Short@Pass1")  # 11 chars
    assert main(ARGS, empty_db) == 1
    assert _count(empty_db, User) == 0


def test_users_api_rejects_11_char_password(login):
    c = login(Role.super_admin)
    body = {"email": "eleven@sandbox.assurevoice.in", "full_name": "Eleven Chars", "job_title": "Service Executive",
            "role": "human_agent", "password": "Short@Pass1"}
    assert c.post("/api/v1/users", json=body).status_code == 422
    me = c.get("/api/v1/auth/me").json()["user"]["id"]
    assert c.patch(f"/api/v1/users/{me}", json={"password": "Short@Pass1"}).status_code == 422
