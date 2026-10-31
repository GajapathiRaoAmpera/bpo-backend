"""Create the first super_admin on an empty production database.

    BOOTSTRAP_PASSWORD=... python -m app.bootstrap --email it.admin@insurer.in --name "Asha Rao"

Password comes from BOOTSTRAP_PASSWORD or an interactive prompt. Also adds the approved default
scripts and the integration rows from the seed. Does nothing if any user already exists.
"""
import argparse
import getpass
import os
import sys

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import AuditLog, Script, User
from app.models.enums import ApprovalStatus, AuditAction, Role
from app.schemas.users import MIN_PASSWORD_LEN
from app.seed import _Gen


def bootstrap(db: Session, email: str, name: str, password: str, title: str = "IT Administrator") -> bool:
    """Returns False (and changes nothing) if any user already exists."""
    if db.scalar(select(User.id).limit(1)):
        return False
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    email = TypeAdapter(EmailStr).validate_python(email.strip()).lower()
    admin = User(email=email, full_name=name.strip(), job_title=title.strip(), role=Role.super_admin,
                 password_hash=hash_password(password))
    db.add(admin)
    db.flush()
    g = _Gen(db, rng_seed=0)
    # scripts_() credits the seed personas as maker/checker; on a fresh install the admin is the only user.
    g.users = {r: admin for r in (Role.bot_designer, Role.ops_manager, Role.compliance_officer)}
    g.scripts_()
    db.flush()
    db.execute(delete(Script).where(Script.status == ApprovalStatus.pending))  # the seed's demo v2 draft
    g.integrations_()
    db.add(AuditLog(actor_user_id=admin.id, actor_role=admin.role, action=AuditAction.create,
                    entity_type="user", entity_id=str(admin.id),
                    detail={"email": email, "role": Role.super_admin, "via": "bootstrap"}))
    db.commit()
    return True


def _password() -> str:
    if pw := os.environ.get("BOOTSTRAP_PASSWORD"):
        return pw
    pw = getpass.getpass("Password for the super admin: ")
    if pw != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match")
    return pw


def main(argv: list[str] | None = None, db: Session | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.bootstrap", description=__doc__.splitlines()[0])
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--title", default="IT Administrator")
    args = ap.parse_args(argv)
    if db is None:
        from app.core.db import SessionLocal
        with SessionLocal() as s:
            return main(argv, s)
    if db.scalar(select(User.id).limit(1)):
        print("Users already exist; nothing to do. Add staff in Admin → Users.")
        return 0
    try:
        bootstrap(db, args.email, args.name, _password(), args.title)
    except ValueError as e:  # includes pydantic ValidationError for a bad email
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Created super admin {args.email.strip().lower()} with default scripts and integrations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
