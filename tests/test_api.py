"""HTTP API end to end through FastAPI's TestClient, including the simulated PayPal approval page."""

from __future__ import annotations

from fastapi.testclient import TestClient

from leash.app import build_leash, create_app
from leash.compiler import SAMPLES

from .conftest import make_settings


def _client(tmp_path):
    l = build_leash(make_settings(tmp_path))
    return TestClient(create_app(l)), l


def test_journey_over_http(tmp_path):
    c, l = _client(tmp_path)
    with c:
        st = c.get("/api/status").json()
        assert st["paypal_mode"] == "fixture" and "coffee" in st["samples"] and "injection" in st["scenarios"]
        m = c.post("/api/mandates", json={"text": SAMPLES["coffee"]}).json()
        assert m["status"] == "draft" and m["plain_language"] and "token" not in m
        m = c.patch(f"/api/mandates/{m['id']}", json={"ask_above": "24"}).json()
        assert any("$24.00" in line for line in m["plain_language"])
        m = c.post(f"/api/mandates/{m['id']}/sign", json={}).json()
        assert m["status"] == "signed" and m["signature_ok"]
        m = c.post(f"/api/mandates/{m['id']}/fund").json()
        assert m["status"] == "awaiting_funding"
        token = m["envelope"]["order_id"]
        assert "Fixture mode" in c.get(f"/fixture/paypal/checkoutnow?token={token}").text
        r = c.post(f"/fixture/paypal/checkoutnow/approve?token={token}", follow_redirects=False)
        r = c.get(r.headers["location"], follow_redirects=False)
        assert r.status_code == 303 and "funded=1" in r.headers["location"]
        m = c.get(f"/api/mandates/{m['id']}").json()
        assert m["status"] == "active" and m["allowance_left"] == "80.00"

        p = c.post(f"/api/mandates/{m['id']}/agent", json={"task": "a 1 kg bag of medium roast whole beans"}).json()
        assert p["status"] == "captured" and p["receipt_payload"]["payment"]["capture_id"]
        b = c.post(f"/api/mandates/{m['id']}/scenario", json={"name": "spoof"}).json()
        assert b["status"] == "blocked"

        jwks = c.get("/.well-known/jwks.json").json()
        assert any(k["kid"] == "leash-receipt-1" for k in jwks["keys"])
        v = c.post("/v1/verify", json={"receipt": p["receipt"], "merchant_id": p["cart"]["merchant_id"]}).json()
        assert not v["valid"]  # settlement receipts are evidence, not a licence to fulfil again

        ev = c.get(f"/api/purchases/{p['id']}/evidence.pdf")
        assert ev.status_code == 200 and ev.content.startswith(b"%PDF")
        assert c.get(f"/api/purchases/{p['id']}/evidence.json").json()["format"] == "leash.evidence/1"

        d = c.get("/api/dashboard").json()
        assert d["stats"]["blocked_30d"] == 1 and d["stats"]["purchases_30d"] == 1
        k = c.post("/api/kill-switch", json={"on": True}).json()
        assert k["frozen"] and m["id"] in k["revoked"]
        assert c.get("/api/ledger/verify").json()["ok"]


def test_errors_are_json(tmp_path):
    c, l = _client(tmp_path)
    with c:
        assert c.get("/api/mandates/mdt_nope").status_code == 409
        assert c.post("/api/mandates", json={"text": "   "}).status_code == 409
        m = c.post("/api/mandates", json={"text": SAMPLES["gift"]}).json()
        assert c.post(f"/api/mandates/{m['id']}/fund").status_code == 409  # must be signed first


def test_webhook_signature_required(tmp_path):
    c, l = _client(tmp_path)
    with c:
        assert c.post("/webhooks/paypal", json={"id": "W1", "event_type": "X", "resource": {}}).status_code == 401
        ok = c.post("/webhooks/paypal", json={"id": "W2", "event_type": "X", "resource": {}}, headers={"paypal-transmission-sig": "fixture-signature"})
        assert ok.json()["status"] == "processed"
