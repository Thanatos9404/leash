"""HTTP API through FastAPI's TestClient: the full journey including the simulated PayPal pages and webhooks."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sauda.app import build_engine, create_app
from sauda.mandate import SAMPLE_BRIEF

from .conftest import make_settings


def _client(tmp_path):
    eng = build_engine(make_settings(tmp_path))
    return TestClient(create_app(eng)), eng


def _wait_proposed(c, deal_id):
    import time

    for _ in range(100):
        d = c.get(f"/api/deals/{deal_id}").json()
        if d["status"] != "negotiating":
            return d
        time.sleep(0.05)
    raise AssertionError("negotiation did not finish")


def test_full_journey_over_http(tmp_path):
    c, eng = _client(tmp_path)
    with c:
        assert c.get("/api/status").json()["paypal_mode"] == "fixture"
        cat = c.get("/api/catalog").json()
        assert all("floor_price" not in l for s in cat["sellers"] for l in s["listings"])  # floors never leave the server

        d = c.post("/api/deals", json={"brief": SAMPLE_BRIEF}).json()
        assert d["status"] == "mandate_draft"
        d = c.post(f"/api/deals/{d['id']}/mandate", json={"signer": "Anita Shah", "edits": {"budget_total": "1150"}}).json()
        assert d["status"] == "mandate_signed" and d["mandate"]["signature"]
        c.post(f"/api/deals/{d['id']}/negotiate")
        d = _wait_proposed(c, d["id"])
        assert d["status"] == "proposed" and d["pending"] == ["A1"]
        assert "floor_price" not in str(d)

        d = c.post(f"/api/deals/{d['id']}/actions/A1/approve", json={"approver": "Anita Shah"}).json()
        assert d["status"] == "awaiting_buyer"
        token = d["paypal"]["order_id"]
        page = c.get(f"/fixture/paypal/checkoutnow?token={token}")
        assert "Fixture mode" in page.text and "no money moved" in page.text
        r = c.post(f"/fixture/paypal/checkoutnow/approve?token={token}", follow_redirects=False)
        assert r.status_code == 303 and "/paypal/return" in r.headers["location"]
        r = c.get(r.headers["location"], follow_redirects=False)
        assert r.status_code == 303
        d = c.get(f"/api/deals/{d['id']}").json()
        assert d["status"] == "in_fulfilment" and d["paypal"]["capture_id"]

        for sid in sorted({l["seller_id"] for l in d["lines"]}):
            prev = c.post(f"/api/deals/{d['id']}/dispatch/preview", json={"seller_id": sid, "text": "All shipped, tracking EE123456789IN"}).json()
            assert prev["tracking_number"] == "EE123456789IN"
            d = c.post(f"/api/deals/{d['id']}/dispatch", json={"seller_id": sid, "report": prev, "text": "All shipped"}).json()
        for aid in list(d["pending"]):
            d = c.post(f"/api/deals/{d['id']}/actions/{aid}/approve", json={"approver": "Anita Shah"}).json()
        assert d["status"] == "invoiced"

        inv = d["paypal"]["invoice_id"]
        assert "Pay invoice" in c.get(f"/fixture/paypal/invoice/{inv}").text
        r = c.post(f"/fixture/paypal/invoice/{inv}/pay", follow_redirects=False)
        assert r.status_code == 303
        d = c.get(f"/api/deals/{d['id']}").json()
        assert d["status"] == "settling" and len(d["pending"]) == 1
        d = c.post(f"/api/deals/{d['id']}/actions/{d['pending'][0]}/approve", json={"approver": "Anita Shah"}).json()
        for _ in range(3):
            d = c.post(f"/api/deals/{d['id']}/sync").json()
        assert d["status"] == "settled" and d["summary"]["held"] == "0.00"

        assert c.get("/api/ledger/verify").json()["ok"]
        assert c.get(f"/api/ledger?deal_id={d['id']}").json()
        calls = c.get("/api/paypal/calls").json()
        assert any(x["path"] == "/v1/payments/payouts" for x in calls)


def test_webhook_rejects_bad_signature_and_dedups(tmp_path):
    c, eng = _client(tmp_path)
    with c:
        r = c.post("/webhooks/paypal", json={"id": "WH-1", "event_type": "INVOICING.INVOICE.PAID", "resource": {}})
        assert r.status_code == 401
        h = {"paypal-transmission-sig": "fixture-signature"}
        ok = c.post("/webhooks/paypal", json={"id": "WH-2", "event_type": "INVOICING.INVOICE.PAID", "resource": {"id": "none"}}, headers=h)
        assert ok.json()["status"] == "ignored"
        again = c.post("/webhooks/paypal", json={"id": "WH-2", "event_type": "INVOICING.INVOICE.PAID", "resource": {"id": "none"}}, headers=h)
        assert again.json()["status"] == "duplicate"


def test_errors_are_json(tmp_path):
    c, eng = _client(tmp_path)
    with c:
        assert c.get("/api/deals/SD-NOPE").status_code == 409
        assert c.post("/api/deals", json={"brief": "   "}).status_code == 409
        d = c.post("/api/deals", json={"brief": SAMPLE_BRIEF}).json()
        bad = c.post(f"/api/deals/{d['id']}/mandate", json={"edits": {"items": [{"product": "gold", "qty_kg": 1}]}})
        assert bad.status_code == 422
        assert c.post(f"/api/deals/{d['id']}/negotiate").status_code == 409  # not signed yet
