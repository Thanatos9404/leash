"""The real sandbox REST client against a mock transport (no network): auth, idempotency headers, retries, the
AUTHORIZE -> capture -> void flow as Leash sends it, multipart dispute evidence and webhook verification."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from leash.app import build_leash
from leash.paypal import PayPalError, RestPayPal, approve_link, first_authorization

from .conftest import make_settings, run


class FakeSandbox:
    """Answers like the sandbox (response shapes from the public API reference) and records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.tokens = 0
        self.fail_next: list[int] = []
        self.order_status = "PAYER_ACTION_REQUIRED"

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        path, method = req.url.path, req.method
        if path == "/v1/oauth2/token":
            self.tokens += 1
            return httpx.Response(200, json={"access_token": f"TOKEN-{self.tokens}", "token_type": "Bearer", "expires_in": 32400})
        if self.fail_next:
            code = self.fail_next.pop(0)
            return httpx.Response(code, json={"name": "INTERNAL_SERVER_ERROR", "message": "x", "debug_id": "dbg1"})
        if path == "/v2/checkout/orders" and method == "POST":
            return httpx.Response(201, json={"id": "8MC585209K746392H", "status": "PAYER_ACTION_REQUIRED", "links": [
                {"href": "https://api-m.sandbox.paypal.com/v2/checkout/orders/8MC585209K746392H", "rel": "self", "method": "GET"},
                {"href": "https://www.sandbox.paypal.com/checkoutnow?token=8MC585209K746392H", "rel": "payer-action", "method": "GET"}]})
        if path.startswith("/v2/checkout/orders/") and method == "GET":
            return httpx.Response(200, json={"id": "8MC585209K746392H", "status": self.order_status})
        if path.endswith("/authorize"):
            return httpx.Response(201, json={"id": "8MC585209K746392H", "status": "COMPLETED", "purchase_units": [{"payments": {"authorizations": [
                {"id": "0VF52814937998046", "status": "CREATED", "amount": {"currency_code": "USD", "value": "80.00"}, "expiration_time": "2026-11-10T10:00:00Z"}]}}]})
        if path.endswith("/capture"):
            body = json.loads(req.content)
            return httpx.Response(201, json={"id": "2GG279541U471931P", "status": "COMPLETED", "amount": body["amount"], "final_capture": body.get("final_capture", False),
                                             "seller_receivable_breakdown": {"paypal_fee": {"currency_code": "USD", "value": "1.26"}}})
        if path.endswith("/void"):
            return httpx.Response(204)
        if path.startswith("/v1/customer/disputes") and path.endswith("/provide-evidence"):
            return httpx.Response(200, json={"links": []})
        if path == "/v1/notifications/verify-webhook-signature":
            body = json.loads(req.content)
            return httpx.Response(200, json={"verification_status": "SUCCESS" if body["transmission_sig"] == "good" else "FAILURE"})
        return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND", "message": "nope"})


@pytest.fixture
def fake():
    return FakeSandbox()


def _settings(tmp_path, **kw):
    return make_settings(tmp_path, paypal_client_id="CID", paypal_client_secret="SECRET", paypal_mode_pref="auto", paypal_webhook_id="WH-1", **kw)


@pytest.fixture
def rest(tmp_path, fake):
    return RestPayPal(_settings(tmp_path), transport=httpx.MockTransport(fake))


def test_mode_selection(tmp_path):
    assert make_settings(tmp_path).paypal_mode == "fixture"
    assert _settings(tmp_path).paypal_mode == "sandbox"
    assert rest_host(tmp_path) == "https://api-m.sandbox.paypal.com"


def rest_host(tmp_path):
    return RestPayPal(_settings(tmp_path)).base_url


def test_oauth_basic_auth_and_token_cache(rest, fake):
    run(rest.get_order("A"))
    run(rest.get_order("B"))
    tok = fake.requests[0]
    assert tok.headers["authorization"] == "Basic " + base64.b64encode(b"CID:SECRET").decode()
    assert fake.tokens == 1 and fake.requests[1].headers["authorization"] == "Bearer TOKEN-1"


def test_expired_token_refreshes_once(rest, fake):
    run(rest.get_order("A"))
    fake.fail_next = [401]
    run(rest.get_order("A"))
    assert fake.tokens == 2


def test_5xx_retried_with_the_same_request_id(rest, fake, monkeypatch):
    async def no_sleep(_):
        return None
    monkeypatch.setattr("leash.paypal.client.asyncio.sleep", no_sleep)
    fake.fail_next = [503]
    run(rest.capture_authorization("AUTH", {"amount": {"currency_code": "USD", "value": "1.00"}}, "leash-pur_1-capture"))
    caps = [r for r in fake.requests if r.url.path.endswith("/capture")]
    assert len(caps) == 2 and {r.headers["paypal-request-id"] for r in caps} == {"leash-pur_1-capture"}


def test_errors_parse(rest):
    with pytest.raises(PayPalError) as e:
        run(rest.get_dispute("nope"))
    assert e.value.status == 404 and e.value.name == "RESOURCE_NOT_FOUND"


def test_helpers():
    assert approve_link({"links": [{"rel": "payer-action", "href": "x"}]}) == "x"
    assert first_authorization({"purchase_units": [{"payments": {"authorizations": [{"id": "A"}]}}]})["id"] == "A"


def test_dispute_evidence_is_multipart(rest, fake):
    run(rest.provide_evidence("PP-D-1", {"evidences": [{"evidence_type": "OTHER", "notes": "n"}]}, "r.pdf", b"%PDF-1.4 x", "k"))
    req = fake.requests[-1]
    assert req.headers["content-type"].startswith("multipart/form-data") and b'name="input"' in req.content and b"r.pdf" in req.content


def test_webhook_verification(rest, fake):
    h = {"PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/c", "PAYPAL-TRANSMISSION-ID": "t",
         "PAYPAL-TRANSMISSION-SIG": "good", "PAYPAL-TRANSMISSION-TIME": "2026-10-12T00:00:00Z"}
    assert run(rest.verify_webhook(h, {"id": "E"}))
    assert not run(rest.verify_webhook({**h, "PAYPAL-TRANSMISSION-SIG": "bad"}, {"id": "E"}))
    assert not run(rest.verify_webhook({}, {"id": "E"}))


def test_engine_flow_against_the_rest_client(tmp_path, fake):
    s = _settings(tmp_path)
    l = build_leash(s, paypal=RestPayPal(s, transport=httpx.MockTransport(fake)))
    from leash.compiler import SAMPLES

    m = run(l.compile(SAMPLES["coffee"]))
    m = run(l.sign(m.id))
    m = run(l.fund(m.id))
    body = json.loads([r for r in fake.requests if r.url.path == "/v2/checkout/orders"][0].content)
    assert body["intent"] == "AUTHORIZE" and body["purchase_units"][0]["amount"]["value"] == "80.00"
    assert body["payment_source"]["paypal"]["experience_context"]["return_url"].startswith("http://testserver/paypal/return?kind=allowance")
    assert m.envelope.approve_url.startswith("https://www.sandbox.paypal.com/checkoutnow")
    fake.order_status = "APPROVED"
    run(l.on_paypal_return("allowance", m.id))
    m = l.mandate(m.id)
    assert m.status == "active" and m.envelope.authorization_id == "0VF52814937998046"
    p = run(l.run_agent(m.id, "a 1 kg bag of medium roast whole beans"))
    assert p.status == "captured" and p.payment["capture_id"] == "2GG279541U471931P"
    cap = [r for r in fake.requests if r.url.path.endswith("/capture")][-1]
    sent = json.loads(cap.content)
    assert sent["final_capture"] is False and sent["invoice_id"] == p.id and cap.headers["paypal-request-id"] == f"leash-{p.id}-capture"
    run(l.revoke(m.id))
    assert any(r.url.path == "/v2/payments/authorizations/0VF52814937998046/void" for r in fake.requests)
    l.store.close()
