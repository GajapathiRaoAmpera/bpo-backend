from datetime import date

from pydantic import BaseModel

from app.models.enums import CallOutcome, Sentiment


class DashboardSummary(BaseModel):
    days: int
    calls: int
    calls_today: int
    connected: int  # calls that reached the customer
    renewals: int  # payment links paid in the window
    collections_inr: int
    ai_contained_pct: float  # connected calls the AI finished without a human
    avg_qa_score: float | None  # out of 5
    open_escalations: int
    renewals_due_30d: int
    outcomes: dict[CallOutcome, int]
    sentiment: dict[Sentiment, int]


class TrendPoint(BaseModel):
    day: date  # IST calendar day
    calls: int
    renewals: int
    collections_inr: int
    positive: int
    neutral: int
    negative: int
