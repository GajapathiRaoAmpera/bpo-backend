"""`python -m app.import_customers file.csv [--dry-run]`: upsert customers + policies from a CSV.

One row per policy; rows are grouped into customers by `customer_code`. Every row needs `consent=yes`.
All-or-nothing: any bad row prints `row N: reason` (N = CSV line, header = 1) and nothing is written.
Upserts on customer_code / policy_no in one transaction, plus one counts-only audit_log row."""

import csv
import re
import sys
from collections.abc import Iterable
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.audit import record
from app.models import Customer, Policy
from app.models.enums import AuditAction, Language, PolicyStatus, Product
from app.services.voice_tools import normalize_policy_no, parse_dob

CUSTOMER_COLS = ("customer_code", "full_name", "phone", "email", "dob", "city", "state", "preferred_language")
POLICY_COLS = ("policy_no", "product", "plan_name", "sum_insured_inr", "premium_inr", "status",
               "start_date", "renewal_date")
REQUIRED = (*CUSTOMER_COLS, *POLICY_COLS, "consent")  # vehicle_reg is optional
TEXT_MAX = {"customer_code": 20, "full_name": 120, "city": 80, "state": 80, "plan_name": 120}
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
POLICY_RE = re.compile(r"[A-Z]{3}-\d{4}-\d{6}")


def _phone(raw: str) -> str:
    """Indian mobile, stored as 10 digits like the seed: '+91 98765-43210' -> '9876543210'."""
    digits = re.sub(r"[\s\-()]", "", raw).removeprefix("+91")
    digits = digits[2:] if len(digits) == 12 and digits.startswith("91") else digits.removeprefix("0")
    if not re.fullmatch(r"[6-9]\d{9}", digits):
        raise ValueError("phone must be a 10-digit Indian mobile starting 6-9")
    return digits


def _enum(enum: type[StrEnum], col: str, raw: str):
    try:
        return enum(raw.lower())
    except ValueError:
        raise ValueError(f"{col} must be one of {', '.join(enum)}") from None


def _parse(row: dict[str, str]) -> tuple[dict, dict]:
    """One CSV row -> (customer fields, policy fields). Raises ValueError with the reason."""
    v = {k: (row.get(k) or "").strip() for k in (*REQUIRED, "vehicle_reg")}
    if v["consent"].lower() != "yes":
        raise ValueError("consent must be 'yes'")
    if missing := [k for k in REQUIRED if not v[k]]:
        raise ValueError(f"missing {', '.join(missing)}")
    for col, n in TEXT_MAX.items():
        if len(v[col]) > n:
            raise ValueError(f"{col} longer than {n} characters")
    if not EMAIL_RE.fullmatch(v["email"]) or len(v["email"]) > 200:
        raise ValueError("email is not valid")
    dates = {}
    for col in ("dob", "start_date", "renewal_date"):
        dates[col] = parse_dob(v[col])
        if dates[col] is None:
            raise ValueError(f"{col} is not a date (use YYYY-MM-DD)")
    if dates["renewal_date"] < dates["start_date"]:
        raise ValueError("renewal_date is before start_date")
    inr = {}
    for col in ("sum_insured_inr", "premium_inr"):
        if not (amount := v[col].replace(",", "")).isdigit():
            raise ValueError(f"{col} must be a whole number of rupees, 0 or more")
        inr[col] = int(amount)
    policy_no = normalize_policy_no(v["policy_no"])
    if not POLICY_RE.fullmatch(policy_no):
        raise ValueError("policy_no must look like MOT-2026-001234")
    if len(v["vehicle_reg"]) > 20:
        raise ValueError("vehicle_reg longer than 20 characters")
    customer = {"customer_code": v["customer_code"], "full_name": v["full_name"], "phone": _phone(v["phone"]),
                "email": v["email"], "dob": dates["dob"], "city": v["city"], "state": v["state"],
                "preferred_language": _enum(Language, "preferred_language", v["preferred_language"])}
    policy = {"policy_no": policy_no, "product": _enum(Product, "product", v["product"]),
              "plan_name": v["plan_name"], **inr, "status": _enum(PolicyStatus, "status", v["status"]),
              "start_date": dates["start_date"], "renewal_date": dates["renewal_date"],
              "vehicle_reg": v["vehicle_reg"] or None}
    return customer, policy


def import_customers(db: Session, lines: Iterable[str], dry_run: bool = False) -> tuple[dict, list[str]]:
    """Validate and upsert. Returns (counts, errors); on any error nothing is written."""
    reader = csv.DictReader(lines)
    if missing := [c for c in REQUIRED if c not in (reader.fieldnames or [])]:
        return {}, [f"row 1: missing columns {', '.join(missing)}"]
    errors: list[str] = []
    customers: dict[str, dict] = {}
    policies: list[tuple[int, str, dict]] = []  # (line, customer_code, fields)
    seen_policies: set[str] = set()
    for row in reader:
        line = reader.line_num
        try:
            cust, pol = _parse(row)
            code = cust["customer_code"]
            if customers.setdefault(code, cust) != cust:
                raise ValueError(f"customer fields differ from an earlier row for {code}")
            if pol["policy_no"] in seen_policies:
                raise ValueError(f"policy_no {pol['policy_no']} appears twice")
        except ValueError as e:
            errors.append(f"row {line}: {e}")
            continue
        seen_policies.add(pol["policy_no"])
        policies.append((line, code, pol))
    if errors:
        return {}, errors

    counts = dict.fromkeys(("customers_created", "customers_updated", "policies_created", "policies_updated"), 0)
    try:
        by_code: dict[str, Customer] = {}
        for code, fields in customers.items():
            c = db.scalar(select(Customer).where(Customer.customer_code == code))
            counts["customers_updated" if c else "customers_created"] += 1
            if c is None:
                c = Customer(**fields)
                db.add(c)
            else:
                for k, val in fields.items():
                    setattr(c, k, val)
            by_code[code] = c
        db.flush()
        for line, code, fields in policies:
            p = db.scalar(select(Policy).where(Policy.policy_no == fields["policy_no"]))
            if p and p.customer_id != by_code[code].id:  # never silently move a policy between customers
                errors.append(f"row {line}: policy_no {fields['policy_no']} belongs to another customer")
                continue
            counts["policies_updated" if p else "policies_created"] += 1
            if p is None:
                db.add(Policy(customer_id=by_code[code].id, **fields))
            else:
                for k, val in fields.items():
                    setattr(p, k, val)
        if errors or dry_run:
            db.rollback()
            return ({} if errors else counts), errors
        # The CLI has no HTTP request; record() only reads headers/client/state, so an empty scope works.
        # record() commits, which commits the whole import together with its audit row.
        record(db, Request({"type": "http", "headers": []}), AuditAction.create,
               entity_type="customer_import", detail=counts)
    except Exception:
        db.rollback()
        raise
    return counts, []


def main(argv: list[str]) -> int:
    paths = [a for a in argv if a != "--dry-run"]
    if len(paths) != 1:
        print("usage: python -m app.import_customers file.csv [--dry-run]", file=sys.stderr)
        return 2
    from app.core.db import SessionLocal  # imported here so tests can use the function without a CLI engine

    with open(paths[0], newline="", encoding="utf-8-sig") as f, SessionLocal() as db:
        counts, errors = import_customers(db, f, dry_run="--dry-run" in argv)
    for e in errors:
        print(e, file=sys.stderr)
    if errors:
        print(f"{len(errors)} bad row(s); nothing imported.", file=sys.stderr)
        return 1
    print(("Dry run, nothing written: " if "--dry-run" in argv else "Imported: ")
          + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
