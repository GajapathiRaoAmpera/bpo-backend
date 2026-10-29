from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")


def format_inr(amount: int) -> str:
    """125000 -> '₹1,25,000' (Indian digit grouping)."""
    sign, s = ("-" if amount < 0 else ""), str(abs(int(amount)))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        s = ",".join([head, *groups, tail]) if head else ",".join([*groups, tail])
    return f"{sign}₹{s}"


def format_date(d: date | datetime) -> str:
    """'29 Oct 2026' (datetimes are shown in IST)."""
    if isinstance(d, datetime):
        d = d.astimezone(IST)
    return f"{d.day} {d:%b %Y}"


assert format_inr(125000) == "₹1,25,000"
assert format_inr(38_42_500) == "₹38,42,500"
assert format_inr(999) == "₹999" and format_inr(1000) == "₹1,000"
assert format_inr(1_00_00_000) == "₹1,00,00,000"
