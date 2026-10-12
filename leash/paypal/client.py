"""PayPal REST client, sandbox only.

APIs used (https://api-m.sandbox.paypal.com):
  POST /v1/oauth2/token                                   client-credentials access token
  POST /v2/checkout/orders                                intent=AUTHORIZE: the allowance, or a one-off cart
  GET  /v2/checkout/orders/{id}                           order status after buyer approval
  POST /v2/checkout/orders/{id}/authorize                 place the authorization (funds held)
  GET  /v2/payments/authorizations/{id}                   authorization status
  POST /v2/payments/authorizations/{id}/capture           capture one purchase (final_capture=false keeps it open)
  POST /v2/payments/authorizations/{id}/void              release what is left (revoke, kill switch, rejection)
  POST /v2/payments/captures/{id}/refund                  refunds
  GET  /v1/customer/disputes, /v1/customer/disputes/{id}  disputes on agent purchases
  POST /v1/customer/disputes/{id}/provide-evidence        attach the signed intent receipt as evidence
  POST /v1/notifications/verify-webhook-signature         webhook authenticity

Every write carries a PayPal-Request-Id derived from the purchase or mandate, so a retry can never charge twice.
Secrets and tokens are never logged; the call log keeps method, path, status, debug id and request id.
"""

from __future__ import annotations

import asyncio
import base64
import json
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

    @property
    def issue(self) -> str:
        if isinstance(self.details, list) and self.details:
            return str(self.details[0].get("issue", ""))
        return ""


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
        return self.__dict__.copy()


class RestPayPal:
    mode = "sandbox"

    def __init__(self, settings: Settings, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        if settings.paypal_env != "sandbox":
            raise SandboxOnlyError("RestPayPal only supports the sandbox.")
        if not (settings.paypal_client_id and settings.paypal_client_secret):
            raise SandboxOnlyError("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are required for sandbox mode.")
        self.base_url = PAYPAL_SANDBOX_API
        if "sandbox" not in self.base_url:  # belt and braces: never a live host
            raise SandboxOnlyError("non-sandbox PayPal host")
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

    # ------------------------------------------------------------------ auth and transport

    async def _access_token(self, force: bool = False) -> str:
        async with self._lock:
            if self._token and not force and time.time() < self._token_exp - 60:
                return self._token
            basic = base64.b64encode(f"{self._id}:{self._secret}".encode()).decode()
            t0 = time.perf_counter()
            r = await self._http.post("/v1/oauth2/token", data={"grant_type": "client_credentials"},
                                      headers={"Authorization": f"Basic {basic}", "Accept": "application/json"})
            self._log("POST", "/v1/oauth2/token", r, "", t0)
            if r.status_code != 200:
                raise self._error(r)
            body = r.json()
            self._token = body["access_token"]
            self._token_exp = time.time() + int(body.get("expires_in", 3600))
            return self._token

    async def _request(self, method: str, path: str, *, json_body: Any = None, request_id: str = "",
                       prefer: str = "return=representation", files: Any = None) -> dict[str, Any]:
        refresh = False
        retryable = method == "GET" or bool(request_id)  # a write is only retried under the same request id
        for attempt in range(3):
            token = await self._access_token(force=refresh)
            refresh = False
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            if files is None:
                headers["Content-Type"] = "application/json"
            if request_id:
                headers["PayPal-Request-Id"] = request_id
            if prefer:
                headers["Prefer"] = prefer
            t0 = time.perf_counter()
            try:
                if files is not None:
                    r = await self._http.request(method, path, files=files, headers=headers)
                else:
                    r = await self._http.request(method, path, json=json_body, headers=headers)
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
        self.calls.append(CallRecord(time.time(), method, path, r.status_code, request_id, r.headers.get("paypal-debug-id", ""),
                                     self.mode, int((time.perf_counter() - t0) * 1000)))
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
        return await self._request("POST", "/v2/checkout/orders", json_body=body, request_id=request_id)

    async def get_order(self, order_id: str) -> dict:
        return await self._request("GET", f"/v2/checkout/orders/{order_id}", prefer="")

    async def authorize_order(self, order_id: str, request_id: str) -> dict:
        return await self._request("POST", f"/v2/checkout/orders/{order_id}/authorize", json_body={}, request_id=request_id)

    # ------------------------------------------------------------------ Payments v2

    async def get_authorization(self, authorization_id: str) -> dict:
        return await self._request("GET", f"/v2/payments/authorizations/{authorization_id}", prefer="")

    async def capture_authorization(self, authorization_id: str, body: dict, request_id: str) -> dict:
        return await self._request("POST", f"/v2/payments/authorizations/{authorization_id}/capture", json_body=body, request_id=request_id)

    async def void_authorization(self, authorization_id: str, request_id: str) -> dict:
        return await self._request("POST", f"/v2/payments/authorizations/{authorization_id}/void", request_id=request_id)

    async def refund_capture(self, capture_id: str, body: dict, request_id: str) -> dict:
        return await self._request("POST", f"/v2/payments/captures/{capture_id}/refund", json_body=body, request_id=request_id)

    # ------------------------------------------------------------------ Disputes v1

    async def list_disputes(self) -> dict:
        return await self._request("GET", "/v1/customer/disputes?page_size=50", prefer="")

    async def get_dispute(self, dispute_id: str) -> dict:
        return await self._request("GET", f"/v1/customer/disputes/{dispute_id}", prefer="")

    async def provide_evidence(self, dispute_id: str, evidence: dict, filename: str, content: bytes, request_id: str) -> dict:
        files = [
            ("input", (None, json.dumps(evidence), "application/json")),
            ("file1", (filename, content, "application/pdf")),
        ]
        return await self._request("POST", f"/v1/customer/disputes/{dispute_id}/provide-evidence", files=files, request_id=request_id, prefer="")

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
        res = await self._request("POST", "/v1/notifications/verify-webhook-signature", json_body=body, prefer="")
        return res.get("verification_status") == "SUCCESS"


def approve_link(order: dict) -> Optional[str]:
    """Buyer approval URL: rel=payer-action when payment_source.paypal is used, rel=approve otherwise."""
    for rel in ("payer-action", "approve"):
        for link in order.get("links", []):
            if link.get("rel") == rel:
                return link.get("href")
    return None


def first_authorization(order: dict) -> dict:
    return ((order.get("purchase_units") or [{}])[0].get("payments") or {}).get("authorizations", [{}])[0]
