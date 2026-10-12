"""Lifecycle: allowance, automatic purchase, step-up, rejection, exceptions, refunds, disputes, money conservation."""

from __future__ import annotations

from decimal import Decimal

from leash.crypto import jws_peek, jws_verify
from leash.receipts import RECEIPT_TYP

from .conftest import active_mandate, run


def test_allowance_is_a_paypal_authorization(leash):
    m = run(active_mandate(leash))
    assert m.status == "active" and m.envelope.amount == Decimal("80.00")
    order = leash.paypal.state["orders"][m.envelope.order_id]
    assert order["intent"] == "AUTHORIZE" and order["status"] == "COMPLETED"
    assert leash.paypal.state["auths"][m.envelope.authorization_id]["status"] == "CREATED"


def test_automatic_purchase_produces_verified_chained_receipts(leash):
    m = run(active_mandate(leash))
    p1 = run(leash.run_agent(m.id, "a 1 kg bag of medium roast whole beans"))
    assert p1.status == "captured" and p1.approval["method"] == "auto" and p1.verification["ok"]
    auth = leash.paypal.state["auths"][m.envelope.authorization_id]
    assert auth["status"] == "PARTIALLY_CAPTURED" and Decimal(auth["captured"]) == p1.cart.total
    _, payload = jws_verify(p1.receipt, leash.receipt_key.public, RECEIPT_TYP)
    assert payload["payment"]["capture_id"] == p1.payment["capture_id"] and payload["cart"]["hash"] == p1.cart.cart_hash
    assert payload["prev"] is None
    p2 = run(leash.run_agent(m.id, "Swiss Water decaf 500 g"))
    assert p2.status == "captured"
    assert jws_peek(p2.receipt)[1]["prev"] == p1.receipt_hash  # chained per mandate


def test_step_up_approve_and_reject(leash):
    m = run(active_mandate(leash))
    p = run(leash.run_agent(m.id, "Colombia Huila single origin 1 kg"))
    assert p.status == "awaiting_approval" and p.decision.outcome == "ask"
    p = run(leash.approve(p.id))
    assert p.status == "captured" and p.approval["method"] == "user-signature"
    q = run(leash.run_agent(m.id, "Espresso roast whole beans 1 kg"))
    assert q.status in ("captured", "awaiting_approval")
    r = run(leash.run_agent(m.id, "Northside organic medium roast"))
    if r.status == "awaiting_approval":
        r = run(leash.reject(r.id, reason="Not now"))
        assert r.status == "rejected"


def test_exception_path_authorizes_verifies_and_captures(leash):
    m = run(active_mandate(leash))
    p = run(leash.run_scenario(m.id, "over_cap"))
    p = run(leash.approve_exception(p.id))
    assert p.status == "awaiting_paypal" and p.payment["approve_url"]
    leash.paypal.approve_order(p.payment["order_id"])
    run(leash.on_paypal_return("exception", p.id))
    p = leash.purchase(p.id)
    assert p.status == "captured" and p.payment["mode"] == "exception"
    assert leash.paypal.state["auths"][p.payment["authorization_id"]]["status"] == "CAPTURED"


def test_exception_is_voided_if_mandate_revoked_before_paypal_approval(leash):
    m = run(active_mandate(leash))
    p = run(leash.run_scenario(m.id, "over_cap"))
    p = run(leash.approve_exception(p.id))
    leash.paypal.approve_order(p.payment["order_id"])
    run(leash.revoke(m.id))  # revoked while the user was on PayPal
    run(leash.on_paypal_return("exception", p.id))
    p = leash.purchase(p.id)
    assert p.status in ("voided", "rejected")
    assert not p.payment.get("capture_id")


def test_refund_and_dispute_evidence(leash):
    m = run(active_mandate(leash))
    p = run(leash.run_agent(m.id, "a 1 kg bag of medium roast whole beans"))
    p = run(leash.refund(p.id, "5.00", "One bag arrived damaged"))
    assert p.refunds[0]["amount"] == "5.00" and p.status == "captured"
    p = leash.simulate_dispute(p.id)
    assert p.dispute["status"] == "WAITING_FOR_SELLER_RESPONSE"
    bundle, pdf = run(leash.evidence(p.id))
    assert bundle["receipt"] == p.receipt and bundle["mandate"]["token"] and pdf.startswith(b"%PDF")
    p = run(leash.respond_to_dispute(p.id))
    assert p.dispute["status"] == "UNDER_REVIEW"
    d = leash.paypal.state["disputes"][p.dispute["id"]]
    assert d["evidences"][0]["documents"][0]["name"].endswith(".pdf")


def test_money_is_conserved(leash):
    m = run(active_mandate(leash))
    for task in ("a 1 kg bag of medium roast whole beans", "Swiss Water decaf 500 g"):
        run(leash.run_agent(m.id, task))
    m = leash.mandate(m.id)
    caps = sum(Decimal(c["amount"]["value"]) for c in leash.paypal.state["captures"].values())
    assert caps == m.envelope.captured and leash.allowance_left(m) == m.envelope.amount - caps
    m = run(leash.revoke(m.id))
    assert leash.allowance_left(m) == Decimal("0.00")
    assert leash.store.verify_chain()["ok"]
