"""The policy engine. Deterministic: same mandate, cart, history and clock give the same decision.

No model output is read here. The agent's reasoning, product descriptions and merchant display names cannot
change an outcome; only the signed policy, the verified merchant directory, the cart's own numbers and the
purchase history can.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from .catalog import Directory, normalise_name
from .models import Cart, Check, Decision, Mandate, Policy, Purchase
from .money import D

COMMITTED = {"captured", "verifying", "awaiting_paypal", "refunded"}
WINDOWS = {"day": timedelta(days=1), "week": timedelta(days=7), "month": timedelta(days=30)}


@dataclass
class Context:
    mandate: Mandate
    cart: Cart
    agent_id: str
    directory: Directory
    history: list[Purchase]
    now: datetime
    signature_ok: bool
    signature_detail: str = ""
    used_nonces: set[str] = field(default_factory=set)
    payments_frozen: bool = False
    allowance_left: Optional[Decimal] = None


def _spent(history: list[Purchase], since: Optional[datetime]) -> Decimal:
    total = Decimal("0")
    for p in history:
        if p.status in COMMITTED and (since is None or p.created_at >= since):
            refunded = sum((D(r["amount"]) for r in p.refunds), Decimal("0"))
            total += p.cart.total - refunded
    return D(total)


def _window_start(period: str, now: datetime, policy: Policy) -> Optional[datetime]:
    if period == "total":
        return None
    return max(now - WINDOWS[period], policy.valid_from)


def evaluate(ctx: Context) -> Decision:
    p = ctx.mandate.policy
    cart = ctx.cart
    checks: list[Check] = []

    def add(rule: str, ok: bool, detail: str, severity: str = "deny") -> None:
        checks.append(Check(rule=rule, ok=bool(ok), severity=severity, detail=detail))

    # --- the mandate itself
    add("kill_switch", not ctx.payments_frozen, "Payments are frozen across Leash." if ctx.payments_frozen else "Payments are not frozen.")
    st = ctx.mandate.status
    add("mandate_active", st == "active",
        "Mandate is active." if st == "active" else f"Mandate is {st.replace('_', ' ')}, so nothing can be bought under it.")
    add("mandate_signature", ctx.signature_ok,
        "Your signature on the mandate verifies." if ctx.signature_ok else f"Mandate signature check failed: {ctx.signature_detail or 'the policy changed after signing'}.")
    add("agent_authorized", ctx.agent_id == ctx.mandate.agent_id,
        f"Agent '{ctx.agent_id}' is the one you authorized." if ctx.agent_id == ctx.mandate.agent_id
        else f"Agent '{ctx.agent_id}' is not authorized; this mandate belongs to '{ctx.mandate.agent_id}'.")
    in_window = p.valid_from <= ctx.now <= p.valid_until
    add("validity", in_window, f"Within the mandate's dates (until {p.valid_until:%d %b %Y})." if in_window else f"Outside the mandate's dates ({p.valid_from:%d %b} to {p.valid_until:%d %b %Y}).")
    if p.time_window:
        local = ctx.now + timedelta(minutes=p.time_window.tz_offset_minutes)
        ok = local.weekday() in p.time_window.days and p.time_window.start_hour <= local.hour < p.time_window.end_hour
        add("time_window", ok, "Inside the allowed shopping hours." if ok else f"Outside the allowed shopping hours (it is {local:%a %H:%M} local).")
    add("currency", cart.currency == p.currency, f"Priced in {cart.currency}.")
    fresh = cart.nonce not in ctx.used_nonces
    add("replay", fresh, "First use of this cart." if fresh else "This exact cart was already submitted once; replays are refused.")

    # --- the cart's own integrity
    items_total = D(sum((i.unit_price * i.quantity for i in cart.items), Decimal("0")))
    sums = items_total == cart.subtotal and D(cart.subtotal + cart.shipping + cart.tax) == cart.total and cart.total > 0
    add("cart_integrity", sums, "Cart totals add up." if sums else f"Cart totals do not add up (items ${items_total}, cart says ${cart.total}).")
    drift = []
    for i in cart.items:
        listed = ctx.directory.products.get(i.product_id)
        if listed is None or listed.merchant_id != cart.merchant_id:
            drift.append(f"{i.name} is not sold by this merchant")
        elif listed.price != i.unit_price:
            drift.append(f"{i.name} costs ${listed.price}, not ${i.unit_price}")
    add("price_check", not drift, "Prices match the merchant's listing." if not drift else "; ".join(drift) + ".")

    # --- who is being paid
    merchant = ctx.directory.merchant(cart.merchant_id)
    if merchant is None or not merchant.verified:
        twins = ctx.directory.lookalikes(merchant) if merchant else [
            m for m in ctx.directory.merchants.values() if m.verified and normalise_name(m.name) == normalise_name(cart.merchant_name)]
        if twins:
            real = twins[0]
            detail = (f"Possible spoof: this store uses the name '{cart.merchant_name}' but its domain {cart.merchant_domain} is not "
                      f"the verified {real.domain}.")
        else:
            detail = f"{cart.merchant_name} ({cart.merchant_domain}) is not a verified merchant."
        add("merchant_verified", False, detail)
    else:
        same = merchant.domain == cart.merchant_domain
        add("merchant_verified", same, f"{merchant.name} is verified ({merchant.domain})." if same
            else f"Cart claims domain {cart.merchant_domain}, but {merchant.name} is verified at {merchant.domain}.")
        if p.merchants_allow:
            ok = merchant.id in p.merchants_allow
            add("merchant_allowed", ok, f"{merchant.name} is on your list." if ok else f"{merchant.name} is not one of the stores you allowed.", p.unknown_merchant)

    # --- what is being bought
    blocked = sorted({i.category for i in cart.items if i.category in p.categories_block})
    add("categories_blocked", not blocked, "No blocked categories." if not blocked else f"You never allow {', '.join(c.replace('_', ' ') for c in blocked)}.")
    if p.categories_allow:
        outside = sorted({i.category for i in cart.items if i.category not in p.categories_allow})
        add("categories_allowed", not outside, "Everything is in the allowed categories." if not outside
            else f"{', '.join(c.replace('_', ' ') for c in outside)} is outside what this mandate covers ({', '.join(c.replace('_', ' ') for c in p.categories_allow)}).")
    subs = [i.name for i in cart.items if i.is_subscription]
    add("subscriptions", p.allow_subscriptions or not subs, "No subscriptions." if not subs else
        ("Subscriptions are allowed." if p.allow_subscriptions else f"'{subs[0]}' is a recurring subscription, which you never allow."))
    if p.max_quantity:
        big = [i for i in cart.items if i.quantity > p.max_quantity]
        add("quantity", not big, f"At most {p.max_quantity} of any item." if not big else f"{big[0].quantity} x {big[0].name} is over your limit of {p.max_quantity}.")

    # --- how much
    if p.per_purchase_max is not None:
        ok = cart.total <= p.per_purchase_max
        add("per_purchase_max", ok, f"${cart.total} is within the ${p.per_purchase_max} per-purchase cap." if ok
            else f"${cart.total} is over your ${p.per_purchase_max} per-purchase cap.")
    if p.budget:
        since = _window_start(p.budget.period, ctx.now, p)
        spent = _spent(ctx.history, since)
        left = D(p.budget.amount - spent)
        ok = cart.total <= left
        add("budget", ok, f"${cart.total} fits the ${left} left of your ${p.budget.amount} budget." if ok
            else f"${cart.total} is more than the ${left} left of your ${p.budget.amount} budget.")
    if ctx.allowance_left is not None:
        ok = cart.total <= ctx.allowance_left
        add("paypal_allowance", ok, f"${ctx.allowance_left} is left on the PayPal allowance." if ok
            else f"Only ${ctx.allowance_left} is left on the PayPal allowance.")

    # --- how often
    done = sorted((h for h in ctx.history if h.status in COMMITTED), key=lambda h: h.created_at)
    if p.min_interval_minutes and done:
        gap = (ctx.now - done[-1].created_at).total_seconds() / 60
        ok = gap >= p.min_interval_minutes
        add("velocity", ok, f"{int(gap)} minutes since the last purchase." if ok
            else f"Only {int(gap)} minutes since the last purchase; you require {p.min_interval_minutes}.")
    if p.per_purchase_max is not None:
        recent = [h for h in done if h.cart.merchant_id == cart.merchant_id and (ctx.now - h.created_at) <= timedelta(minutes=p.split_window_minutes)]
        combined = D(sum((h.cart.total for h in recent), Decimal("0")) + cart.total)
        ok = not recent or combined <= p.per_purchase_max
        add("split_purchase", ok, "Not a split purchase." if ok else
            f"Looks like a split purchase: ${combined} at {cart.merchant_name} within {p.split_window_minutes} minutes, over the ${p.per_purchase_max} cap.")
    if p.max_purchases:
        since = _window_start(p.max_purchases.period, ctx.now, p)
        n = len([h for h in done if since is None or h.created_at >= since])
        ok = n < p.max_purchases.count
        add("purchase_count", ok, f"Order {n + 1} of {p.max_purchases.count} this {p.max_purchases.period}." if ok
            else f"This would be order {n + 1}; you asked to approve anything past {p.max_purchases.count} per {p.max_purchases.period}.", "ask")
    if p.ask_above is not None:
        ok = p.ask_above > 0 and cart.total <= p.ask_above
        add("ask_above", ok, f"${cart.total} is under your ${p.ask_above} ask-first line." if ok else
            ("You asked to approve every purchase." if p.ask_above == 0 else f"${cart.total} is over ${p.ask_above}, so you asked to approve it."), "ask")

    deny = [c for c in checks if not c.ok and c.severity == "deny"]
    ask = [c for c in checks if not c.ok and c.severity == "ask"]
    if deny:
        more = f" ({len(deny) - 1} more rule{'s' if len(deny) > 2 else ''} also failed.)" if len(deny) > 1 else ""
        return Decision(outcome="deny", checks=checks, summary=f"Blocked. {deny[0].detail}{more}")
    if ask:
        return Decision(outcome="ask", checks=checks, summary=f"Needs your OK. {ask[0].detail}")
    return Decision(outcome="allow", checks=checks, summary=f"Within every rule of '{p.title}'.")
