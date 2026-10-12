"""PayPal REST client, sandbox only.

APIs used (all on https://api-m.sandbox.paypal.com):
  POST /v1/oauth2/token                                  client-credentials access token
  POST /v2/checkout/orders                               create the advance order (buyer approves on PayPal)
  GET  /v2/checkout/orders/{id}                          order status
  POST /v2/checkout/orders/{id}/capture                  capture after buyer approval
  POST /v2/checkout/orders/{id}/track                    attach FPO dispatch tracking to the capture
  POST /v2/payments/captures/{id}/refund                 partial refund for short shipments
  POST /v1/payments/payouts                              pay FPOs (batch payouts)
  GET  /v1/payments/payouts/{batch_id}                   payout batch + item status
  POST /v2/invoicing/invoices                            draft the balance invoice
  POST /v2/invoicing/invoices/{id}/send                  send it to the buyer
  GET  /v2/invoicing/invoices/{id}                       invoice status (PAID?)
  POST /v1/notifications/verify-webhook-signature        webhook authenticity

Every write carries a PayPal-Request-Id derived from the action, so retries are idempotent.
Secrets and access tokens are never logged; the call log keeps method, path, status, debug id and request id.
"""

from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from ..config import PAYPAL_SANDBOX_API, SandboxOnlyError, Settings


class PayPalError(RuntimeError):
    def __init__(self, status: int, name: str, message: str, debug_id: str = "", details: Any = None) -> None:
        super().__init__(f"PayPal {status} {name}: {message}" + (f" (debug_id {debug_id})" if debug_id else ""))
        self.status = status
        self.name = name
        self.message = message
        self.debug_id = debug_id
        self.details = details


@dataclass
class CallRecord:
    at: float
    method: str
    path: str
    status: int
    request_id: str
    debug_id: str
    mode: str
    ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "method": self.method,
            "path": self.path,
            "status": self.status,
            "request_id": self.request_id,
            "debug_id": self.debug_id,
            "mode": self.mode,
            "ms": self.ms,
        }


class RestPayPal:
    mode = "sandbox"

    def __init__(self, settings: Settings, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        if settings.paypal_env != "sandbox":
            raise SandboxOnlyError("RestPayPal only supports the sandbox.")
        if not (settings.paypal_client_id and settings.paypal_client_secret):
            raise SandboxOnlyError("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are required for sandbox mode.")
        self.base_url = PAYPAL_SANDBOX_API
        assert "sandbox" in self.base_url  # belt and braces: never a live host
        self._id = settings.paypal_client_id
        self._secret = settings.paypal_client_secret
        self.webhook_id = settings.paypal_webhook_id
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=30.0, transport=transport)
        self._token: Optional[str] = None
        self._token_exp = 0.0
        self._lock = asyncio.Lock()
        self.calls: list[CallRecord] = []

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ auth

    async def _access_token(self, force: bool = False) -> str:
        async with self._lock:
            if self._token and not force and time.time() < self._token_exp - 60:
                return self._token
            basic = base64.b64encode(f"{self._id}:{self._secret}".encode()).decode()
            t0 = time.perf_counter()
            r = await self._http.post(
                "/v1/oauth2/token",
                data={"grant_type": "client_credentials"},
                headers={"Authorization": f"Basic {basic}", "Accept": "application/json"},
            )
            self._log("POST", "/v1/oauth2/token", r, "", t0)
            if r.status_code != 200:
                raise self._error(r)
            body = r.json()
            self._token = body["access_token"]
            self._token_exp = time.time() + int(body.get("expires_in", 3600))
            return self._token

    # ------------------------------------------------------------------ transport

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        request_id: str = "",
        prefer: str = "return=representation",
    ) -> dict[str, Any]:
        refresh = False
        retryable = method == "GET" or bool(request_id)  # a write is only retried under the same request id
        for attempt in range(3):
            token = await self._access_token(force=refresh)
            refresh = False
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"}
            if request_id:
                headers["PayPal-Request-Id"] = request_id
            if prefer:
                headers["Prefer"] = prefer
            t0 = time.perf_counter()
            try:
                r = await self._http.request(method, path, json=json, headers=headers)
            except httpx.TransportError:
                if attempt < 2 and retryable:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise
            self._log(method, path, r, request_id, t0)
            if r.status_code == 401 and attempt == 0:
                refresh = True  # token expired: fetch a new one and replay once
                continue
            if r.status_code >= 500 and attempt < 2 and retryable:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise self._error(r)
            if r.status_code == 204 or not r.content:
                return {}
            return r.json()
        raise PayPalError(503, "RETRIES_EXHAUSTED", f"{method} {path} failed after retries")

    def _log(self, method: str, path: str, r: httpx.Response, request_id: str, t0: float) -> None:
        self.calls.append(
            CallRecord(
                at=time.time(),
                method=method,
                path=path,
                status=r.status_code,
                request_id=request_id,
                debug_id=r.headers.get("paypal-debug-id", ""),
                mode=self.mode,
                ms=int((time.perf_counter() - t0) * 1000),
            )
        )
        del self.calls[:-500]

    @staticmethod
    def _error(r: httpx.Response) -> PayPalError:
        try:
            body = r.json()
        except ValueError:
            body = {}
        name = body.get("name") or body.get("error") or "HTTP_ERROR"
        message = body.get("message") or body.get("error_description") or r.text[:200]
        return PayPalError(r.status_code, name, message, body.get("debug_id", r.headers.get("paypal-debug-id", "")), body.get("details"))

    # ------------------------------------------------------------------ Orders v2

    async def create_order(self, body: dict, request_id: str) -> dict:
        return await self._request("POST", "/v2/checkout/orders", json=body, request_id=request_id)

    async def get_order(self, order_id: str) -> dict:
        return await self._request("GET", f"/v2/checkout/orders/{order_id}", prefer="")

    async def capture_order(self, order_id: str, request_id: str) -> dict:
        return await self._request("POST", f"/v2/checkout/orders/{order_id}/capture", json={}, request_id=request_id)

    async def add_tracking(self, order_id: str, body: dict, request_id: str) -> dict:
        return await self._request("POST", f"/v2/checkout/orders/{order_id}/track", json=body, request_id=request_id)

    # ------------------------------------------------------------------ Payments v2

    async def refund_capture(self, capture_id: str, body: dict, request_id: str) -> dict:
        return await self._request("POST", f"/v2/payments/captures/{capture_id}/refund", json=body, request_id=request_id)

    # ------------------------------------------------------------------ Payouts v1

    async def create_payout(self, body: dict, request_id: str) -> dict:
        return await self._request("POST", "/v1/payments/payouts", json=body, request_id=request_id, prefer="")

    async def get_payout(self, batch_id: str) -> dict:
        return await self._request("GET", f"/v1/payments/payouts/{batch_id}", prefer="")

    # ------------------------------------------------------------------ Invoicing v2

    async def create_invoice(self, body: dict, request_id: str) -> dict:
        return await self._request("POST", "/v2/invoicing/invoices", json=body, request_id=request_id)

    async def send_invoice(self, invoice_id: str, body: dict, request_id: str) -> dict:
        return await self._request("POST", f"/v2/invoicing/invoices/{invoice_id}/send", json=body, request_id=request_id, prefer="")

    async def get_invoice(self, invoice_id: str) -> dict:
        return await self._request("GET", f"/v2/invoicing/invoices/{invoice_id}", prefer="")

    # ------------------------------------------------------------------ Webhooks

    async def verify_webhook(self, headers: dict[str, str], event: dict) -> bool:
        if not self.webhook_id:
            return False
        h = {k.lower(): v for k, v in headers.items()}
        body = {
            "auth_algo": h.get("paypal-auth-algo", ""),
            "cert_url": h.get("paypal-cert-url", ""),
            "transmission_id": h.get("paypal-transmission-id", ""),
            "transmission_sig": h.get("paypal-transmission-sig", ""),
            "transmission_time": h.get("paypal-transmission-time", ""),
            "webhook_id": self.webhook_id,
            "webhook_event": event,
        }
        if not all(body[k] for k in ("auth_algo", "cert_url", "transmission_id", "transmission_sig", "transmission_time")):
            return False
        res = await self._request("POST", "/v1/notifications/verify-webhook-signature", json=body, prefer="")
        return res.get("verification_status") == "SUCCESS"


def approve_link(order: dict) -> Optional[str]:
    """The buyer approval URL: rel=payer-action when payment_source.paypal is used, rel=approve otherwise."""
    for rel in ("payer-action", "approve"):
        for link in order.get("links", []):
            if link.get("rel") == rel:
                return link.get("href")
    return None


def invoice_id_from(resp: dict) -> Optional[str]:
    """Create-invoice returns the full invoice with Prefer: return=representation, or only a link otherwise."""
    if resp.get("id"):
        return resp["id"]
    href = resp.get("href") or next((l.get("href") for l in resp.get("links", []) if l.get("rel") == "self"), None)
    return href.rstrip("/").rsplit("/", 1)[-1] if href else None
