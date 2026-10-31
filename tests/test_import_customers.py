import io
from pathlib import Path

from sqlalchemy import func, select

from app.import_customers import import_customers, main
from app.models import AuditLog, Customer, Policy

SAMPLE = (Path(__file__).parent.parent / "docs" / "customers_sample.csv").read_text()
HEADER, *ROWS = SAMPLE.splitlines()


def run(db, rows, **kw):
    return import_customers(db, io.StringIO("\n".join([HEADER, *rows]) + "\n"), **kw)


def tst_counts(db):
    customers = db.scalar(select(func.count()).select_from(Customer).where(Customer.customer_code.like("TST-%")))
    policies = db.scalar(select(func.count()).select_from(Policy).where(Policy.policy_no.like("%-9000__")))
    return customers, policies


def last_audit_id(db):
    return db.scalar(select(func.max(AuditLog.id))) or 0


def cleanup(db):
    ids = select(Customer.id).where(Customer.customer_code.like("TST-%"))
    db.query(Policy).filter(Policy.customer_id.in_(ids)).delete(synchronize_session=False)
    db.query(Customer).filter(Customer.customer_code.like("TST-%")).delete(synchronize_session=False)
    db.commit()


def test_valid_file_imports_and_audits_counts_only(db):
    cleanup(db)
    before = last_audit_id(db)
    counts, errors = run(db, ROWS)
    assert errors == []
    assert counts == {"customers_created": 2, "customers_updated": 0, "policies_created": 3, "policies_updated": 0}
    assert tst_counts(db) == (2, 3)
    c = db.scalar(select(Customer).where(Customer.customer_code == "TST-0001"))
    assert c.phone == "9876543210" and len(c.policies) == 2
    audits = db.scalars(select(AuditLog).where(AuditLog.id > before)).all()
    assert len(audits) == 1 and audits[0].entity_type == "customer_import"
    assert audits[0].action == "create" and audits[0].actor_user_id is None
    assert audits[0].detail == counts  # counts only: no names, phones, emails
    assert "Tester" not in str(audits[0].detail)
    cleanup(db)


def test_one_bad_row_writes_nothing(db):
    cleanup(db)
    before = last_audit_id(db)
    bad = ROWS[2].replace(",due,", ",overdue,").replace("12,400", "-5")
    counts, errors = run(db, [*ROWS[:2], bad])
    assert counts == {} and len(errors) == 1
    assert errors[0].startswith("row 4: ")  # header is line 1
    assert tst_counts(db) == (0, 0) and last_audit_id(db) == before


def test_row_without_consent_is_rejected(db):
    cleanup(db)
    no_consent = ROWS[0].removesuffix(",yes") + ",no"
    counts, errors = run(db, [no_consent, ROWS[2]])
    assert errors == ["row 2: consent must be 'yes'"]
    assert tst_counts(db) == (0, 0)


def test_mismatched_customer_fields_rejected(db):
    cleanup(db)
    other_city = ROWS[1].replace("Chennai", "Madurai")
    _, errors = run(db, [ROWS[0], other_city])
    assert errors == ["row 3: customer fields differ from an earlier row for TST-0001"]


def test_reimport_with_changed_premium_upserts(db):
    cleanup(db)
    run(db, ROWS)
    changed = ROWS[0].replace(",14200,", ",15999,")
    counts, errors = run(db, [changed, *ROWS[1:]])
    assert errors == []
    assert counts == {"customers_created": 0, "customers_updated": 2, "policies_created": 0, "policies_updated": 3}
    assert tst_counts(db) == (2, 3)
    db.expire_all()
    assert db.scalar(select(Policy.premium_inr).where(Policy.policy_no == "MOT-2026-900001")) == 15999
    cleanup(db)


def test_dry_run_writes_nothing(db, tmp_path, capsys):
    cleanup(db)
    before = last_audit_id(db)
    f = tmp_path / "c.csv"
    f.write_text(SAMPLE)
    assert main([str(f), "--dry-run"]) == 0
    assert "customers_created=2" in capsys.readouterr().out
    assert tst_counts(db) == (0, 0) and last_audit_id(db) == before


def test_cli_bad_file_exits_nonzero(tmp_path, capsys):
    f = tmp_path / "c.csv"
    f.write_text(HEADER + "\n" + ROWS[0].replace("asha.tester@example.com", "not-an-email") + "\n")
    assert main([str(f)]) == 1
    assert "row 2: email is not valid" in capsys.readouterr().err
