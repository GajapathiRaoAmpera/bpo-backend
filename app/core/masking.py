def mask_phone(phone: str) -> str:
    """'9876543221' -> '98XXXXXX21' (keeps first 2 and last 2 digits)."""
    digits = "".join(c for c in phone if c.isdigit())[-10:]
    if len(digits) < 4:
        return "X" * len(digits)
    return digits[:2] + "X" * (len(digits) - 4) + digits[-2:]


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}{'*' * max(len(local) - 1, 3)}@{domain}"
