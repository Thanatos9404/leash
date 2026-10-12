"""Domain models.

Terminology loosely follows Google's Agent Payments Protocol (AP2): an *intent mandate* is the user's signed,
human-not-present instruction; a *cart approval* is the user's signature over one specific cart (AP2 calls the
equivalent a cart mandate). Leash does not claim AP2 conformance; the objects are its own.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Period = Literal["day", "week", "month", "total"]
Severity = Literal["deny", "ask"]
Outcome = Literal["allow", "ask", "deny"]


def now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- merchants and products


class Merchant(BaseModel):
    id: str  # Leash's verified merchant id; display names are never trusted on their own
    name: str
    domain: str
    category: str
    verified: bool = True
    paypal_merchant_id: str = ""
    country: str = "US"
    note: str = ""


class Product(BaseModel):
    id: str
    merchant_id: str
    title: str
    description: str
    price: Decimal
    currency: Literal["USD"] = "USD"
    category: str
    url: str = ""
    image: str = ""
    brand: str = ""
    size: str = ""
    is_subscription: bool = False
    in_stock: bool = True
    source: str = "fixture"


# --------------------------------------------------------------------------- mandate policy


class Budget(BaseModel):
    amount: Decimal
    period: Period = "month"


class CountLimit(BaseModel):
    count: int = Field(ge=1, le=1000)
    period: Period = "month"


class TimeWindow(BaseModel):
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])  # 0 = Monday
    start_hour: int = Field(default=0, ge=0, le=23)
    end_hour: int = Field(default=24, ge=1, le=24)
    tz_offset_minutes: int = 330  # demo default: IST


class Policy(BaseModel):
    """The typed, deterministic form of a mandate. Everything the policy engine enforces lives here."""

    title: str
    purpose: str
    currency: Literal["USD"] = "USD"
    per_purchase_max: Optional[Decimal] = None  # hard
    ask_above: Optional[Decimal] = None  # step-up threshold
    budget: Optional[Budget] = None  # hard
    max_purchases: Optional[CountLimit] = None  # step-up when exceeded
    min_interval_minutes: Optional[int] = None  # hard velocity limit
    merchants_allow: list[str] = Field(default_factory=list)  # verified merchant ids; empty = any verified merchant
    unknown_merchant: Severity = "deny"
    categories_allow: list[str] = Field(default_factory=list)  # empty = any category not blocked
    categories_block: list[str] = Field(default_factory=lambda: ["gift_cards"])
    max_quantity: Optional[int] = None
    allow_subscriptions: bool = False
    valid_from: datetime
    valid_until: datetime
    time_window: Optional[TimeWindow] = None
    split_window_minutes: int = 60


MandateStatus = Literal["draft", "signed", "awaiting_funding", "active", "revoked", "expired"]


class Envelope(BaseModel):
    """The PayPal authorization that backs a mandate: the agent can never capture more than this."""

    order_id: Optional[str] = None
    approve_url: Optional[str] = None
    order_status: Optional[str] = None
    authorization_id: Optional[str] = None
    authorization_status: Optional[str] = None
    amount: Decimal = Decimal("0")
    captured: Decimal = Decimal("0")
    expires_at: Optional[str] = None
    voided_at: Optional[datetime] = None


class Mandate(BaseModel):
    id: str
    owner: str
    agent_id: str = "leash-shopper"
    source_text: str
    policy: Policy
    plain_language: list[str]
    policy_hash: str
    compiler: dict[str, Any] = Field(default_factory=dict)
    status: MandateStatus = "draft"
    token: Optional[str] = None  # user-signed compact JWS
    signer: dict[str, Any] = Field(default_factory=dict)
    envelope: Envelope = Field(default_factory=Envelope)
    created_at: datetime = Field(default_factory=now)
    activated_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    revoke_reason: Optional[str] = None
    receipt_head: Optional[str] = None  # hash of the newest receipt in this mandate's chain


# --------------------------------------------------------------------------- carts, decisions, purchases


class CartItem(BaseModel):
    product_id: str
    variant_id: str = ""
    name: str
    quantity: int = Field(ge=1, le=10000)
    unit_price: Decimal
    category: str
    is_subscription: bool = False
    item_url: str = ""


class Cart(BaseModel):
    """Shaped after the PayPalCart object of PayPal's agentic commerce Cart API (items, totals, payment_method)."""

    id: str
    merchant_id: str
    merchant_name: str
    merchant_domain: str
    items: list[CartItem]
    subtotal: Decimal
    shipping: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")
    total: Decimal
    currency: Literal["USD"] = "USD"
    nonce: str
    created_at: datetime = Field(default_factory=now)
    cart_hash: str = ""


class Check(BaseModel):
    rule: str
    ok: bool
    severity: Severity = "deny"
    detail: str


class Decision(BaseModel):
    outcome: Outcome
    checks: list[Check]
    summary: str
    evaluated_at: datetime = Field(default_factory=now)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]


PurchaseStatus = Literal[
    "blocked",  # denied by a hard rule
    "awaiting_approval",  # step-up: the user must approve this cart
    "awaiting_paypal",  # one-off exception: user is approving this exact cart on PayPal
    "verifying",  # merchant is verifying the agent's authority
    "captured",
    "rejected",  # the user said no
    "voided",  # an authorization was released
    "failed",
    "refunded",
]


class Purchase(BaseModel):
    id: str
    mandate_id: str
    agent_id: str
    task: str
    reasoning: str
    agent_ai: str = "rules"
    cart: Cart
    decision: Decision
    status: PurchaseStatus
    approval: dict[str, Any] = Field(default_factory=dict)
    payment: dict[str, Any] = Field(default_factory=dict)
    verification: dict[str, Any] = Field(default_factory=dict)
    receipt: Optional[str] = None  # compact JWS intent receipt
    receipt_hash: Optional[str] = None
    refunds: list[dict[str, Any]] = Field(default_factory=list)
    dispute: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
