"""FIXTURE MODE: an in-process stand-in for the PayPal sandbox, used when no sandbox keys are configured.

It is NOT PayPal. It returns responses shaped like the documented Orders v2, Payments v2, Payouts v1 and
Invoicing v2 payloads (ids prefixed "FX" so they can never be mistaken for real ones), applies a few of the
same validation rules (item totals, approval before capture, refund ceilings, unique sender_batch_id,
unique invoice numbers) and honours PayPal-Request-Id idempotency. Buyer approval and invoice payment are
simulated by local pages under /fixture/paypal/* that are clearly labelled as simulations.
"""

from __future__ import annotations

import copy
import random
import string
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

from ..money import D, fmt
from .client import CallRecord, PayPalError

API = "https://api-m.sandbox.paypal.com"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id(prefix: str = "FX", n: int = 17) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return prefix + "".join(random.choice(alphabet) for _ in range(n - len(prefix)))


def _unprocessable(issue: str, description: str) -> PayPalError:
    return PayPalError(
        422,
        "UNPROCESSABLE_ENTITY",
        "The requested action could not be performed, semantically incorrect, or failed business validation.",
        debug_id=_id("fxdbg", 13).lower(),
        details=[{"issue": issue, "description": description}],
    )


class FixturePayPal:
    mode = "fixture"

    def __init__(
        self,
        public_base_url: str = "http://localhost:8000",
        state: Optional[dict] = None,
        persist: Optional[Callable[[dict], None]] = None,
        fee_pct: str = "0.0349",
        fee_fixed: str = "0.49",
    ) -> None:
        self.public_base_url = public_base_url.rstrip("/")
        self.state: dict[str, Any] = state or {"orders": {}, "captures": {}, "payouts": {}, "invoices": {}, "idem": {}, "batch_ids": []}
        self._persist = persist
        self._lock = threading.RLock()
        self.fee_pct = Decimal(fee_pct)
        self.fee_fixed = Decimal(fee_fixed)
        self.calls: list[CallRecord] = []
        self.webhook_id = "FIXTURE-WEBHOOK"

    async def aclose(self) -> None:
        return None

    # ------------------------------------------------------------------ plumbing

    def _save(self) -> None:
        if self._persist:
            self._persist(self.state)

    def _record(self, method: str, path: str, status: int, request_id: str = "") -> None:
        self.calls.append(CallRecord(time.time(), method, path, status, request_id, "", self.mode, 0))
        del self.calls[:-500]

    def _idem(self, request_id: str) -> Optional[dict]:
        if request_id and request_id in self.state["idem"]:
            return copy.deepcopy(self.state["idem"][request_id])
        return None

    def _remember(self, request_id: str, response: dict) -> dict:
        if request_id:
            self.state["idem"][request_id] = copy.deepcopy(response)
        self._save()
        return response

    def _write(self, method: str, path: str, request_id: str, fn: Callable[[], dict], status: int = 201) -> dict:
        with self._lock:
            cached = self._idem(request_id)
            if cached is not None:
                self._record(method, path, 200, request_id)  # replayed
                return cached
            try:
                resp = fn()
            except PayPalError as e:
                self._record(method, path, e.status, request_id)
                raise
            self._record(method, path, status, request_id)
            return self._remember(request_id, resp)

    # ------------------------------------------------------------------ Orders v2

    async def create_order(self, body: dict, request_id: str) -> dict:
        return self._write("POST", "/v2/checkout/orders", request_id, lambda: self._create_order(body))

    def _create_order(self, body: dict) -> dict:
        if body.get("intent") not in ("CAPTURE", "AUTHORIZE"):
            raise PayPalError(400, "INVALID_REQUEST", "intent is required", details=[{"issue": "MISSING_REQUIRED_PARAMETER", "field": "/intent"}])
        units = body.get("purchase_units") or []
        if not 1 <= len(units) <= 10:
            raise PayPalError(400, "INVALID_REQUEST", "purchase_units must have 1-10 items")
        for pu in units:
            amount = pu["amount"]
            items = pu.get("items") or []
            if items:
                item_total = sum(Decimal(i["unit_amount"]["value"]) * int(i["quantity"]) for i in items)
                declared = Decimal(amount.get("breakdown", {}).get("item_total", {}).get("value", "-1"))
                if D(item_total) != D(declared):
                    raise _unprocessable("ITEM_TOTAL_MISMATCH", "Should equal sum of (unit_amount * quantity) across all items.")
                if D(declared) != D(amount["value"]):
                    raise _unprocessable("AMOUNT_MISMATCH", "Should equal item_total + tax_total + shipping + handling + insurance - shipping_discount - discount.")
        oid = _id("FX")
        ctx = (body.get("payment_source") or {}).get("paypal", {}).get("experience_context", {})
        order = {
            "id": oid,
            "intent": body["intent"],
            "status": "PAYER_ACTION_REQUIRED" if ctx else "CREATED",
            "purchase_units": copy.deepcopy(units),
            "create_time": _ts(),
            "_return_url": ctx.get("return_url", ""),
            "_cancel_url": ctx.get("cancel_url", ""),
        }
        self.state["orders"][oid] = order
        approve = f"{self.public_base_url}/fixture/paypal/checkoutnow?token={oid}"
        links = [{"href": f"{API}/v2/checkout/orders/{oid}", "rel": "self", "method": "GET"}]
        if ctx:
            links.append({"href": approve, "rel": "payer-action", "method": "GET"})
        else:
            links.append({"href": approve, "rel": "approve", "method": "GET"})
        links.append({"href": f"{API}/v2/checkout/orders/{oid}/capture", "rel": "capture", "method": "POST"})
        resp = {"id": oid, "status": order["status"], "links": links}
        if ctx:
            resp["payment_source"] = {"paypal": {}}
        return resp

    async def get_order(self, order_id: str) -> dict:
        with self._lock:
            order = self.state["orders"].get(order_id)
            if not order:
                self._record("GET", f"/v2/checkout/orders/{order_id}", 404)
                raise PayPalError(404, "RESOURCE_NOT_FOUND", "The specified resource does not exist.")
            self._record("GET", f"/v2/checkout/orders/{order_id}", 200)
            return self._order_view(order)

    def _order_view(self, order: dict) -> dict:
        view = {k: copy.deepcopy(v) for k, v in order.items() if not k.startswith("_")}
        view["links"] = [{"href": f"{API}/v2/checkout/orders/{order['id']}", "rel": "self", "method": "GET"}]
        return view

    def approve_order(self, order_id: str, payer_email: str = "buyer-fixture@sauda.example") -> dict:
        """Simulates the buyer clicking 'Pay Now' on PayPal. Fixture only."""
        with self._lock:
            order = self.state["orders"][order_id]
            if order["status"] not in ("CREATED", "PAYER_ACTION_REQUIRED", "APPROVED"):
                raise PayPalError(422, "UNPROCESSABLE_ENTITY", f"order is {order['status']}")
            order["status"] = "APPROVED"
            order["payer"] = {
                "name": {"given_name": "Anita", "surname": "Shah"},
                "email_address": payer_email,
                "payer_id": order.get("payer", {}).get("payer_id") or _id("FXPAYER", 13),
            }
            self._save()
            return {"return_url": order["_return_url"], "payer_id": order["payer"]["payer_id"]}

    def cancel_url(self, order_id: str) -> str:
        return self.state["orders"].get(order_id, {}).get("_cancel_url", "")

    async def capture_order(self, order_id: str, request_id: str) -> dict:
        return self._write("POST", f"/v2/checkout/orders/{order_id}/capture", request_id, lambda: self._capture(order_id))

    def _capture(self, order_id: str) -> dict:
        order = self.state["orders"].get(order_id)
        if not order:
            raise PayPalError(404, "RESOURCE_NOT_FOUND", "The specified resource does not exist.")
        if order["status"] == "COMPLETED":
            raise _unprocessable("ORDER_ALREADY_CAPTURED", "Order already captured.")
        if order["status"] != "APPROVED":
            raise _unprocessable("ORDER_NOT_APPROVED", "Payer has not yet approved the Order for payment.")
        pu = order["purchase_units"][0]
        gross = D(pu["amount"]["value"])
        fee = D(gross * self.fee_pct + self.fee_fixed)
        cap_id = _id("FXCAP", 17)
        capture = {
            "id": cap_id,
            "status": "COMPLETED",
            "amount": {"currency_code": pu["amount"]["currency_code"], "value": fmt(gross)},
            "final_capture": True,
            "seller_protection": {"status": "ELIGIBLE", "dispute_categories": ["ITEM_NOT_RECEIVED", "UNAUTHORIZED_TRANSACTION"]},
            "seller_receivable_breakdown": {
                "gross_amount": {"currency_code": "USD", "value": fmt(gross)},
                "paypal_fee": {"currency_code": "USD", "value": fmt(fee)},
                "net_amount": {"currency_code": "USD", "value": fmt(gross - fee)},
            },
            "invoice_id": pu.get("invoice_id"),
            "custom_id": pu.get("custom_id"),
            "create_time": _ts(),
            "update_time": _ts(),
            "links": [
                {"href": f"{API}/v2/payments/captures/{cap_id}", "rel": "self", "method": "GET"},
                {"href": f"{API}/v2/payments/captures/{cap_id}/refund", "rel": "refund", "method": "POST"},
                {"href": f"{API}/v2/checkout/orders/{order_id}", "rel": "up", "method": "GET"},
            ],
        }
        order["status"] = "COMPLETED"
        pu["payments"] = {"captures": [capture]}
        self.state["captures"][cap_id] = {"order_id": order_id, "amount": fmt(gross), "refunded": "0.00", "trackers": []}
        return {
            "id": order_id,
            "status": "COMPLETED",
            "payment_source": {"paypal": {"email_address": order["payer"]["email_address"], "account_id": order["payer"]["payer_id"]}},
            "purchase_units": [{"reference_id": pu.get("reference_id", "default"), "payments": {"captures": [copy.deepcopy(capture)]}}],
            "payer": copy.deepcopy(order["payer"]),
            "links": [{"href": f"{API}/v2/checkout/orders/{order_id}", "rel": "self", "method": "GET"}],
        }

    async def add_tracking(self, order_id: str, body: dict, request_id: str) -> dict:
        return self._write("POST", f"/v2/checkout/orders/{order_id}/track", request_id, lambda: self._track(order_id, body))

    def _track(self, order_id: str, body: dict) -> dict:
        order = self.state["orders"].get(order_id)
        if not order or order["status"] != "COMPLETED":
            raise _unprocessable("CAPTURE_NOT_FOUND", "Tracking can only be added to a captured order.")
        cap_id = body.get("capture_id")
        if cap_id not in self.state["captures"]:
            raise _unprocessable("CAPTURE_ID_NOT_FOUND", "capture_id does not belong to this order.")
        if not body.get("tracking_number") or not body.get("carrier"):
            raise PayPalError(400, "INVALID_REQUEST", "tracking_number and carrier are required")
        tracker_id = f"{cap_id}-{body['tracking_number']}"
        self.state["captures"][cap_id]["trackers"].append(tracker_id)
        view = self._order_view(order)
        view["purchase_units"][0].setdefault("shipping", {})["trackers"] = [
            {"id": t, "status": "SHIPPED", "create_time": _ts()} for t in self.state["captures"][cap_id]["trackers"]
        ]
        return view

    # ------------------------------------------------------------------ Payments v2

    async def refund_capture(self, capture_id: str, body: dict, request_id: str) -> dict:
        return self._write("POST", f"/v2/payments/captures/{capture_id}/refund", request_id, lambda: self._refund(capture_id, body))

    def _refund(self, capture_id: str, body: dict) -> dict:
        cap = self.state["captures"].get(capture_id)
        if not cap:
            raise PayPalError(404, "RESOURCE_NOT_FOUND", "The specified resource does not exist.")
        amount = D(body["amount"]["value"])
        remaining = D(cap["amount"]) - D(cap["refunded"])
        if amount <= 0 or amount > remaining:
            raise _unprocessable("REFUND_AMOUNT_EXCEEDED", "The refund amount must be less than or equal to the capture amount that has not yet been refunded.")
        cap["refunded"] = fmt(D(cap["refunded"]) + amount)
        rid = _id("FXREF", 17)
        return {
            "id": rid,
            "amount": {"currency_code": body["amount"]["currency_code"], "value": fmt(amount)},
            "invoice_id": body.get("invoice_id"),
            "note_to_payer": body.get("note_to_payer"),
            "status": "COMPLETED",
            "create_time": _ts(),
            "update_time": _ts(),
            "links": [
                {"href": f"{API}/v2/payments/refunds/{rid}", "rel": "self", "method": "GET"},
                {"href": f"{API}/v2/payments/captures/{capture_id}", "rel": "up", "method": "GET"},
            ],
        }

    # ------------------------------------------------------------------ Payouts v1

    async def create_payout(self, body: dict, request_id: str) -> dict:
        return self._write("POST", "/v1/payments/payouts", request_id, lambda: self._payout(body))

    def _payout(self, body: dict) -> dict:
        header = body.get("sender_batch_header") or {}
        items = body.get("items") or []
        if not 1 <= len(items) <= 15000:
            raise PayPalError(400, "VALIDATION_ERROR", "Invalid request - see details", details=[{"field": "items", "issue": "1-15000 items required"}])
        sbid = header.get("sender_batch_id")
        if sbid and sbid in self.state["batch_ids"]:
            raise PayPalError(400, "USER_BUSINESS_ERROR", "Batch with given sender_batch_id already exists")
        for it in items:
            if not it.get("receiver") or D(it["amount"]["value"]) <= 0:
                raise PayPalError(400, "VALIDATION_ERROR", "Invalid request - see details")
        bid = _id("FXB", 13)
        if sbid:
            self.state["batch_ids"].append(sbid)
        total = sum(D(i["amount"]["value"]) for i in items)
        self.state["payouts"][bid] = {
            "header": copy.deepcopy(header),
            "items": copy.deepcopy(items),
            "status": "PENDING",
            "polls": 0,
            "time_created": _ts(),
            "total": fmt(total),
            "item_ids": [_id("FXI", 13) for _ in items],
        }
        return {
            "batch_header": {"sender_batch_header": copy.deepcopy(header), "payout_batch_id": bid, "batch_status": "PENDING"},
            "links": [{"href": f"{API}/v1/payments/payouts/{bid}", "rel": "self", "method": "GET", "encType": "application/json"}],
        }

    async def get_payout(self, batch_id: str) -> dict:
        with self._lock:
            p = self.state["payouts"].get(batch_id)
            if not p:
                self._record("GET", f"/v1/payments/payouts/{batch_id}", 404)
                raise PayPalError(404, "INVALID_RESOURCE_ID", "Requested resource ID was not found.")
            p["polls"] += 1
            if p["status"] == "PENDING":
                p["status"] = "PROCESSING"
            elif p["status"] == "PROCESSING":
                p["status"] = "SUCCESS"
                p["time_completed"] = _ts()
            self._save()
            self._record("GET", f"/v1/payments/payouts/{batch_id}", 200)
            return self._payout_view(batch_id, p)

    def _payout_view(self, batch_id: str, p: dict) -> dict:
        done = p["status"] == "SUCCESS"
        items = []
        for it, iid in zip(p["items"], p["item_ids"]):
            unclaimed = "unclaimed" in it["receiver"]
            status = ("UNCLAIMED" if unclaimed else "SUCCESS") if done else "PENDING"
            fee = D(min(D(it["amount"]["value"]) * Decimal("0.02"), Decimal("20"))) if done else D(0)
            items.append(
                {
                    "payout_item_id": iid,
                    "transaction_id": ("FXT" + iid[3:]) if done else None,
                    "transaction_status": status,
                    "payout_item_fee": {"currency": "USD", "value": fmt(fee)},
                    "payout_batch_id": batch_id,
                    "payout_item": copy.deepcopy(it),
                    "time_processed": _ts() if done else None,
                    "links": [{"href": f"{API}/v1/payments/payouts-item/{iid}", "rel": "item", "method": "GET"}],
                }
            )
        fees = sum(D(i["payout_item_fee"]["value"]) for i in items)
        return {
            "batch_header": {
                "payout_batch_id": batch_id,
                "batch_status": p["status"],
                "time_created": p["time_created"],
                **({"time_completed": p["time_completed"]} if p.get("time_completed") else {}),
                "sender_batch_header": copy.deepcopy(p["header"]),
                "amount": {"currency": "USD", "value": p["total"]},
                "fees": {"currency": "USD", "value": fmt(fees)},
            },
            "items": items,
            "links": [{"href": f"{API}/v1/payments/payouts/{batch_id}", "rel": "self", "method": "GET"}],
        }

    # ------------------------------------------------------------------ Invoicing v2

    async def create_invoice(self, body: dict, request_id: str) -> dict:
        return self._write("POST", "/v2/invoicing/invoices", request_id, lambda: self._create_invoice(body))

    def _create_invoice(self, body: dict) -> dict:
        detail = body.get("detail") or {}
        number = detail.get("invoice_number")
        if not detail.get("currency_code"):
            raise PayPalError(400, "INVALID_REQUEST", "detail.currency_code is required")
        if number and any(inv["detail"].get("invoice_number") == number for inv in self.state["invoices"].values()):
            raise _unprocessable("DUPLICATE_INVOICE_NUMBER", "Invoice number already exists.")
        items = body.get("items") or []
        if not items:
            raise PayPalError(400, "INVALID_REQUEST", "items are required")
        total = sum(D(i["unit_amount"]["value"]) * Decimal(str(i["quantity"])) for i in items)
        iid = "INV2-" + "-".join(_id("", 4) for _ in range(5))
        iid = iid.replace("INV2-", "INV2-FX", 1)
        inv = {
            "id": iid,
            "status": "DRAFT",
            "detail": {**copy.deepcopy(detail), "metadata": {"create_time": _ts(), "recipient_view_url": f"{self.public_base_url}/fixture/paypal/invoice/{iid}", "invoicer_view_url": f"{self.public_base_url}/fixture/paypal/invoice/{iid}"}},
            "invoicer": copy.deepcopy(body.get("invoicer") or {}),
            "primary_recipients": copy.deepcopy(body.get("primary_recipients") or []),
            "items": copy.deepcopy(items),
            "configuration": copy.deepcopy(body.get("configuration") or {}),
            "amount": {"currency_code": detail["currency_code"], "value": fmt(total), "breakdown": {"item_total": {"currency_code": detail["currency_code"], "value": fmt(total)}}},
            "due_amount": {"currency_code": detail["currency_code"], "value": fmt(total)},
        }
        self.state["invoices"][iid] = inv
        return self._invoice_view(inv)

    def _invoice_view(self, inv: dict) -> dict:
        view = copy.deepcopy(inv)
        view["links"] = [
            {"href": f"{API}/v2/invoicing/invoices/{inv['id']}", "rel": "self", "method": "GET"},
            {"href": f"{API}/v2/invoicing/invoices/{inv['id']}/send", "rel": "send", "method": "POST"},
        ]
        return view

    async def send_invoice(self, invoice_id: str, body: dict, request_id: str) -> dict:
        return self._write("POST", f"/v2/invoicing/invoices/{invoice_id}/send", request_id, lambda: self._send(invoice_id), status=200)

    def _send(self, invoice_id: str) -> dict:
        inv = self.state["invoices"].get(invoice_id)
        if not inv:
            raise PayPalError(404, "RESOURCE_NOT_FOUND", "The specified resource does not exist.")
        if inv["status"] != "DRAFT":
            raise _unprocessable("INVALID_INVOICE_STATUS", f"Invoice is {inv['status']}.")
        inv["status"] = "SENT"
        return {"href": inv["detail"]["metadata"]["recipient_view_url"], "rel": "payer-view", "method": "GET"}

    async def get_invoice(self, invoice_id: str) -> dict:
        with self._lock:
            inv = self.state["invoices"].get(invoice_id)
            if not inv:
                self._record("GET", f"/v2/invoicing/invoices/{invoice_id}", 404)
                raise PayPalError(404, "RESOURCE_NOT_FOUND", "The specified resource does not exist.")
            self._record("GET", f"/v2/invoicing/invoices/{invoice_id}", 200)
            return self._invoice_view(inv)

    def pay_invoice(self, invoice_id: str) -> dict:
        """Simulates the buyer paying the invoice on PayPal's invoice page. Fixture only."""
        with self._lock:
            inv = self.state["invoices"][invoice_id]
            if inv["status"] not in ("SENT", "UNPAID", "PAID"):
                raise PayPalError(422, "UNPROCESSABLE_ENTITY", f"invoice is {inv['status']}")
            if inv["status"] != "PAID":
                inv["status"] = "PAID"
                inv["payments"] = {
                    "paid_amount": copy.deepcopy(inv["amount"]),
                    "transactions": [{"payment_id": _id("FXPAY", 17), "payment_date": _ts()[:10], "method": "PAYPAL", "amount": {"currency_code": "USD", "value": inv["amount"]["value"]}}],
                }
                inv["due_amount"] = {"currency_code": "USD", "value": "0.00"}
                self._save()
            return self._invoice_view(inv)

    # ------------------------------------------------------------------ Webhooks

    async def verify_webhook(self, headers: dict[str, str], event: dict) -> bool:
        # Fixture events are generated locally and signed with a fixed marker header.
        h = {k.lower(): v for k, v in headers.items()}
        return h.get("paypal-transmission-sig") == "fixture-signature"
