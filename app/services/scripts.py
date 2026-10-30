import difflib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Script
from app.models.enums import ApprovalStatus, CallMode, Speaker


def active_script(db: Session, mode: CallMode) -> Script | None:
    """Maker-checker: the bot only ever runs the most recently APPROVED script."""
    return db.scalar(select(Script)
                     .where(Script.mode == mode, Script.status == ApprovalStatus.approved)
                     .order_by(Script.reviewed_at.desc(), Script.version.desc())
                     .limit(1))


def precheck(prompt: str) -> dict[str, bool]:
    """Keyword pre-check of a prompt against the compliance checklist. A hint for the maker and
    checker only; the checker's own ticks decide approval.
    shortcut: keyword matching, upgrade to an LLM rubric if prompts stop following the house rules wording."""
    p = prompt.lower()
    return {
        "ai_disclosure": ("automated" in p or "ai assistant" in p) and "record" in p,
        "verification_before_details": "verify_customer" in p,
        "figures_from_tools_only": "figure" in p and "tool" in p,
        "no_mis_selling": "mis-sell" in p or "no pressure" in p,
        "escalation_path": "escalate_to_human" in p,
    }


def side_by_side(old: str, new: str) -> list[dict]:
    """Line diff for the approvals screen: rows of (tag, left, right)."""
    a, b = old.splitlines(), new.splitlines()
    rows: list[dict] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        left, right = a[i1:i2], b[j1:j2]
        for k in range(max(len(left), len(right))):
            rows.append({"tag": tag, "left": left[k] if k < len(left) else None,
                         "right": right[k] if k < len(right) else None})
    return rows


def simulate_test_call(prompt: str, customer_says: str) -> dict:
    """Bot studio test call. Simulated: turns are rule-generated from the prompt's pre-check,
    no model is called and no call record is created (draft scripts never reach the bot)."""
    checks = precheck(prompt)
    opening = ("Hello, this is Vani, an automated AI assistant from AssureVoice Insurance. "
               "This call is recorded for quality and compliance."
               if checks["ai_disclosure"] else "Hello, this is AssureVoice Insurance.")
    reply = ("I can help with that. To protect your details, please share your policy number and date of birth."
             if checks["verification_before_details"] else "Sure, let me pull up your policy details.")
    return {"checks": checks, "simulated": True,
            "note": "Simulated text preview: replies follow rules derived from this draft, not the live voice model. "
                    "No call is placed and nothing is saved.",
            "turns": [{"speaker": Speaker.bot, "text": f"{opening} How can I help you today?"},
                      {"speaker": Speaker.customer, "text": customer_says},
                      {"speaker": Speaker.bot, "text": reply}]}
