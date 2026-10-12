"""The deal state machine: mandate -> negotiation -> proposal -> approval -> PayPal execution -> settlement.

    mandate_draft -> mandate_signed -> negotiating -> proposed -> awaiting_buyer -> in_fulfilment
        -> invoiced -> settling -> settled

Agents propose; the policy engine decides whether an action is allowed and who must approve it; only then does
the executor call PayPal, with an idempotency key derived from the action. Every step is written to the
hash-chained ledger.
"""

from __future__ import annotations

import asyncio
import random
import re
import string
from decimal import Decimal
from typing import Any, Optional

from .catalog import Catalog
from .config import Settings
from .dispatch import parse_dispatch
from .i18n import render
from .llm import LLM
from .mandate import apply_edits, draft_mandate, sign
from .models import Action, Deal, DealLine, Notice, Offer, Thread, now
from .money import D, allocate, fmt
from .negotiation import negotiate
from .optimizer import choose
from .paypal import PayPalAPI, PayPalError, approve_link, invoice_id_from
from .policy import TRACKING_RE, PolicyEngine, net_of_fee
from .store import Store

FINAL_BATCH = {"SUCCESS", "DENIED", "CANCELED"}
FAILED_ITEM = {"FAILED", "RETURNED", "BLOCKED", "REFUNDED", "REVERSED", "DENIED"}


def _rid(n: int = 6) -> str:
    return "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(n))


class DealError(RuntimeError):
    pass


class Engine:
    def __init__(self, settings: Settings, store: Store, paypal: PayPalAPI, llm: LLM, catalog: Catalog) -> None:
        self.settings = settings
        self.store = store
        self.paypal = paypal
        self.llm = llm
        self.catalog = catalog
        self.policy = PolicyEngine(settings, catalog)
        self._locks: dict[str, asyncio.Lock] = {}
        self.live_threads: dict[str, dict[tuple[str, str], Thread]] = {}
        self.tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ helpers

    def lock(self, deal_id: str) -> asyncio.Lock:
        return self._locks.setdefault(deal_id, asyncio.Lock())

    def get(self, deal_id: str) -> Deal:
        deal = self.store.get_deal(deal_id)
        if not deal:
            raise DealError(f"unknown deal {deal_id}")
        return deal

    def log(self, deal: Optional[Deal], actor: str, kind: str, title: str, data: Optional[dict] = None) -> dict:
        return self.store.append(deal.id if deal else None, actor, kind, title, data or {})

    def save(self, deal: Deal) -> None:
        self.store.save_deal(deal)

    def _new_action(self, deal: Deal, kind: str, amount: Decimal, summary: str, **kw: Any) -> Action:
        aid = f"A{len(deal.actions) + 1}"
        a = Action(
            id=aid, kind=kind, amount=D(amount), summary=summary,
            idempotency_key=f"sauda-{deal.id}-{aid}-{kind.lower()}", **kw,
        )
        deal.actions.append(a)
        return a

    def spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)
        return t

    # ------------------------------------------------------------------ 1. mandate

    async def create_deal(self, brief: str) -> Deal:
        brief = brief.strip()[:4000]
        if not brief:
            raise DealError("brief is empty")
        mandate, ai, warnings = await draft_mandate(brief, self.catalog, self.llm, buyer_email=self.settings.buyer_email)
        if not mandate.buyer_email and self.paypal.mode == "fixture":
            mandate.buyer_email = "anita.shah@sauda.example"
        deal = Deal(id="SD-" + _rid(), brief=brief, mandate=mandate, mandate_ai=ai, mandate_warnings=warnings)
        self.save(deal)
        self.log(deal, "agent:buyer", "mandate.drafted", "Drafted a spending mandate from the buyer's brief",
                 {"ai": ai, "warnings": warnings, "mandate": mandate.model_dump(mode="json")})
        return deal

    async def sign_mandate(self, deal_id: str, edits: Optional[dict], signer: str) -> Deal:
        async with self.lock(deal_id):
            deal = self.get(deal_id)
            if deal.status not in ("mandate_draft", "mandate_signed"):
                raise DealError("mandate can only be edited before negotiation starts")
            m = apply_edits(deal.mandate, edits, self.catalog)
            if not m.items:
                raise DealError("mandate has no items")
            deal.mandate = sign(m, self.settings.secret, signer)
            deal.status = "mandate_signed"
            self.save(deal)
            self.log(deal, f"human:{signer}", "mandate.signed", "Buyer reviewed and signed the mandate",
                     {"signature": deal.mandate.signature[:16] + "...", "mandate": deal.mandate.model_dump(mode="json")})
            return deal

    # ------------------------------------------------------------------ 2. negotiation

    async def start_negotiation(self, deal_id: str) -> Deal:
        async with self.lock(deal_id):
            deal = self.get(deal_id)
            if deal.status != "mandate_signed":
                raise DealError(f"cannot negotiate from status {deal.status}")
            deal.status = "negotiating"
            self.save(deal)
            self.log(deal, "agent:buyer", "negotiation.started", "Buyer agent opened negotiations with FPO agents")
        self.live_threads[deal_id] = {}
        self.spawn(self._negotiate(deal_id))
        return deal

    async def _negotiate(self, deal_id: str) -> None:
        deal = self.get(deal_id)
        live = self.live_threads.setdefault(deal_id, {})

        async def on_thread(t: Thread) -> None:
            live[(t.seller_id, t.product)] = t
            self.log(deal, "agent:buyer", "negotiation.excluded", f"Skipped {t.seller_name} for {self.catalog.product_name(t.product)}",
                     {"seller": t.seller_id, "product": t.product, "reason": t.exclusion})

        async def on_offer(t: Thread, o: Offer) -> None:
            live[(t.seller_id, t.product)] = t
            actor = "agent:buyer" if o.side == "buyer" else f"agent:{t.seller_id}"
            price = f" at ${o.unit_price}/kg" if o.unit_price is not None else ""
            self.log(deal, actor, f"negotiation.{o.action}", f"{t.seller_name}: {o.side} {o.action}{price}",
                     {"seller": t.seller_id, "product": t.product, "round": o.round, "unit_price": o.unit_price,
                      "message": o.message, "message_local": o.message_local, "lang": o.lang, "ai": o.ai, "guardrail": o.guardrail})

        try:
            threads, _ = await negotiate(deal.mandate, self.catalog, self.llm, on_offer, on_thread)
            await self._propose(deal_id, threads)
        except Exception as e:  # keep the deal inspectable instead of stuck in "negotiating"
            async with self.lock(deal_id):
                deal = self.get(deal_id)
                deal.status, deal.error = "failed", f"negotiation failed: {e}"
                self.save(deal)
                self.log(deal, "system", "negotiation.error", "Negotiation failed", {"error": str(e)})
        finally:
            self.live_threads.pop(deal_id, None)

    async def _propose(self, deal_id: str, threads: list[Thread]) -> None:
        async with self.lock(deal_id):
            deal = self.get(deal_id)
            deal.threads = threads
            m = deal.mandate
            plan, alternatives = choose(m, threads, self.catalog)
            if plan is None:
                deal.status, deal.error = "failed", "No seller agreed to terms inside the mandate."
                self.save(deal)
                self.log(deal, "agent:buyer", "proposal.none", deal.error)
                return
            lines: list[DealLine] = []
            for i, p in enumerate(plan.picks, start=1):
                s = self.catalog.seller(p.seller_id)
                lines.append(DealLine(
                    line_id=f"L{i}", seller_id=s.id, seller_name=s.name, product=p.product,
                    product_name=self.catalog.product_name(p.product), qty_kg=p.qty_kg, unit_price=p.unit_price,
                    list_price=p.list_price, value=p.value,
                ))
            subtotal = D(sum((l.value for l in lines), Decimal("0")))
            advance = D(subtotal * m.advance_pct)
            for l, part in zip(lines, allocate(advance, [l.value for l in lines])):
                l.advance = part
            deal.lines = lines
            deal.alternatives = alternatives
            quotes = {(t.seller_id, t.product): next((o.unit_price for o in t.offers if o.action == "quote"), None) for t in threads}
            list_total = D(sum((l.list_price * l.qty_kg for l in lines), Decimal("0")))
            quote_total = D(sum(((quotes.get((l.seller_id, l.product)) or l.list_price) * l.qty_kg for l in lines), Decimal("0")))
            members = sum(self.catalog.seller(s).members for s in {l.seller_id for l in lines})
            deal.totals = {
                "subtotal": str(subtotal), "list_total": str(list_total), "quote_total": str(quote_total),
                "savings_vs_list": str(D(list_total - subtotal)), "savings_vs_quotes": str(D(quote_total - subtotal)),
                "advance": str(advance), "balance": str(D(subtotal - advance)), "advance_pct": str(m.advance_pct),
                "budget": str(m.budget_total), "budget_left": str(D(m.budget_total - subtotal)),
                "sellers": len({l.seller_id for l in lines}), "farmer_members": members,
                "platform_fee_pct": self.settings.platform_fee_pct,
            }
            deal.rationale, deal.risks, deal.rationale_ai = await self._rationale(deal, plan.notes)
            body = self._order_body(deal)
            a = self._new_action(
                deal, "CREATE_ORDER", advance,
                f"Collect the {int(m.advance_pct * 100)}% advance (${advance}) from {m.buyer_business or m.buyer_name} with PayPal Checkout",
                reason=deal.rationale, payload={"request": body},
            )
            self.policy.evaluate(deal, a)
            deal.status = "proposed"
            self.save(deal)
            self.log(deal, "agent:buyer", "proposal.ready", f"Proposed basket: {len(lines)} lines, ${subtotal}",
                     {"totals": deal.totals, "rationale_ai": deal.rationale_ai, "action": a.id, "policy": [c.model_dump() for c in a.policy]})

    async def _rationale(self, deal: Deal, notes: list[str]) -> tuple[str, list[str], str]:
        t = deal.totals
        lines_txt = "; ".join(f"{l.qty_kg} kg {l.product_name} from {l.seller_name} at ${l.unit_price}/kg (list ${l.list_price})" for l in deal.lines)
        excluded = "; ".join(f"{x.seller_name} ({self.catalog.product_name(x.product)}): {x.exclusion}" for x in deal.threads if x.status == "excluded")
        failed = "; ".join(f"{x.seller_name} ({self.catalog.product_name(x.product)})" for x in deal.threads if x.status == "failed")
        alts = "; ".join(f"{', '.join(a['sellers'])} ${a['cost']} rejected: {a['rejected_because']}" for a in deal.alternatives)
        risks: list[str] = list(notes)
        single = [it.product for it in deal.mandate.items if len({x.seller_id for x in deal.threads if x.product == it.product and x.status == "agreed"}) == 1]
        for p in single:
            risks.append(f"Single source for {self.catalog.product_name(p).lower()}: no backup seller agreed terms.")
        lead = max((self.catalog.seller(l.seller_id).lead_time_days for l in deal.lines), default=0)
        fallback = (
            f"This basket costs ${t['subtotal']} against a ${t['budget']} budget, ${t['savings_vs_quotes']} below the sellers' "
            f"opening quotes and ${t['savings_vs_list']} below list. It uses {t['sellers']} FPOs representing "
            f"{t['farmer_members']:,} member farmers, all meeting the mandate's certification terms and dispatching within {lead} days. "
            f"You pay {int(Decimal(t['advance_pct']) * 100)}% (${t['advance']}) now through PayPal; each FPO is paid its share "
            "the moment it proves dispatch, and the balance is billed by PayPal invoice once everything has shipped."
        )
        reply = await self.llm.json(
            "You are the buyer's purchasing agent writing a short decision memo for the human who must approve a "
            "payment. Be factual, specific and plain. Do not invent numbers: use only the figures given. "
            'Return JSON: {"rationale": string (max 90 words), "risks": [string] (0-3 items, max 20 words each)}.',
            f"Basket: {lines_txt}.\nTotals: {t}.\nExcluded sellers: {excluded or 'none'}.\nNo agreement: {failed or 'none'}.\n"
            f"Alternatives considered: {alts or 'none'}.\nKnown risks: {'; '.join(risks) or 'none'}.",
            purpose="rationale", max_tokens=400, temperature=0.3,
        )
        if reply and reply.get("rationale"):
            seen = {r.lower()[:22] for r in risks}
            merged = list(risks)
            for r in (str(x)[:160] for x in (reply.get("risks") or [])[:3]):
                key = r.lower()[:22]
                if key not in seen and not ("single source" in key and any("single source" in s for s in seen)):
                    merged.append(r)
                    seen.add(key)
            return str(reply["rationale"])[:900], merged[:4], self.llm.label()
        return fallback, risks[:4], "rules"

    def _order_body(self, deal: Deal) -> dict:
        m = deal.mandate
        pct = int(m.advance_pct * 100)
        items = [
            {
                "name": f"{pct}% advance: {l.product_name}, {l.qty_kg} kg"[:127],
                "description": f"{l.seller_name}, ${l.unit_price}/kg, line {l.line_id}"[:127],
                "sku": f"{deal.id}-{l.line_id}",
                "quantity": "1",
                "unit_amount": {"currency_code": "USD", "value": fmt(l.advance)},
                "category": "PHYSICAL_GOODS",
            }
            for l in deal.lines
        ]
        base = self.settings.public_base_url
        return {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": deal.id,
                    "custom_id": deal.id,
                    "invoice_id": f"{deal.id}-ADV",
                    "description": f"Sauda {deal.id}: {pct}% advance for {len(deal.lines)} FPO lots"[:127],
                    "soft_descriptor": "SAUDA",
                    "amount": {
                        "currency_code": "USD",
                        "value": fmt(deal.advance_total),
                        "breakdown": {"item_total": {"currency_code": "USD", "value": fmt(deal.advance_total)}},
                    },
                    "items": items,
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "brand_name": "Sauda (sandbox demo)",
                        "user_action": "PAY_NOW",
                        "shipping_preference": "NO_SHIPPING",
                        "return_url": f"{base}/paypal/return?deal={deal.id}",
                        "cancel_url": f"{base}/paypal/cancel?deal={deal.id}",
                    }
                }
            },
        }

    # ------------------------------------------------------------------ 3. approvals

    async def approve(self, deal_id: str, action_id: str, approver: str) -> Deal:
        async with self.lock(deal_id):
            deal = self.get(deal_id)
            a = deal.action(action_id)
            if a.status != "needs_approval":
                raise DealError(f"action {action_id} is {a.status}, not awaiting approval")
            self.policy.evaluate(deal, a)  # re-check at approval time: state may have changed
            if a.status == "blocked":
                self.save(deal)
                self.log(deal, "policy", "action.blocked", f"{a.kind} blocked at approval", {"action": a.id, "rule": a.approval_rule})
                return deal
            a.status, a.approved_by, a.approved_at = "approved", approver, now()
            self.log(deal, f"human:{approver}", "action.approved", f"Approved: {a.summary}", {"action": a.id, "amount": a.amount})
            await self._execute(deal, a)
            await self._after(deal)
            self.save(deal)
            return deal

    async def reject(self, deal_id: str, action_id: str, approver: str, reason: str = "") -> Deal:
        async with self.lock(deal_id):
            deal = self.get(deal_id)
            a = deal.action(action_id)
            if a.status not in ("needs_approval", "blocked"):
                raise DealError(f"action {action_id} is {a.status}")
            a.status, a.approved_by, a.approved_at, a.error = "rejected", approver, now(), reason or "rejected by buyer"
            if a.kind == "CREATE_ORDER":
                deal.status = "cancelled"
            self.save(deal)
            self.log(deal, f"human:{approver}", "action.rejected", f"Rejected: {a.summary}", {"action": a.id, "reason": a.error})
            return deal

    async def retry(self, deal_id: str, action_id: str, approver: str) -> Deal:
        """Re-run a failed action with its original idempotency key, so PayPal cannot execute it twice."""
        async with self.lock(deal_id):
            deal = self.get(deal_id)
            a = deal.action(action_id)
            if a.status != "failed":
                raise DealError(f"action {action_id} is {a.status}, only failed actions can be retried")
            a.error = None
            self.log(deal, f"human:{approver}", "action.retry", f"Retrying {a.kind}", {"action": a.id, "request_id": a.idempotency_key})
            await self._execute(deal, a)
            await self._after(deal)
            self.save(deal)
            return deal

    async def _submit(self, deal: Deal, a: Action) -> None:
        """Evaluate policy and execute immediately if no human is required."""
        self.policy.evaluate(deal, a)
        self.log(deal, "policy", "action.proposed", a.summary,
                 {"action": a.id, "kind": a.kind, "amount": a.amount, "status": a.status, "rule": a.approval_rule,
                  "checks": [c.model_dump() for c in a.policy]})
        if a.status == "approved":
            await self._execute(deal, a)

    # ------------------------------------------------------------------ 4. execution (the only place PayPal is called)

    async def _execute(self, deal: Deal, a: Action) -> None:
        if a.status == "executed":
            return
        self.policy.evaluate(deal, a, route=False)  # final check right before money moves
        if a.status == "blocked":
            self.log(deal, "policy", "action.blocked", f"{a.kind} blocked before execution", {"action": a.id, "rule": a.approval_rule})
            return
        a.status = "executing"
        try:
            handler = getattr(self, f"_exec_{a.kind.lower()}")
            await handler(deal, a)
            a.status = "executed"
            self.log(deal, f"paypal:{self.paypal.mode}", "action.executed", f"Executed: {a.summary}",
                     {"action": a.id, "kind": a.kind, "amount": a.amount, "request_id": a.idempotency_key, "result": a.result})
        except PayPalError as e:
            a.status, a.error = "failed", str(e)
            self.log(deal, f"paypal:{self.paypal.mode}", "action.failed", f"PayPal rejected {a.kind}",
                     {"action": a.id, "error": str(e), "details": e.details, "debug_id": e.debug_id})

    async def _exec_create_order(self, deal: Deal, a: Action) -> None:
        resp = await self.paypal.create_order(a.payload["request"], a.idempotency_key)
        deal.paypal.order_id = resp["id"]
        deal.paypal.order_status = resp.get("status")
        deal.paypal.approve_url = approve_link(resp)
        a.result = {"order_id": resp["id"], "status": resp.get("status"), "approve_url": deal.paypal.approve_url}
        deal.status = "awaiting_buyer"

    async def _exec_capture_order(self, deal: Deal, a: Action) -> None:
        resp = await self.paypal.capture_order(deal.paypal.order_id, a.idempotency_key)
        cap = resp["purchase_units"][0]["payments"]["captures"][0]
        deal.paypal.order_status = resp.get("status")
        deal.paypal.capture_id = cap["id"]
        deal.paypal.capture_status = cap.get("status")
        deal.paypal.captured = D(cap["amount"]["value"])
        fee = (cap.get("seller_receivable_breakdown") or {}).get("paypal_fee")
        deal.paypal.paypal_fee = D(fee["value"]) if fee else None
        deal.paypal.payer_id = (resp.get("payer") or {}).get("payer_id") or deal.paypal.payer_id
        a.result = {"capture_id": cap["id"], "status": cap.get("status"), "amount": cap["amount"]["value"],
                    "paypal_fee": fee["value"] if fee else None, "seller_protection": (cap.get("seller_protection") or {}).get("status")}
        if cap.get("status") == "COMPLETED":
            deal.status = "in_fulfilment"
            self._notify_order(deal)

    async def _exec_add_tracking(self, deal: Deal, a: Action) -> None:
        body = {
            "capture_id": deal.paypal.capture_id,
            "tracking_number": a.payload["tracking_number"],
            "carrier": a.payload["carrier"],
            "notify_payer": True,
        }
        if a.payload["carrier"] == "OTHER":
            body["carrier_name_other"] = a.payload.get("carrier_name_other", "Other")
        await self.paypal.add_tracking(deal.paypal.order_id, body, a.idempotency_key)
        deal.paypal.trackers.append({"tracking_number": body["tracking_number"], "carrier": body["carrier"], "seller_id": a.payload.get("seller_id")})
        a.result = {"tracking_number": body["tracking_number"], "carrier": body["carrier"]}

    async def _exec_refund(self, deal: Deal, a: Action) -> None:
        body = {
            "amount": {"value": fmt(a.amount), "currency_code": "USD"},
            "invoice_id": f"{deal.id}-RF-{a.id}",
            "note_to_payer": a.payload.get("note", "Refund for goods not shipped")[:255],
        }
        resp = await self.paypal.refund_capture(deal.paypal.capture_id, body, a.idempotency_key)
        deal.paypal.refunds.append({"id": resp["id"], "status": resp.get("status"), "amount": fmt(a.amount), "action": a.id})
        lines = {l.line_id: l for l in deal.lines}
        for lid, amt in a.payload["lines"].items():
            lines[lid].refunded_advance = D(lines[lid].refunded_advance + D(amt))
        a.result = {"refund_id": resp["id"], "status": resp.get("status"), "amount": fmt(a.amount)}

    async def _exec_payout(self, deal: Deal, a: Action) -> None:
        stage = a.payload["stage"]
        body = {
            "sender_batch_header": {
                "sender_batch_id": a.idempotency_key,
                "recipient_type": "EMAIL",
                "email_subject": "Sauda: payment released for your dispatch" if stage == "dispatch" else "Sauda: balance released",
                "email_message": f"Payment for Sauda deal {deal.id}. Thank you for supplying through Sauda.",
            },
            "items": [
                {
                    "recipient_type": "EMAIL",
                    "amount": {"value": fmt(it["amount"]), "currency": "USD"},
                    "receiver": it["receiver"],
                    "note": f"{deal.id} {stage} release: " + ", ".join(it["lines"].keys()),
                    "sender_item_id": f"{deal.id}-{a.id}-{it['seller_id']}",
                }
                for it in a.payload["items"]
            ],
        }
        resp = await self.paypal.create_payout(body, a.idempotency_key)
        head = resp["batch_header"]
        batch = {
            "batch_id": head["payout_batch_id"], "status": head.get("batch_status"), "stage": stage, "action": a.id,
            "amount": fmt(a.amount), "items": [{"seller_id": it["seller_id"], "amount": fmt(it["amount"]), "status": "PENDING"} for it in a.payload["items"]],
        }
        deal.paypal.payout_batches.append(batch)
        lines = {l.line_id: l for l in deal.lines}
        for it in a.payload["items"]:
            for lid, amt in it["lines"].items():
                l = lines[lid]
                gross = l.released_advance if stage == "dispatch" else l.balance
                l.paid_out = D(l.paid_out + D(amt))
                l.fees = D(l.fees + D(gross - D(amt)))
        a.result = {"payout_batch_id": head["payout_batch_id"], "batch_status": head.get("batch_status"), "sender_batch_id": a.idempotency_key}
        for it in a.payload["items"]:
            self._notify_payout(deal, it, stage, head["payout_batch_id"])

    async def _exec_issue_invoice(self, deal: Deal, a: Action) -> None:
        m = deal.mandate
        number = f"{deal.id}-BAL"
        given, _, surname = (m.buyer_name or "Buyer").partition(" ")
        body = {
            "detail": {
                "invoice_number": number,
                "reference": deal.id,
                "currency_code": "USD",
                "note": "Balance for goods dispatched by the FPOs listed. Advance already paid through PayPal Checkout.",
                "terms_and_conditions": "Prices FOB India. Balance due within 10 days of dispatch.",
                "payment_term": {"term_type": "NET_10"},
            },
            "invoicer": {"business_name": "Sauda Trade Desk (sandbox demo)"},
            "primary_recipients": [
                {"billing_info": {"name": {"given_name": given, "surname": surname or "-"}, "business_name": m.buyer_business or None, "email_address": m.buyer_email}}
            ],
            "items": [
                {
                    "name": f"Balance: {l.product_name}, {l.shipped_kg} kg"[:200],
                    "description": f"{l.seller_name}, ${l.unit_price}/kg, tracking {l.tracking}"[:1000],
                    "quantity": "1",
                    "unit_amount": {"currency_code": "USD", "value": fmt(l.balance)},
                    "unit_of_measure": "AMOUNT",
                }
                for l in deal.lines if l.balance > 0
            ],
            "configuration": {"partial_payment": {"allow_partial_payment": False}, "allow_tip": False, "tax_calculated_after_discount": True, "tax_inclusive": False},
        }
        if not body["primary_recipients"][0]["billing_info"]["business_name"]:
            body["primary_recipients"][0]["billing_info"].pop("business_name")
        inv_id = deal.paypal.invoice_id  # set if a previous attempt created the draft but failed to send it
        if not inv_id:
            created = await self.paypal.create_invoice(body, a.idempotency_key)
            inv_id = invoice_id_from(created)
            if not inv_id:
                raise PayPalError(500, "NO_INVOICE_ID", "create invoice returned no id")
            deal.paypal.invoice_id, deal.paypal.invoice_number, deal.paypal.invoice_status = inv_id, number, "DRAFT"
        sent = await self.paypal.send_invoice(inv_id, {"send_to_invoicer": True, "send_to_recipient": True}, a.idempotency_key + "-send")
        url = sent.get("href") if isinstance(sent, dict) else None
        if not url:
            fresh = await self.paypal.get_invoice(inv_id)
            url = ((fresh.get("detail") or {}).get("metadata") or {}).get("recipient_view_url")
        deal.paypal.invoice_id, deal.paypal.invoice_number = inv_id, number
        deal.paypal.invoice_status, deal.paypal.invoice_url, deal.paypal.invoice_amount = "SENT", url, a.amount
        deal.status = "invoiced"
        a.result = {"invoice_id": inv_id, "invoice_number": number, "status": "SENT", "payer_view": url}

    # ------------------------------------------------------------------ 5. buyer approval on PayPal

    async def on_buyer_return(self, order_id: str, payer_id: str = "") -> Deal:
        deal = self.store.find_deal_by_order(order_id)
        if not deal:
            raise DealError("no deal for this PayPal order")
        async with self.lock(deal.id):
            deal = self.get(deal.id)
            if deal.paypal.capture_id:
                return deal  # already captured (return URL reloaded, or webhook got there first)
            order = await self.paypal.get_order(order_id)
            deal.paypal.order_status = order.get("status")
            deal.paypal.payer_id = payer_id or ((order.get("payer") or {}).get("payer_id"))
            self.log(deal, "human:buyer", "checkout.approved", "Buyer approved the advance on PayPal", {"order_id": order_id, "status": deal.paypal.order_status})
            if deal.paypal.order_status == "APPROVED":
                a = self._new_action(deal, "CAPTURE_ORDER", deal.advance_total, f"Capture the approved advance ${deal.advance_total}")
                await self._submit(deal, a)
            await self._after(deal)
            self.save(deal)
            return deal

    async def on_buyer_cancel(self, deal_id: str) -> Deal:
        deal = self.get(deal_id)
        self.log(deal, "human:buyer", "checkout.cancelled", "Buyer left the PayPal approval page without paying", {"order_id": deal.paypal.order_id})
        return deal

    # ------------------------------------------------------------------ 6. FPO dispatch

    async def preview_dispatch(self, deal_id: str, seller_id: str, text: str) -> dict:
        deal = self.get(deal_id)
        lines = [l for l in deal.lines_for(seller_id) if l.status == "pending"]
        if not lines:
            raise DealError("no pending lines for this seller")
        parsed = await parse_dispatch(text[:2000], lines, self.catalog, self.llm)
        parsed["lines"] = [{**x, "product_name": next(l.product_name for l in lines if l.line_id == x["line_id"]),
                            "ordered_kg": next(l.qty_kg for l in lines if l.line_id == x["line_id"])} for x in parsed["lines"]]
        return parsed

    async def confirm_dispatch(self, deal_id: str, seller_id: str, report: dict, text: str = "") -> Deal:
        async with self.lock(deal_id):
            deal = self.get(deal_id)
            if deal.status not in ("in_fulfilment",):
                raise DealError(f"dispatch can only be reported while the deal is in fulfilment (now {deal.status})")
            seller = self.catalog.seller(seller_id)
            pending = {l.line_id: l for l in deal.lines_for(seller_id) if l.status == "pending"}
            if not pending:
                raise DealError("no pending lines for this seller")
            tracking = re.sub(r"[^A-Za-z0-9]", "", str(report.get("tracking_number") or "")).upper()
            carrier = str(report.get("carrier") or "OTHER").upper().replace(" ", "_")[:40]
            qty = {str(x["line_id"]): int(x["shipped_kg"]) for x in report.get("lines", [])}
            shipped_any = any(qty.get(lid, 0) > 0 for lid in pending)
            if shipped_any and not TRACKING_RE.match(tracking):
                raise DealError("a valid tracking number (8-30 letters/digits) is required for shipped goods")
            released_total, refund_total = Decimal("0"), Decimal("0")
            payout_lines: dict[str, str] = {}
            refund_lines: dict[str, str] = {}
            for lid, l in pending.items():
                q = max(0, min(qty.get(lid, l.qty_kg), l.qty_kg))  # never more than ordered
                l.shipped_kg = q
                l.tracking = tracking if q > 0 else None
                l.carrier = carrier if q > 0 else None
                l.status = "dispatched" if q == l.qty_kg else ("short" if q > 0 else "cancelled")
                l.released_advance = D(l.advance * q / l.qty_kg)
                l.balance = D(D(l.unit_price * q) - l.released_advance)
                if l.released_advance > 0:
                    payout_lines[lid] = fmt(net_of_fee(l.released_advance, self.policy.fee_pct))
                    released_total += D(payout_lines[lid])
                short_adv = D(l.advance - l.released_advance)
                if short_adv > 0:
                    refund_lines[lid] = fmt(short_adv)
                    refund_total += short_adv
            self.log(deal, f"human:{seller_id}", "dispatch.reported", f"{seller.name} reported dispatch",
                     {"text": text[:500], "tracking": tracking, "carrier": carrier,
                      "lines": {lid: {"shipped_kg": l.shipped_kg, "status": l.status} for lid, l in pending.items()}})
            if shipped_any:
                known = {"DHL", "FEDEX", "UPS", "INDIA_POST", "ARAMEX", "BLUEDART", "DTDC", "DELHIVERY"}
                t = self._new_action(deal, "ADD_TRACKING", Decimal("0"), f"Attach {seller.name}'s tracking {tracking} to the PayPal capture",
                                     payload={"tracking_number": tracking, "carrier": carrier if carrier in known else "OTHER",
                                              "carrier_name_other": carrier.title(), "seller_id": seller_id})
                await self._submit(deal, t)
            if payout_lines:
                p = self._new_action(
                    deal, "PAYOUT", released_total, f"Release ${released_total} to {seller.name} for verified dispatch",
                    payload={"stage": "dispatch", "items": [{"seller_id": seller_id, "receiver": seller.paypal_email, "amount": fmt(released_total), "lines": payout_lines}]},
                    evidence={"tracking": tracking, "carrier": carrier},
                    reason=f"Advance share for shipped goods, less the {D(Decimal(self.settings.platform_fee_pct) * 100)}% platform fee.",
                )
                await self._submit(deal, p)
            if refund_lines:
                shorts = [l for l in pending.values() if l.status in ("short", "cancelled")]
                note = "; ".join(f"{l.qty_kg - (l.shipped_kg or 0)} kg {l.product_name} not shipped" for l in shorts)
                r = self._new_action(
                    deal, "REFUND", D(refund_total), f"Refund ${D(refund_total)} of the advance to the buyer for the shortfall",
                    payload={"lines": refund_lines, "note": f"Sauda {deal.id}: {note}"}, evidence={"shortfall": note},
                    reason="The buyer prepaid an advance on goods that will not ship.",
                )
                await self._submit(deal, r)
                for l in shorts:
                    local, en = render("short", seller.language, product_en=l.product_name,
                                       product_local=self.catalog.local_name(l.product, seller.language),
                                       short=l.qty_kg - (l.shipped_kg or 0), refund=refund_lines.get(l.line_id, "0.00"))
                    deal.notices.append(Notice(seller_id=seller_id, kind="short", lang=seller.language, text_local=local, text_en=en))
            await self._after(deal)
            self.save(deal)
            return deal

    # ------------------------------------------------------------------ 7. sync / settlement

    async def _after(self, deal: Deal) -> None:
        """Advance the state machine after anything changes."""
        if deal.status == "in_fulfilment" and deal.lines and all(l.status != "pending" for l in deal.lines):
            balance = D(sum((l.balance for l in deal.lines), Decimal("0")))
            # One invoice action per deal; a failed one is retried explicitly (same idempotency key), never duplicated.
            if balance > 0 and not any(a.kind == "ISSUE_INVOICE" for a in deal.actions):
                inv = self._new_action(deal, "ISSUE_INVOICE", balance, f"Invoice the buyer ${balance} balance for shipped goods (PayPal Invoicing)",
                                       reason="All FPOs have reported dispatch; balance is billed on what actually shipped.")
                await self._submit(deal, inv)
            elif balance == 0:
                deal.status = "settling"
        if deal.status == "invoiced" and deal.paypal.invoice_status in ("PAID", "MARKED_AS_PAID"):
            deal.status = "settling"
            if not any(a.kind == "PAYOUT" and a.payload.get("stage") == "balance" for a in deal.actions):
                items = []
                for sid in sorted({l.seller_id for l in deal.lines}):
                    ls = {l.line_id: fmt(net_of_fee(l.balance, self.policy.fee_pct)) for l in deal.lines_for(sid) if l.balance > 0}
                    if ls:
                        amt = D(sum((D(v) for v in ls.values()), Decimal("0")))
                        items.append({"seller_id": sid, "receiver": self.catalog.seller(sid).paypal_email, "amount": fmt(amt), "lines": ls})
                total = D(sum((D(i["amount"]) for i in items), Decimal("0")))
                a = self._new_action(deal, "PAYOUT", total, f"Confirm receipt and release the ${total} balance to {len(items)} FPOs in one PayPal Payouts batch",
                                     payload={"stage": "balance", "items": items}, reason="The buyer paid the balance invoice.")
                await self._submit(deal, a)
        if deal.status == "settling":
            open_actions = [a for a in deal.actions if a.status in ("needs_approval", "approved", "executing")]
            batches_final = all(b["status"] in FINAL_BATCH for b in deal.paypal.payout_batches)
            if not open_actions and batches_final:
                deal.status = "settled"
                self.log(deal, "system", "deal.settled", "Deal settled", {"summary": self.summary(deal)})

    async def sync(self, deal_id: str) -> Deal:
        """Poll PayPal for anything webhooks would normally tell us (useful on localhost)."""
        async with self.lock(deal_id):
            deal = self.get(deal_id)
            pp = deal.paypal
            changed = False
            if pp.order_id and not pp.capture_id and deal.status == "awaiting_buyer":
                order = await self.paypal.get_order(pp.order_id)
                if order.get("status") != pp.order_status:
                    pp.order_status = order.get("status")
                    changed = True
                if pp.order_status == "APPROVED" and not any(a.kind == "CAPTURE_ORDER" and a.status == "executed" for a in deal.actions):
                    a = self._new_action(deal, "CAPTURE_ORDER", deal.advance_total, f"Capture the approved advance ${deal.advance_total}")
                    await self._submit(deal, a)
                    changed = True
            for b in pp.payout_batches:
                if b["status"] in FINAL_BATCH:
                    continue
                res = await self.paypal.get_payout(b["batch_id"])
                status = res["batch_header"].get("batch_status")
                if status != b["status"]:
                    b["status"] = status
                    changed = True
                    self.log(deal, f"paypal:{self.paypal.mode}", "payout.status", f"Payout batch {b['batch_id']} is {status}", {"batch": b["batch_id"], "status": status})
                for item, it in zip(b["items"], res.get("items", [])):
                    st = it.get("transaction_status")
                    if st and st != item["status"]:
                        item["status"] = st
                        item["transaction_id"] = it.get("transaction_id")
                        changed = True
                        if st in FAILED_ITEM:
                            self._revert_payout(deal, b, item)
            if pp.invoice_id and pp.invoice_status not in ("PAID", "MARKED_AS_PAID", "CANCELLED"):
                inv = await self.paypal.get_invoice(pp.invoice_id)
                st = inv.get("status")
                if st != pp.invoice_status:
                    pp.invoice_status = st
                    changed = True
                    if st in ("PAID", "MARKED_AS_PAID"):
                        paid = ((inv.get("payments") or {}).get("paid_amount") or inv.get("amount") or {}).get("value")
                        pp.invoice_paid = D(paid) if paid else pp.invoice_amount
                        self.log(deal, f"paypal:{self.paypal.mode}", "invoice.paid", f"Buyer paid invoice {pp.invoice_number}", {"invoice_id": pp.invoice_id, "amount": pp.invoice_paid})
            await self._after(deal)
            self.save(deal)
            return deal

    def _revert_payout(self, deal: Deal, batch: dict, item: dict) -> None:
        a = deal.action(batch["action"])
        for it in a.payload["items"]:
            if it["seller_id"] != item["seller_id"]:
                continue
            for lid, amt in it["lines"].items():
                l = next(x for x in deal.lines if x.line_id == lid)
                l.paid_out = D(l.paid_out - D(amt))
        self.log(deal, f"paypal:{self.paypal.mode}", "payout.item_failed", f"Payout to {item['seller_id']} {item['status']}; funds remain held",
                 {"batch": batch["batch_id"], "seller": item["seller_id"], "status": item["status"]})

    async def handle_webhook(self, event: dict) -> dict:
        """Map PayPal webhook events onto the same transitions as sync()."""
        eid = event.get("id", "")
        seen = set(self.store.kv_get("webhook_ids") or [])
        if eid and eid in seen:
            return {"status": "duplicate"}
        etype = event.get("event_type", "")
        res = event.get("resource") or {}
        deal: Optional[Deal] = None
        if etype.startswith("CHECKOUT.ORDER"):
            deal = self.store.find_deal_by_order(res.get("id", ""))
        elif etype.startswith("PAYMENT.CAPTURE"):
            oid = ((res.get("supplementary_data") or {}).get("related_ids") or {}).get("order_id", "")
            deal = self.store.find_deal_by_order(oid) or self.store.find_deal_by(lambda d: d.paypal.capture_id == res.get("id"))
        elif etype.startswith("PAYMENT.PAYOUTS"):
            bid = (res.get("batch_header") or {}).get("payout_batch_id") or res.get("payout_batch_id", "")
            deal = self.store.find_deal_by(lambda d: any(b["batch_id"] == bid for b in d.paypal.payout_batches))
        elif etype.startswith("INVOICING.INVOICE"):
            iid = (res.get("invoice") or res).get("id", "")
            deal = self.store.find_deal_by(lambda d: d.paypal.invoice_id == iid)
        if eid:
            seen.add(eid)
            self.store.kv_set("webhook_ids", sorted(seen)[-2000:])
        if not deal:
            self.log(None, "paypal:webhook", "webhook.ignored", f"Webhook {etype} matched no deal", {"event_id": eid})
            return {"status": "ignored"}
        self.log(deal, "paypal:webhook", "webhook.received", f"Webhook {etype}", {"event_id": eid})
        await self.sync(deal.id)
        return {"status": "processed", "deal": deal.id}

    # ------------------------------------------------------------------ notices & summaries

    def _notify_order(self, deal: Deal) -> None:
        m = deal.mandate
        for l in deal.lines:
            s = self.catalog.seller(l.seller_id)
            share = fmt(net_of_fee(l.advance, self.policy.fee_pct))
            local, en = render("order", s.language, product_en=l.product_name, product_local=self.catalog.local_name(l.product, s.language),
                               buyer=m.buyer_business or m.buyer_name, qty=l.qty_kg, price=l.unit_price, value=l.value,
                               pct=int(m.advance_pct * 100), share=share)
            deal.notices.append(Notice(seller_id=s.id, kind="order", lang=s.language, text_local=local, text_en=en))

    def _notify_payout(self, deal: Deal, item: dict, stage: str, batch_id: str) -> None:
        s = self.catalog.seller(item["seller_id"])
        lines = [l for l in deal.lines if l.line_id in item["lines"]]
        if stage == "dispatch":
            l = lines[0]
            local, en = render("payout", s.language, product_en=l.product_name, product_local=self.catalog.local_name(l.product, s.language),
                               amount=fmt(item["amount"]), qty=l.shipped_kg, tracking=l.tracking, batch=batch_id)
        else:
            local, en = render("balance", s.language, amount=fmt(item["amount"]), batch=batch_id)
        deal.notices.append(Notice(seller_id=s.id, kind=f"payout_{stage}", lang=s.language, text_local=local, text_en=en))

    def summary(self, deal: Deal) -> dict:
        pp = deal.paypal
        refunded = D(sum((D(r["amount"]) for r in pp.refunds), Decimal("0")))
        paid_out = D(sum((l.paid_out for l in deal.lines), Decimal("0")))
        fees = D(sum((l.fees for l in deal.lines), Decimal("0")))
        collected = D(pp.captured + pp.invoice_paid)
        return {
            "collected": fmt(collected), "refunded": fmt(refunded), "paid_to_fpos": fmt(paid_out), "platform_fees": fmt(fees),
            "held": fmt(D(collected - refunded - paid_out - fees)),
            "buyer_net_paid": fmt(D(collected - refunded)),
            "shipped_value": fmt(D(sum((D(l.unit_price * (l.shipped_kg or 0)) for l in deal.lines), Decimal("0")))),
        }

