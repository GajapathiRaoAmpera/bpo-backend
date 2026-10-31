import os
import tempfile

# Must be set before the app (and its engine) is imported.
_tmp = tempfile.mkdtemp()
os.environ.update(JWT_SECRET="test-secret-" + "x" * 40, SEED_PASSWORD="Test@Pass1",
                  DATABASE_URL=f"sqlite:///{_tmp}/test.db", FRONTEND_ORIGIN="http://localhost:3000",
                  GOOGLE_API_KEY="",  # never call Gemini from tests
                  PLIVO_AUTH_ID="", PLIVO_AUTH_TOKEN="", PLIVO_FROM_NUMBER="", PUBLIC_BASE_URL="",
                  CONTACT_ALLOWLIST_PREFIX="")  # never dial real phones from tests, whatever .env says

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.enums import Role  # noqa: E402
from app.seed import PERSONAS, seed  # noqa: E402

PASSWORD = os.environ["SEED_PASSWORD"]
EMAILS = {role: f"{n.split()[0].lower()}.{n.split(' ', 1)[1].lower().replace(' ', '')}@sandbox.assurevoice.in"
          for n, role, _ in PERSONAS}


@pytest.fixture(scope="session", autouse=True)
def _db():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db, PASSWORD, customers=40, calls=120, days=10)
    yield


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def login(client):
    def _login(role: Role) -> TestClient:
        r = client.post("/api/v1/auth/login", json={"email": EMAILS[role], "password": PASSWORD})
        assert r.status_code == 200, r.text
        return client
    return _login
