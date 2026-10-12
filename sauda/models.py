"""Domain models shared by the agents, the policy engine and the PayPal executor."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .money import D


def now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- catalog


class Tier(BaseModel):
    min_kg: int
    discount_pct: Decimal  # 0.05 = 5% off list


class Listing(BaseModel):
    product: str
    list_price: Decimal  # USD per kg, FOB
    floor_price: Decimal  # private to the seller agent; never sent to the buyer side
    stock_kg: int
    min_order_kg: int = 10
    tiers: list[Tier] = Field(default_factory=list)

    def quote_price(self, qty: int) -> Decimal:
        disc = Decimal("0")
        for t in self.tiers:
            if qty >= t.min_kg:
                disc = max(disc, t.discount_pct)
        return D(self.list_price * (1 - disc))


class Seller(BaseModel):
    id: str
    name: str
    region: str
    state: str
    language: Literal["hi", "mr", "ta", "ml", "en"]
    manager: str
    members: int
    certs: list[str]
    export_ready: bool = True
    lead_time_days: int
    min_advance_pct: Decimal
    style: Literal["firm", "flexible"] = "flexible"
    paypal_email: str
    listings: list[Listing]

    def listing(self, product: str) -> Optional[Listing]:
        return next((l for l in self.listings if l.product == product), None)

    def public(self) -> dict[str, Any]:
        """Everything the buyer side may see. Floor prices are stripped."""
        data = self.model_dump(mode="json")
        for l in data["listings"]:
            l.pop("floor_price", None)
        data.pop("style", None)
        return data


class Product(BaseModel):
    key: str
    name: str
    category: str
    synonyms: list[str]
    local: dict[str, str]


# --------------------------------------------------------------------------- mandate


class MandateItem(BaseModel):
    product: str
    qty_kg: int = Field(gt=0, le=100000)
    max_unit_price: Optional[Decimal] = None


class Mandate(BaseModel):
    buyer_name: str = "Buyer"
    buyer_business: str = ""
    buyer_email: str = ""
    currency: Literal["USD"] = "USD"
    items: list[MandateItem]
    budget_total: Decimal
    max_sellers: int = Field(default=3, ge=1, le=10)
    max_per_seller: Optional[Decimal] = None
    required_certs: list[str] = Field(default_factory=list)
    max_lead_time_days: Optional[int] = None
    advance_pct: Decimal = Decimal("0.30")
    auto_release_limit: Decimal = Decimal("150.00")
    expires_at: datetime
    notes: str = ""
    signed_by: Optional[str] = None
    signed_at: Optional[datetime] = None
    signature: Optional[str] = None

    def signable(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude={"signature"})
        return data


# --------------------------------------------------------------------------- negotiation


class Offer(BaseModel):
    round: int
    side: Literal["buyer", "seller"]
    action: Literal["rfq", "quote", "counter", "accept", "reject", "walk_away"]
    unit_price: Optional[Decimal] = None
    qty_kg: int
    message: str
    message_local: Optional[str] = None
    lang: Optional[str] = None
    ai: str = "rules"
    guardrail: Optional[str] = None
    at: datetime = Field(default_factory=now)


class Thread(BaseModel):
    seller_id: str
    seller_name: str
    product: str
    qty_kg: int
    list_price: Decimal
    status: Literal["open", "agreed", "failed", "excluded"] = "open"
    agreed_price: Optional[Decimal] = None
    exclusion: Optional[str] = None
    offers: list[Offer] = Field(default_factory=list)


# --------------------------------------------------------------------------- deal


class DealLine(BaseModel):
    line_id: str
    seller_id: str
    seller_name: str
    product: str
    product_name: str
    qty_kg: int
    unit_price: Decimal
    list_price: Decimal
    value: Decimal
    advance: Decimal = Decimal("0")  # this line's slice of the PayPal order (exact cents)
    status: Literal["pending", "dispatched", "short", "cancelled"] = "pending"
    shipped_kg: Optional[int] = None
    tracking: Optional[str] = None
    carrier: Optional[str] = None
    released_advance: Decimal = Decimal("0")  # part of advance attributable to shipped goods
    refunded_advance: Decimal = Decimal("0")
    balance: Decimal = Decimal("0")  # invoiced balance for shipped goods
    paid_out: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")


class PolicyCheck(BaseModel):
    name: str
    ok: bool
    detail: str


ActionKind = Literal["CREATE_ORDER", "CAPTURE_ORDER", "ADD_TRACKING", "PAYOUT", "REFUND", "ISSUE_INVOICE"]
ActionStatus = Literal["needs_approval", "approved", "executing", "executed", "failed", "rejected", "blocked"]


class Action(BaseModel):
    id: str
    kind: ActionKind
    amount: Decimal = Decimal("0")
    currency: str = "USD"
    summary: str
    reason: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    policy: list[PolicyCheck] = Field(default_factory=list)
    requires_human: bool = True
    approval_rule: str = ""
    status: ActionStatus = "needs_approval"
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    idempotency_key: str
    result: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=now)


class PayPalState(BaseModel):
    order_id: Optional[str] = None
    order_status: Optional[str] = None
    approve_url: Optional[str] = None
    payer_id: Optional[str] = None
    capture_id: Optional[str] = None
    capture_status: Optional[str] = None
    captured: Decimal = Decimal("0")
    paypal_fee: Optional[Decimal] = None
    refunds: list[dict[str, Any]] = Field(default_factory=list)
    payout_batches: list[dict[str, Any]] = Field(default_factory=list)
    invoice_id: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_status: Optional[str] = None
    invoice_url: Optional[str] = None
    invoice_amount: Decimal = Decimal("0")
    invoice_paid: Decimal = Decimal("0")
    trackers: list[dict[str, Any]] = Field(default_factory=list)


DealStatus = Literal[
    "mandate_draft",
    "mandate_signed",
    "negotiating",
    "proposed",
    "awaiting_buyer",
    "in_fulfilment",
    "invoiced",
    "settling",
    "settled",
    "failed",
    "cancelled",
]


class Notice(BaseModel):
    """A message to an FPO manager, in their language with an English gloss."""

    seller_id: str
    kind: str
    lang: str
    text_local: str
    text_en: str
    ai: str = "template"
    at: datetime = Field(default_factory=now)


class Deal(BaseModel):
    id: str
    created_at: datetime = Field(default_factory=now)
    status: DealStatus = "mandate_draft"
    brief: str = ""
    mandate: Mandate
    mandate_ai: str = "rules"
    mandate_warnings: list[str] = Field(default_factory=list)
    threads: list[Thread] = Field(default_factory=list)
    lines: list[DealLine] = Field(default_factory=list)
    rationale: str = ""
    rationale_ai: str = "rules"
    risks: list[str] = Field(default_factory=list)
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    totals: dict[str, Any] = Field(default_factory=dict)
    paypal: PayPalState = Field(default_factory=PayPalState)
    actions: list[Action] = Field(default_factory=list)
    notices: list[Notice] = Field(default_factory=list)
    error: Optional[str] = None

    def action(self, action_id: str) -> Action:
        for a in self.actions:
            if a.id == action_id:
                return a
        raise KeyError(action_id)

    def lines_for(self, seller_id: str) -> list[DealLine]:
        return [l for l in self.lines if l.seller_id == seller_id]

    @property
    def advance_total(self) -> Decimal:
        return D(sum((l.advance for l in self.lines), Decimal("0")))
