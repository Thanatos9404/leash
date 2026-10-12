"""The real sandbox REST client, exercised against a mock transport (no network): auth, headers,
idempotency keys, retries, error parsing and the request bodies Sauda sends."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from sauda.app import build_engine
from sauda.paypal import PayPalError, RestPayPal, approve_link, invoice_id_from

from .conftest import make_settings, run
from .test_flow_e2e import negotiate_to_proposal


class FakePayPal:
    """Records every request and answers like the sandbox would (shapes from the public API reference)."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.token_calls = 0
        self.fail_next: list[int] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        path = req.url.path
        if path == "/v1/oauth2/token":
            self.token_calls += 1
            return httpx.Response(200, json={"access_token": f"TOKEN-{self.token_calls}", "token_type": "Bearer", "expires_in": 32400, "app_id": "APP-TEST"})
        if self.fail_next:
            code = self.fail_next.pop(0)
            return httpx.Response(code, json={"name": "INTERNAL_SERVER_ERROR" if code >= 500 else "AUTHENTICATION_FAILURE", "message": "x", "debug_id": "dbg123"},
                                  headers={"paypal-debug-id": "dbg123"})
        body = json.loads(req.content or b"{}")
        if path == "/v2/checkout/orders" and req.method == "POST":
            return httpx.Response(201, json={"id": "5O190127TN364715T", "status": "PAYER_ACTION_REQUIRED", "payment_source": {"paypal": {}},
                                             "links": [{"href": "https://api-m.sandbox.paypal.com/v2/checkout/orders/5O190127TN364715T", "rel": "self", "method": "GET"},
                                                       {"href": "https://www.sandbox.paypal.com/checkoutnow?token=5O190127TN364715T", "rel": "payer-action", "method": "GET"}]})
        if path.startswith("/v2/checkout/orders/") and req.method == "GET":
            return httpx.Response(200, json={"id": path.rsplit("/", 1)[-1], "status": "APPROVED"})
        if path.endswith("/capture"):
            return httpx.Response(201, json={"id": "5O190127TN364715T", "status": "COMPLETED", "purchase_units": [{"payments": {"captures": [
                {"id": "3C679366HH908993F", "status": "COMPLETED", "amount": {"currency_code": "USD", "value": "100.00"},
                 "seller_receivable_breakdown": {"paypal_fee": {"currency_code": "USD", "value": "3.98"}}}]}}],
                "payer": {"payer_id": "QYR5Z8XDVJNXQ"}})
        if path == "/v1/payments/payouts":
            return httpx.Response(201, json={"batch_header": {"payout_batch_id": "5UXD2E8A7EBQJ", "batch_status": "PENDING", "sender_batch_header": body["sender_batch_header"]}})
        if path == "/v2/invoicing/invoices":
            return httpx.Response(201, json={"rel": "self", "href": "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-Z56S-5LLA-Q52L-CPZ5", "method": "GET"})
        if path.endswith("/send"):
            return httpx.Response(200, json={"rel": "payer-view", "href": "https://www.sandbox.paypal.com/invoice/p/#Z56S5LLAQ52LCPZ5", "method": "GET"})
        if path == "/v1/notifications/verify-webhook-signature":
            return httpx.Response(200, json={"verification_status": "SUCCESS" if body.get("transmission_sig") == "good" else "FAILURE"})
        if "/refund" in path:
            return httpx.Response(201, json={"id": "1JU08902781691411", "status": "COMPLETED"})
        return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND", "message": "nope"})


@pytest.fixture
def fake():
    return FakePayPal()


@pytest.fixture
def rest(tmp_path, fake):
    s = make_settings(tmp_path, paypal_client_id="CID", paypal_client_secret="SECRET", paypal_mode_pref="auto", paypal_webhook_id="WH-1")
    return RestPayPal(s, transport=httpx.MockTransport(fake))


def test_mode_selection(tmp_path):
    assert make_settings(tmp_path).paypal_mode == "fixture"
    assert make_settings(tmp_path, paypal_client_id="a", paypal_client_secret="b", paypal_mode_pref="auto").paypal_mode == "sandbox"
    assert make_settings(tmp_path, paypal_client_id="a", paypal_client_secret="b", paypal_mode_pref="fixture").paypal_mode == "fixture"


def test_only_sandbox_host(rest):
    assert rest.base_url == "https://api-m.sandbox.paypal.com"


def test_oauth_basic_auth_and_token_cache(rest, fake):
    run(rest.get_order("X"))
    run(rest.get_order("Y"))
    tok = fake.requests[0]
    assert tok.url.path == "/v1/oauth2/token"
    assert tok.headers["authorization"] == "Basic " + base64.b64encode(b"CID:SECRET").decode()
    assert b"grant_type=client_credentials" in tok.content
    assert fake.token_calls == 1  # cached
    assert fake.requests[1].headers["authorization"] == "Bearer TOKEN-1"


def test_writes_carry_request_id_and_prefer(rest, fake):
    run(rest.create_order({"intent": "CAPTURE", "purchase_units": []}, "sauda-SD-1-A1-create_order"))
    req = fake.requests[-1]
    assert req.headers["paypal-request-id"] == "sauda-SD-1-A1-create_order"
    assert req.headers["prefer"] == "return=representation"
    assert req.headers["content-type"] == "application/json"


def test_expired_token_is_refreshed_once(rest, fake):
    run(rest.get_order("X"))
    fake.fail_next = [401]
    run(rest.get_order("X"))
    assert fake.token_calls == 2


def test_5xx_retried_with_same_request_id(rest, fake, monkeypatch):
    async def no_sleep(_):
        return None
    monkeypatch.setattr("sauda.paypal.client.asyncio.sleep", no_sleep)
    fake.fail_next = [503]
    run(rest.create_payout({"sender_batch_header": {"sender_batch_id": "k"}, "items": []}, "k"))
    posts = [r for r in fake.requests if r.url.path == "/v1/payments/payouts"]
    assert len(posts) == 2 and {r.headers["paypal-request-id"] for r in posts} == {"k"}


def test_errors_are_parsed(rest, fake):
    with pytest.raises(PayPalError) as e:
        run(rest.get_invoice("missing"))
    assert e.value.status == 404 and e.value.name == "RESOURCE_NOT_FOUND"


def test_helpers():
    assert approve_link({"links": [{"rel": "self", "href": "a"}, {"rel": "payer-action", "href": "b"}]}) == "b"
    assert approve_link({"links": [{"rel": "approve", "href": "c"}]}) == "c"
    assert invoice_id_from({"rel": "self", "href": "https://x/v2/invoicing/invoices/INV2-AAAA"}) == "INV2-AAAA"
    assert invoice_id_from({"id": "INV2-B"}) == "INV2-B"


def test_webhook_verification(rest, fake):
    headers = {"PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/cert", "PAYPAL-TRANSMISSION-ID": "t1",
               "PAYPAL-TRANSMISSION-SIG": "good", "PAYPAL-TRANSMISSION-TIME": "2026-10-12T10:00:00Z"}
    assert run(rest.verify_webhook(headers, {"id": "WH-EVT"}))
    sent = json.loads(fake.requests[-1].content)
    assert sent["webhook_id"] == "WH-1" and sent["webhook_event"] == {"id": "WH-EVT"}
    assert not run(rest.verify_webhook({**headers, "PAYPAL-TRANSMISSION-SIG": "bad"}, {"id": "x"}))
    assert not run(rest.verify_webhook({}, {"id": "x"}))  # missing headers: rejected without calling PayPal


def test_engine_against_rest_client(tmp_path, fake):
    """The engine's real request bodies, as sent through the REST client."""
    s = make_settings(tmp_path, paypal_client_id="CID", paypal_client_secret="SECRET", paypal_mode_pref="auto")
    eng = build_engine(s, paypal=RestPayPal(s, transport=httpx.MockTransport(fake)))
    deal = run(negotiate_to_proposal(eng))
    deal = run(eng.approve(deal.id, deal.actions[0].id, "Anita"))
    assert deal.paypal.order_id == "5O190127TN364715T"
    assert deal.paypal.approve_url.startswith("https://www.sandbox.paypal.com/checkoutnow")
    body = json.loads([r for r in fake.requests if r.url.path == "/v2/checkout/orders"][0].content)
    pu = body["purchase_units"][0]
    assert body["intent"] == "CAPTURE" and pu["amount"]["currency_code"] == "USD"
    assert pu["amount"]["breakdown"]["item_total"]["value"] == pu["amount"]["value"]
    ctx = body["payment_source"]["paypal"]["experience_context"]
    assert ctx["return_url"].startswith("http://testserver/paypal/return") and ctx["user_action"] == "PAY_NOW"
    eng.store.close()
