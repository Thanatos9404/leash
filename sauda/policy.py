"""Deterministic policy engine. Every PayPal action passes through here before it can execute.

The engine answers two questions for an action:
  1. Is it allowed at all? (all checks must pass, otherwise the action is BLOCKED)
  2. Who must approve it? (a human, or the signed mandate's auto-release rule)
No model output is consulted here: amounts are recomputed from agreed prices and reported quantities.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal

from .catalog import Catalog
from .config import Settings
from .mandate import verify
from .models import Action, Deal, PolicyCheck
from .money import D

TRACKING_RE = re.compile(r"^[A-Z0-9]{8,30}$")
MONEY_MOVING = {"CREATE_ORDER", "PAYOUT", "REFUND", "ISSUE_INVOICE", "CAPTURE_ORDER"}


def net_of_fee(amount: Decimal, fee_pct: Decimal) -> Decimal:
    return D(amount * (1 - fee_pct))


class PolicyEngine:
    def __init__(self, settings: Settings, catalog: Catalog) -> None:
        self.settings = settings
        self.catalog = catalog
        self.fee_pct = Decimal(settings.platform_fee_pct)

    def evaluate(self, deal: Deal, action: Action, route: bool = True) -> Action:
        """Run all checks. With route=False (the pre-execution re-check) approval state is left untouched."""
        checks: list[PolicyCheck] = []
        add = lambda name, ok, detail: checks.append(PolicyCheck(name=name, ok=bool(ok), detail=detail))  # noqa: E731
        m = deal.mandate

        add("sandbox_only", self.settings.paypal_env == "sandbox", "PayPal environment is the sandbox")
        if action.kind in MONEY_MOVING:
            add("kill_switch_off", not self.settings.payments_frozen, "Payments are not frozen (SAUDA_PAYMENTS_FROZEN)")
        add("mandate_signed", verify(m, self.settings.secret), "Mandate signature is valid and unchanged since signing")
        add("mandate_active", m.expires_at > datetime.now(timezone.utc), f"Mandate valid until {m.expires_at:%Y-%m-%d}")
        if action.kind != "ADD_TRACKING":
            add("amount_valid", action.amount > 0 and action.amount == D(action.amount), f"Amount ${action.amount} is positive with cents precision")
            add("currency", action.currency == m.currency == "USD", "Currency matches the mandate (USD)")

        getattr(self, f"_check_{action.kind.lower()}")(deal, action, add)

        action.policy = checks
        if not all(c.ok for c in checks):
            action.status = "blocked"
            action.requires_human = True
            action.approval_rule = "Blocked by policy: " + "; ".join(c.detail for c in checks if not c.ok)
            return action
        if route:
            self._route(deal, action)
        return action

    # ------------------------------------------------------------------ approval routing

    def _route(self, deal: Deal, action: Action) -> None:
        m = deal.mandate
        if action.kind == "CREATE_ORDER":
            action.requires_human = True
            action.approval_rule = "Buyer approves every collection here, then again on PayPal's own approval page."
        elif action.kind == "CAPTURE_ORDER":
            action.requires_human = False
            action.approval_rule = "Buyer already approved this exact order on PayPal."
        elif action.kind == "ADD_TRACKING":
            action.requires_human = False
            action.approval_rule = "Non-monetary: attaches dispatch tracking to the PayPal capture."
        elif action.kind == "ISSUE_INVOICE":
            action.requires_human = False
            action.approval_rule = "An invoice is a request: the buyer still chooses to pay it on PayPal."
        elif action.kind == "PAYOUT" and action.payload.get("stage") == "balance":
            action.requires_human = True
            action.approval_rule = "Buyer confirms receipt before the balance is released to the FPOs."
        else:  # dispatch PAYOUT or REFUND
            auto_so_far = sum(
                (a.amount for a in deal.actions if a.approved_by == "mandate" and a.status == "executed" and a.kind in ("PAYOUT", "REFUND")),
                Decimal("0"),
            )
            cap = Decimal(self.settings.auto_release_daily_cap)
            if action.amount <= m.auto_release_limit and auto_so_far + action.amount <= cap:
                action.requires_human = False
                action.approval_rule = (
                    f"Auto-approved by the signed mandate: ${action.amount} is within the ${m.auto_release_limit} "
                    "auto-release limit and backed by verified dispatch evidence."
                )
            elif action.amount > m.auto_release_limit:
                action.requires_human = True
                action.approval_rule = f"${action.amount} exceeds the mandate's ${m.auto_release_limit} auto-release limit."
            else:
                action.requires_human = True
                action.approval_rule = f"Auto-release cap of ${cap} per deal reached; a human must approve."
        action.status = "needs_approval" if action.requires_human else "approved"
        if not action.requires_human:
            action.approved_by = "mandate" if action.kind in ("PAYOUT", "REFUND") else "policy"

    # ------------------------------------------------------------------ per-kind checks

    def _check_create_order(self, deal: Deal, a: Action, add) -> None:
        m = deal.mandate
        subtotal = D(sum((l.value for l in deal.lines), Decimal("0")))
        add("within_budget", subtotal <= m.budget_total, f"Basket ${subtotal} within budget ${m.budget_total}")
        sellers = {l.seller_id for l in deal.lines}
        add("seller_count", len(sellers) <= m.max_sellers, f"{len(sellers)} sellers, mandate max {m.max_sellers}")
        caps = {it.product: it.max_unit_price for it in m.items}
        over = [l for l in deal.lines if caps.get(l.product) is not None and l.unit_price > caps[l.product]]
        add("unit_price_caps", not over, "Every unit price within the mandate's per-kg caps" if not over else f"{over[0].product_name} at ${over[0].unit_price}/kg is above the cap")
        if m.max_per_seller is not None:
            worst = max((D(sum((l.value for l in deal.lines if l.seller_id == s), Decimal("0"))) for s in sellers), default=Decimal("0"))
            add("per_seller_cap", worst <= m.max_per_seller, f"Largest seller share ${worst} within ${m.max_per_seller}")
        bad = []
        for sid in sellers:
            s = self.catalog.seller(sid)
            if any(c not in s.certs for c in m.required_certs):
                bad.append(f"{s.name} lacks certification")
            if m.max_lead_time_days is not None and s.lead_time_days > m.max_lead_time_days:
                bad.append(f"{s.name} lead time")
            if s.min_advance_pct > m.advance_pct:
                bad.append(f"{s.name} advance terms")
        add("seller_terms", not bad, "Sellers meet certification, lead-time and advance terms" if not bad else "; ".join(bad))
        agreed = {(t.seller_id, t.product): t.agreed_price for t in deal.threads if t.status == "agreed"}
        drift = [l for l in deal.lines if agreed.get((l.seller_id, l.product)) != l.unit_price]
        add("price_integrity", not drift, "Line prices equal the prices agreed in negotiation")
        want = {it.product: it.qty_kg for it in m.items}
        got: dict[str, int] = {}
        for l in deal.lines:
            got[l.product] = got.get(l.product, 0) + l.qty_kg
        add("qty_within_mandate", all(got.get(p, 0) <= q for p, q in want.items()) and set(got) <= set(want), "Quantities do not exceed the mandate")
        expected = D(subtotal * m.advance_pct)
        add("amount_integrity", a.amount == expected and D(sum((l.advance for l in deal.lines), Decimal("0"))) == expected,
            f"Order amount ${a.amount} = {int(m.advance_pct * 100)}% of ${subtotal}, split exactly across lines")
        dup = any(x.kind == "CREATE_ORDER" and x.status == "executed" and x.id != a.id for x in deal.actions)
        add("not_duplicate", not dup, "No order already created for this deal")

    def _check_capture_order(self, deal: Deal, a: Action, add) -> None:
        add("payer_approved", deal.paypal.order_status == "APPROVED", f"PayPal order status is {deal.paypal.order_status}")
        add("amount_integrity", a.amount == deal.advance_total, f"Capture ${a.amount} equals the approved order amount")

    def _check_add_tracking(self, deal: Deal, a: Action, add) -> None:
        add("captured", bool(deal.paypal.capture_id), "Tracking attaches to an existing capture")
        add("tracking_format", bool(TRACKING_RE.match(a.payload.get("tracking_number", ""))), "Tracking number looks valid")

    def _check_refund(self, deal: Deal, a: Action, add) -> None:
        refunded = D(sum((D(r["amount"]) for r in deal.paypal.refunds), Decimal("0")))
        remaining = D(deal.paypal.captured - refunded)
        add("within_captured", a.amount <= remaining, f"Refund ${a.amount} within un-refunded capture ${remaining}")
        lines = {l.line_id: l for l in deal.lines}
        expected = Decimal("0")
        for lid, amt in a.payload.get("lines", {}).items():
            l = lines.get(lid)
            if l is None or l.shipped_kg is None:
                add("line_resolved", False, f"Line {lid} has no reported dispatch")
                return
            expected += D(l.advance - l.released_advance - l.refunded_advance)
        add("shortfall_integrity", a.amount == D(expected), f"Refund equals the unshipped share of the advance (${D(expected)})")

    def _check_payout(self, deal: Deal, a: Action, add) -> None:
        lines = {l.line_id: l for l in deal.lines}
        stage = a.payload.get("stage")
        items = a.payload.get("items", [])
        add("has_items", bool(items), f"{len(items)} payout item(s)")
        payees_ok = all(it["receiver"] == self.catalog.seller(it["seller_id"]).paypal_email for it in items)
        add("payee_registered", payees_ok, "Every receiver is the FPO's registered PayPal account (the agent cannot redirect funds)")
        total = D(sum((D(it["amount"]) for it in items), Decimal("0")))
        add("items_sum", total == a.amount, f"Items sum to ${total}")
        problems = []
        for it in items:
            for lid, amt in it["lines"].items():
                l = lines.get(lid)
                if l is None or l.seller_id != it["seller_id"]:
                    problems.append(f"{lid}: wrong seller")
                    continue
                if stage == "dispatch":
                    entitled = net_of_fee(l.released_advance, self.fee_pct)
                    if l.status not in ("dispatched", "short") or not (l.tracking and TRACKING_RE.match(l.tracking)):
                        problems.append(f"{lid}: no verified dispatch")
                else:
                    entitled = net_of_fee(l.released_advance, self.fee_pct) + net_of_fee(l.balance, self.fee_pct)
                if D(l.paid_out + D(amt)) > entitled:
                    problems.append(f"{lid}: ${D(l.paid_out + D(amt))} exceeds entitlement ${entitled}")
        add("entitlement", not problems, "Each release is within what the FPO has earned so far" if not problems else "; ".join(problems))
        if stage == "balance":
            add("invoice_paid", deal.paypal.invoice_status in ("PAID", "MARKED_AS_PAID") and deal.paypal.invoice_paid >= deal.paypal.invoice_amount,
                f"Balance invoice status {deal.paypal.invoice_status}")
        collected = D(deal.paypal.captured + deal.paypal.invoice_paid)
        refunded = D(sum((D(r["amount"]) for r in deal.paypal.refunds), Decimal("0")))
        paid = D(sum((l.paid_out for l in deal.lines), Decimal("0")))
        add("funds_held", D(paid + a.amount) <= D(collected - refunded),
            f"Releases ${D(paid + a.amount)} stay within funds held ${D(collected - refunded)}")

    def _check_issue_invoice(self, deal: Deal, a: Action, add) -> None:
        pending = [l for l in deal.lines if l.status == "pending"]
        add("all_dispatched", not pending, "Every line has a dispatch report" if not pending else f"{len(pending)} line(s) not yet dispatched")
        expected = D(sum((l.balance for l in deal.lines), Decimal("0")))
        add("amount_integrity", a.amount == expected, f"Invoice ${a.amount} equals balance on shipped goods ${expected}")
        add("recipient", bool(deal.mandate.buyer_email), "Buyer email on the mandate (needed to send the invoice)")
        dup = any(x.kind == "ISSUE_INVOICE" and x.status == "executed" and x.id != a.id for x in deal.actions)
        add("not_duplicate", not dup, "No balance invoice already issued")
