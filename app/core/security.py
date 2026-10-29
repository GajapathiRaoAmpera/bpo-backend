from datetime import timedelta

import jwt
from passlib.context import CryptContext

from app.core.config import get_settings
from app.core.db import utcnow

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _pwd.verify(password, password_hash)


def create_token(user_id: int, role: str, landing: str) -> str:
    s = get_settings()
    now = utcnow()
    # role + landing are read (unverified) by the Next.js proxy for routing only;
    # every API call is re-authorised here from the DB user.
    payload = {"sub": str(user_id), "role": role, "landing": landing, "iat": now,
               "exp": now + timedelta(minutes=s.jwt_ttl_minutes)}
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_token(token: str) -> dict:
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm], options={"require": ["exp", "sub"]})
