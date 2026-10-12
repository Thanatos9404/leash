"""FIXTURE MODE: an in-process stand-in for the PayPal sandbox, used when no sandbox keys are configured.

It is NOT PayPal. Responses follow the documented shapes of Orders v2, Payments v2 (authorizations, captures,
refunds) and Disputes v1, with ids prefixed "FX" so they can never be mistaken for real ones. It applies the
rules that matter for safety tests the way PayPal documents them: capture only after approval and
authorization, partial captures with final_capture=false, PayPal's own over-capture allowance (up to 115% of
the authorized amount or +$75, whichever is less), void after partial capture, unique invoice ids, and
PayPal-Request-Id replay. Buyer approval is simulated by a local page labelled as a simulation.
"""

from __future__ import annotations

import copy
import random
import string
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

from ..money import D, fmt
from .client import CallRecord, PayPalError

API = "https://api-m.sandbox.paypal.com"


def _ts(offset_days: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=offset_days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id(prefix: str = "FX", n: int = 17) -> str:
    return prefix + "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(n - len(prefix)))


def _unprocessable(issue: str, description: str) -> PayPalError:
    return PayPalError(422, "UNPROCESSABLE_ENTITY", "The requested action could not be performed, semantically incorrect, or failed business validation.",
                       debug_id=_id("fxdbg", 13).lower(), details=[{"issue": issue, "description": description}])


def empty_state() -> dict:
    return {"orders": {}, "auths": {}, "captures": {}, "disputes": {}, "invoice_ids": [], "idem": {}}


class FixturePayPal:
    mode = "fixture"

    def __init__(self, public_base_url: str = "http://localhost:8000", state: Optional[dict] = None,
                 persist: Optional[Callable[[dict], None]] = None) -> None:
        self.public_base_url = public_base_url.rstrip("/")
        self.state: dict[str, Any] = state or empty_state()
        for k, v in empty_state().items():
            self.state.setdefault(k, v)
        self._persist = persist
        self._lock = threading.RLock()
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

    def _write(self, method: str, path: str, request_id: str, fn: Callable[[], dict], status: int = 201) -> dict:
        with self._lock:
            if request_id and request_id in self.state["idem"]:
                self._record(method, path, 200, request_id)  # PayPal-Request-Id replay: same response, no new effect
                return copy.deepcopy(self.state["idem"][request_id])
            try:
                resp = fn()
            except PayPalError as e:
                self._record(method, path, e.status, request_id)
                raise
            self._record(method, path, status, request_id)
            if request_id:
                self.state["idem"][request_id] = copy.deepcopy(resp)
            self._save()
            return resp

    def _read(self, path: str, table: str, key: str, view: Callable[[dict], dict]) -> dict:
        with self._lock:
            obj = self.state[table].get(key)
            if not obj:
                self._record("GET", path, 404)
                raise PayPalError(404, "RESOURCE_NOT_FOUND", "The specified resource does not exist.")
            self._record("GET", path, 200)
            return view(obj)

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
            items = pu.get("items") or []
            if items:
                item_total = sum(Decimal(i["unit_amount"]["value"]) * int(i["quantity"]) for i in items)
                declared = Decimal(pu["amount"].get("breakdown", {}).get("item_total", {}).get("value", "-1"))
                if D(item_total) != D(declared):
                    raise _unprocessable("ITEM_TOTAL_MISMATCH", "Should equal sum of (unit_amount * quantity) across all items.")
        oid = _id("FX")
        ctx = (body.get("payment_source") or {}).get("paypal", {}).get("experience_context", {})
        self.state["orders"][oid] = {
            "id": oid, "intent": body["intent"], "status": "PAYER_ACTION_REQUIRED" if ctx else "CREATED",
            "purchase_units": copy.deepcopy(units), "create_time": _ts(),
            "_return_url": ctx.get("return_url", ""), "_cancel_url": ctx.get("cancel_url", ""),
        }
        rel = "payer-action" if ctx else "approve"
        return {
            "id": oid,
            "status": self.state["orders"][oid]["status"],
            **({"payment_source": {"paypal": {}}} if ctx else {}),
            "links": [
                {"href": f"{API}/v2/checkout/orders/{oid}", "rel": "self", "method": "GET"},
                {"href": f"{self.public_base_url}/fixture/paypal/checkoutnow?token={oid}", "rel": rel, "method": "GET"},
            ],
        }

    async def get_order(self, order_id: str) -> dict:
        return self._read(f"/v2/checkout/orders/{order_id}", "orders", order_id, self._order_view)

    def _order_view(self, order: dict) -> dict:
        view = {k: copy.deepcopy(v) for k, v in order.items() if not k.startswith("_")}
        view["links"] = [{"href": f"{API}/v2/checkout/orders/{order['id']}", "rel": "self", "method": "GET"}]
        return view

    def approve_order(self, order_id: str) -> dict:
        """Simulates the buyer approving on PayPal. Fixture only."""
        with self._lock:
            order = self.state["orders"][order_id]
            if order["status"] not in ("CREATED", "PAYER_ACTION_REQUIRED", "APPROVED"):
                raise PayPalError(422, "UNPROCESSABLE_ENTITY", f"order is {order['status']}")
            order["status"] = "APPROVED"
            order.setdefault("payer", {"name": {"given_name": "Sandbox", "surname": "Buyer"}, "email_address": "buyer-fixture@leash.example", "payer_id": _id("FXPAYER", 13)})
            self._save()
            return {"return_url": order["_return_url"], "payer_id": order["payer"]["payer_id"]}

    def cancel_url(self, order_id: str) -> str:
        return self.state["orders"].get(order_id, {}).get("_cancel_url", "")

    async def authorize_order(self, order_id: str, request_id: str) -> dict:
        return self._write("POST", f"/v2/checkout/orders/{order_id}/authorize", request_id, lambda: self._authorize(order_id))

    def _authorize(self, order_id: str) -> dict:
        order = self.state["orders"].get(order_id)
        if not order:
            raise PayPalError(404, "RESOURCE_NOT_FOUND", "The specified resource does not exist.")
        if order["intent"] != "AUTHORIZE":
            raise _unprocessable("ACTION_DOES_NOT_MATCH_INTENT", "Order was created with an intent of 'CAPTURE'.")
        if order["status"] == "COMPLETED":
            raise _unprocessable("ORDER_ALREADY_AUTHORIZED", "Order already authorized.")
        if order["status"] != "APPROVED":
            raise _unprocessable("ORDER_NOT_APPROVED", "Payer has not yet approved the Order for payment.")
        pu = order["purchase_units"][0]
        aid = _id("FXAUTH", 17)
        auth = {
            "id": aid, "status": "CREATED", "amount": copy.deepcopy(pu["amount"]), "captured": "0.00",
            "invoice_id": pu.get("invoice_id"), "custom_id": pu.get("custom_id"),
            "seller_protection": {"status": "ELIGIBLE", "dispute_categories": ["ITEM_NOT_RECEIVED", "UNAUTHORIZED_TRANSACTION"]},
            "expiration_time": _ts(29), "create_time": _ts(), "update_time": _ts(), "order_id": order_id,
        }
        self.state["auths"][aid] = auth
        order["status"] = "COMPLETED"
        pu["payments"] = {"authorizations": [self._auth_view(auth)]}
        return {"id": order_id, "status": "COMPLETED", "payer": copy.deepcopy(order.get("payer", {})),
                "purchase_units": [{"reference_id": pu.get("reference_id", "default"), "payments": {"authorizations": [self._auth_view(auth)]}}],
                "links": [{"href": f"{API}/v2/checkout/orders/{order_id}", "rel": "self", "method": "GET"}]}

    # ------------------------------------------------------------------ Payments v2

    def _auth_view(self, a: dict) -> dict:
        view = {k: copy.deepcopy(v) for k, v in a.items() if k not in ("captured", "order_id")}
        view["links"] = [
            {"href": f"{API}/v2/payments/authorizations/{a['id']}", "rel": "self", "method": "GET"},
            {"href": f"{API}/v2/payments/authorizations/{a['id']}/capture", "rel": "capture", "method": "POST"},
            {"href": f"{API}/v2/payments/authorizations/{a['id']}/void", "rel": "void", "method": "POST"},
        ]
        return view

    async def get_authorization(self, authorization_id: str) -> dict:
        return self._read(f"/v2/payments/authorizations/{authorization_id}", "auths", authorization_id, self._auth_view)

    async def capture_authorization(self, authorization_id: str, body: dict, request_id: str) -> dict:
        return self._write("POST", f"/v2/payments/authorizations/{authorization_id}/capture", request_id,
                           lambda: self._capture(authorization_id, body))

    def _capture(self, authorization_id: str, body: dict) -> dict:
        a = self.state["auths"].get(authorization_id)
        if not a:
            raise PayPalError(404, "RESOURCE_NOT_FOUND", "The specified resource does not exist.")
        if a["status"] == "VOIDED":
            raise _unprocessable("AUTHORIZATION_VOIDED", "A voided authorization cannot be captured.")
        if a["status"] == "CAPTURED":
            raise _unprocessable("AUTHORIZATION_ALREADY_CAPTURED", "Authorization has already been fully captured.")
        authorized = D(a["amount"]["value"])
        amount = D(body.get("amount", {}).get("value", a["amount"]["value"]))
        ceiling = min(D(authorized * Decimal("1.15")), D(authorized + Decimal("75")))  # PayPal's documented allowance
        if amount <= 0 or D(D(a["captured"]) + amount) > ceiling:
            raise _unprocessable("MAX_CAPTURE_AMOUNT_EXCEEDED", "Capture amount exceeds allowable limit.")
        inv = body.get("invoice_id")
        if inv and inv in self.state["invoice_ids"]:
            raise _unprocessable("DUPLICATE_INVOICE_ID", "Requested invoice number has been previously captured.")
        if inv:
            self.state["invoice_ids"].append(inv)
        cid = _id("FXCAP", 17)
        fee = D(amount * Decimal("0.0349") + Decimal("0.49"))
        a["captured"] = fmt(D(a["captured"]) + amount)
        final = bool(body.get("final_capture", False))
        a["status"] = "CAPTURED" if final or D(a["captured"]) >= authorized else "PARTIALLY_CAPTURED"
        a["update_time"] = _ts()
        cap = {
            "id": cid, "status": "COMPLETED", "amount": {"currency_code": "USD", "value": fmt(amount)}, "final_capture": final,
            "invoice_id": inv, "custom_id": body.get("custom_id"),
            "seller_protection": a["seller_protection"],
            "seller_receivable_breakdown": {"gross_amount": {"currency_code": "USD", "value": fmt(amount)},
                                            "paypal_fee": {"currency_code": "USD", "value": fmt(fee)},
                                            "net_amount": {"currency_code": "USD", "value": fmt(amount - fee)}},
            "create_time": _ts(), "update_time": _ts(),
            "links": [{"href": f"{API}/v2/payments/captures/{cid}", "rel": "self", "method": "GET"},
                      {"href": f"{API}/v2/payments/captures/{cid}/refund", "rel": "refund", "method": "POST"},
                      {"href": f"{API}/v2/payments/authorizations/{authorization_id}", "rel": "up", "method": "GET"}],
        }
        self.state["captures"][cid] = {**cap, "authorization_id": authorization_id, "refunded": "0.00"}
        return copy.deepcopy(cap)

    async def void_authorization(self, authorization_id: str, request_id: str) -> dict:
        return self._write("POST", f"/v2/payments/authorizations/{authorization_id}/void", request_id, lambda: self._void(authorization_id), status=200)

    def _void(self, authorization_id: str) -> dict:
        a = self.state["auths"].get(authorization_id)
        if not a:
            raise PayPalError(404, "RESOURCE_NOT_FOUND", "The specified resource does not exist.")
        if a["status"] == "VOIDED":
            raise _unprocessable("AUTHORIZATION_ALREADY_VOIDED", "Authorization has already been voided.")
        if a["status"] == "CAPTURED":
            raise _unprocessable("PREVIOUSLY_CAPTURED", "Authorization has been previously captured and cannot be voided.")
        a["status"] = "VOIDED"
        a["update_time"] = _ts()
        return self._auth_view(a)

    async def refund_capture(self, capture_id: str, body: dict, request_id: str) -> dict:
        return self._write("POST", f"/v2/payments/captures/{capture_id}/refund", request_id, lambda: self._refund(capture_id, body))

    def _refund(self, capture_id: str, body: dict) -> dict:
        cap = self.state["captures"].get(capture_id)
        if not cap:
            raise PayPalError(404, "RESOURCE_NOT_FOUND", "The specified resource does not exist.")
        amount = D((body.get("amount") or cap["amount"])["value"])
        remaining = D(cap["amount"]["value"]) - D(cap["refunded"])
        if amount <= 0 or amount > remaining:
            raise _unprocessable("REFUND_AMOUNT_EXCEEDED", "The refund amount must be less than or equal to the capture amount that has not yet been refunded.")
        cap["refunded"] = fmt(D(cap["refunded"]) + amount)
        rid = _id("FXREF", 17)
        return {"id": rid, "status": "COMPLETED", "amount": {"currency_code": "USD", "value": fmt(amount)},
                "note_to_payer": body.get("note_to_payer"), "create_time": _ts(),
                "links": [{"href": f"{API}/v2/payments/refunds/{rid}", "rel": "self", "method": "GET"},
                          {"href": f"{API}/v2/payments/captures/{capture_id}", "rel": "up", "method": "GET"}]}

    # ------------------------------------------------------------------ Disputes v1

    def simulate_dispute(self, capture_id: str, reason: str = "UNAUTHORISED") -> dict:
        """Simulates a buyer opening a dispute on a capture. Fixture only (in the sandbox, file it as the buyer)."""
        with self._lock:
            cap = self.state["captures"][capture_id]
            did = _id("PP-D-FX", 16)
            d = {
                "dispute_id": did, "reason": reason, "status": "WAITING_FOR_SELLER_RESPONSE", "dispute_life_cycle_stage": "INQUIRY",
                "dispute_amount": copy.deepcopy(cap["amount"]), "create_time": _ts(), "update_time": _ts(),
                "seller_response_due_date": _ts(10),
                "disputed_transactions": [{"seller_transaction_id": capture_id, "invoice_number": cap.get("invoice_id"),
                                           "gross_amount": copy.deepcopy(cap["amount"])}],
                "messages": [{"posted_by": "BUYER", "time_posted": _ts(), "content": "I did not authorise this purchase."}],
                "evidences": [],
            }
            self.state["disputes"][did] = d
            self._save()
            return copy.deepcopy(d)

    async def list_disputes(self) -> dict:
        with self._lock:
            self._record("GET", "/v1/customer/disputes", 200)
            items = [{k: copy.deepcopy(d[k]) for k in ("dispute_id", "reason", "status", "dispute_amount", "create_time", "update_time", "dispute_life_cycle_stage")}
                     for d in self.state["disputes"].values()]
            return {"items": items}

    async def get_dispute(self, dispute_id: str) -> dict:
        return self._read(f"/v1/customer/disputes/{dispute_id}", "disputes", dispute_id, copy.deepcopy)

    async def provide_evidence(self, dispute_id: str, evidence: dict, filename: str, content: bytes, request_id: str) -> dict:
        def run() -> dict:
            d = self.state["disputes"].get(dispute_id)
            if not d:
                raise PayPalError(404, "RESOURCE_NOT_FOUND", "The specified resource does not exist.")
            if d["status"] not in ("WAITING_FOR_SELLER_RESPONSE", "UNDER_REVIEW"):
                raise _unprocessable("INVALID_DISPUTE_STATUS", f"Dispute is {d['status']}.")
            if not content.startswith(b"%PDF"):
                raise PayPalError(400, "INVALID_REQUEST", "Evidence files must be JPG, GIF, PNG or PDF.")
            d["evidences"].append({"evidence_type": evidence["evidences"][0].get("evidence_type"), "notes": evidence["evidences"][0].get("notes", ""),
                                   "documents": [{"name": filename, "size": len(content)}], "date": _ts()})
            d["status"] = "UNDER_REVIEW"
            d["update_time"] = _ts()
            return {"links": [{"href": f"{API}/v1/customer/disputes/{dispute_id}", "rel": "self", "method": "GET"}]}
        return self._write("POST", f"/v1/customer/disputes/{dispute_id}/provide-evidence", request_id, run, status=200)

    # ------------------------------------------------------------------ Webhooks

    async def verify_webhook(self, headers: dict[str, str], event: dict) -> bool:
        # Fixture events are generated in-process and marked with a fixed header value.
        h = {k.lower(): v for k, v in headers.items()}
        return h.get("paypal-transmission-sig") == "fixture-signature"
