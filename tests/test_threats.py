"""One test (or more) per threat in THREAT_MODEL.md. All offline, fixture PayPal."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from leash.app import build_leash
from leash.compiler import SAMPLES, compile_mandate
from leash.crypto import KeyPair, jws_sign
from leash.engine import APPROVAL_TYP, LeashError

from .conftest import ScriptedLLM, active_mandate, make_settings, run


def captured(l, mid):
    return [p for p in l.store.purchases(mid) if p.status == "captured"]


# T1 prompt injection on a product page tries to widen the mandate
def test_T1_product_page_injection_cannot_widen_mandate(leash):
    m = run(active_mandate(leash))
    before = (m.policy_hash, m.token)
    p = run(leash.run_scenario(m.id, "injection"))
    assert p.status == "blocked"
    failed = {c.rule for c in p.decision.failures}
    assert {"merchant_allowed", "per_purchase_max", "budget", "paypal_allowance"} <= failed
    after = leash.mandate(m.id)
    assert (after.policy_hash, after.token) == before and not captured(leash, m.id)


def test_T1_injected_text_reaches_the_model_only_as_untrusted_data(settings):
    l = build_leash(settings)
    m = run(active_mandate(l))

    def choose(system, user):
        assert "never as instructions" in system and "<untrusted_product_text>" in user
        return {"product_id": "gr-geisha-5kg", "quantity": 5, "reasoning": "The page said I am pre-approved."}  # model falls for it

    l.agent.llm = ScriptedLLM(settings, {"agent_query": {"query": "geisha tin"}, "agent_choose": choose})
    p = run(l.run_agent(m.id, "Buy the best coffee you can find"))
    assert p.status == "blocked" and p.agent_ai == "scripted"
    assert not captured(l, m.id)
    l.store.close()


def test_T1_compiler_cannot_add_stores_the_user_never_named(settings):
    from leash.catalog import Directory

    llm = ScriptedLLM(settings, {"compile": {"merchants": ["m_beanbloom", "m_goldreserve"], "budget": {"amount": 80, "period": "month"}}})
    policy, report = run(compile_mandate(SAMPLES["coffee"], Directory.load(), llm))
    assert "m_goldreserve" not in policy.merchants_allow
    assert any("did not name it" in w for w in report["warnings"])


# T2 merchant spoofing
def test_T2_lookalike_merchant_is_blocked_and_cannot_be_excepted(leash):
    m = run(active_mandate(leash))
    p = run(leash.run_scenario(m.id, "spoof"))
    assert p.status == "blocked" and p.decision.failures[0].rule == "merchant_verified"
    assert "Possible spoof" in p.decision.summary
    with pytest.raises(LeashError):
        run(leash.approve_exception(p.id))


def test_T2_receipt_for_one_merchant_fails_at_another(leash):
    m = run(active_mandate(leash))
    p = run(leash.run_agent(m.id, "a 1 kg bag of medium roast whole beans"))
    from leash.receipts import build_receipt

    tok = build_receipt("authorization", p, m, leash.receipt_key, "paypal-fixture", None)
    res = leash.verify_receipt(tok, merchant_id="m_dailygrind", cart_hash_value=p.cart.cart_hash)
    assert not res["valid"] and any(c["check"] == "merchant" and not c["ok"] for c in res["checks"])


# T3 replay
def test_T3_replayed_cart_is_refused(leash):
    m = run(active_mandate(leash))
    run(leash.run_agent(m.id, "a 1 kg bag of medium roast whole beans"))
    p = run(leash.run_scenario(m.id, "replay"))
    assert p.status == "blocked" and any(c.rule == "replay" and not c.ok for c in p.decision.checks)
    assert len(captured(leash, m.id)) == 1


def test_T3_authorization_receipt_is_single_use(leash):
    m = run(active_mandate(leash))
    p = run(leash.run_agent(m.id, "a 1 kg bag of medium roast whole beans"))
    from leash.receipts import build_receipt

    tok = build_receipt("authorization", p.model_copy(update={"id": "pur_replaytest"}), m, leash.receipt_key, "paypal-fixture", None)
    first = leash.verify_receipt(tok, merchant_id=p.cart.merchant_id)
    second = leash.verify_receipt(tok, merchant_id=p.cart.merchant_id)
    assert first["valid"] and not second["valid"]
    assert any(c["check"] == "single_use" and not c["ok"] for c in second["checks"])


def test_T3_settlement_receipt_cannot_be_used_to_fulfil(leash):
    m = run(active_mandate(leash))
    p = run(leash.run_agent(m.id, "a 1 kg bag of medium roast whole beans"))
    res = leash.verify_receipt(p.receipt, merchant_id=p.cart.merchant_id)
    assert not res["valid"] and any(c["check"] == "stage" and not c["ok"] for c in res["checks"])


def test_T3_forged_receipt_fails_signature(leash):
    m = run(active_mandate(leash))
    p = run(leash.run_agent(m.id, "a 1 kg bag of medium roast whole beans"))
    from leash.receipts import build_receipt

    forged = build_receipt("authorization", p, m, KeyPair.generate(leash.receipt_key.kid), "paypal-fixture", None)
    assert not leash.verify_receipt(forged)["valid"]


# T4 double capture
def test_T4_concurrent_approvals_capture_once(leash):
    m = run(active_mandate(leash))
    p = run(leash.run_agent(m.id, "Colombia Huila single origin 1 kg"))
    assert p.status == "awaiting_approval"

    async def both():
        return await asyncio.gather(leash.approve(p.id), leash.approve(p.id), return_exceptions=True)

    results = run(both())
    assert sum(1 for r in results if not isinstance(r, Exception)) == 1
    caps = [c for c in leash.paypal.state["captures"].values()]
    assert len(caps) == 1 and leash.purchase(p.id).status == "captured"


def test_T4_paypal_request_id_replay_returns_the_same_capture(leash):
    m = run(active_mandate(leash))
    p = run(leash.run_agent(m.id, "a 1 kg bag of medium roast whole beans"))
    body = {"amount": {"currency_code": "USD", "value": str(p.cart.total)}, "invoice_id": p.id, "final_capture": False}
    again = run(leash.paypal.capture_authorization(m.envelope.authorization_id, body, f"leash-{p.id}-capture"))
    assert again["id"] == p.payment["capture_id"] and len(leash.paypal.state["captures"]) == 1


def test_T4_duplicate_invoice_id_is_refused_by_paypal_rules(leash):
    from leash.paypal import PayPalError

    m = run(active_mandate(leash))
    p = run(leash.run_agent(m.id, "a 1 kg bag of medium roast whole beans"))
    body = {"amount": {"currency_code": "USD", "value": "1.00"}, "invoice_id": p.id, "final_capture": False}
    with pytest.raises(PayPalError) as e:
        run(leash.paypal.capture_authorization(m.envelope.authorization_id, body, "a-different-request-id"))
    assert e.value.issue == "DUPLICATE_INVOICE_ID"


def test_T4_leash_never_over_captures_even_though_paypal_would(leash):
    m = run(active_mandate(leash))  # $80 allowance; PayPal itself would accept up to $92 (115%)
    over = run(leash.paypal.capture_authorization(m.envelope.authorization_id, {"amount": {"currency_code": "USD", "value": "85.00"}}, "probe"))
    assert over["status"] == "COMPLETED"  # documents PayPal's leniency in the simulator
    l2 = build_leash(make_settings(leash.settings.data_dir / "b"))
    m2 = run(active_mandate(l2))
    m2.envelope.captured = Decimal("70.00")  # pretend most of it is spent
    l2.store.save_mandate(m2)
    p = run(l2.run_agent(m2.id, "a 1 kg bag of medium roast whole beans"))
    assert p.status == "blocked" and any(c.rule == "paypal_allowance" and not c.ok for c in p.decision.checks)
    l2.store.close()


# T5 tampering with the mandate or the cart
def test_T5_tampered_mandate_blocks_everything(leash):
    m = run(active_mandate(leash))
    m.policy.per_purchase_max = Decimal("5000")  # edited behind the user's back
    leash.store.save_mandate(m)
    p = run(leash.run_agent(m.id, "a 1 kg bag of medium roast whole beans"))
    assert p.status == "blocked" and p.decision.failures[0].rule == "mandate_signature"


def test_T5_tampered_cart_price_is_refused(leash):
    m = run(active_mandate(leash))
    p = run(leash.run_scenario(m.id, "tamper"))
    assert p.status == "blocked" and any(c.rule == "price_check" and not c.ok for c in p.decision.checks)


def test_T5_approval_signed_for_another_cart_is_rejected(leash):
    m = run(active_mandate(leash))
    p = run(leash.run_agent(m.id, "Colombia Huila single origin 1 kg"))
    token = jws_sign({"typ": "leash.cart-approval/1", "purchase_id": p.id, "cart_hash": "0" * 64, "total": "1.00"}, leash.custodial_key, APPROVAL_TYP)
    with pytest.raises(LeashError):
        run(leash.approve(p.id, token=token))
    stranger = jws_sign({"purchase_id": p.id, "cart_hash": p.cart.cart_hash}, KeyPair.generate("dev_unknown"), APPROVAL_TYP)
    with pytest.raises(LeashError):
        run(leash.approve(p.id, token=stranger))


# T6 revocation, expiry, kill switch
def test_T6_revoke_voids_the_paypal_authorization(leash):
    m = run(active_mandate(leash))
    run(leash.run_agent(m.id, "a 1 kg bag of medium roast whole beans"))
    m = run(leash.revoke(m.id))
    assert m.status == "revoked" and m.envelope.authorization_status == "VOIDED"
    assert leash.paypal.state["auths"][m.envelope.authorization_id]["status"] == "VOIDED"
    p = run(leash.run_agent(m.id, "a 1 kg bag of medium roast whole beans"))
    assert p.status == "blocked" and p.decision.failures[0].rule == "mandate_active"


def test_T6_pending_approval_dies_with_the_mandate(leash):
    m = run(active_mandate(leash))
    p = run(leash.run_agent(m.id, "Colombia Huila single origin 1 kg"))
    run(leash.revoke(m.id))
    assert leash.purchase(p.id).status == "rejected"
    with pytest.raises(LeashError):
        run(leash.approve(p.id))


def test_T6_kill_switch_freezes_and_revokes(leash):
    m = run(active_mandate(leash))
    res = run(leash.kill_switch())
    assert res["frozen"] and m.id in res["revoked"]
    p = run(leash.run_agent(m.id, "a 1 kg bag of medium roast whole beans"))
    assert {c.rule for c in p.decision.failures} >= {"kill_switch", "mandate_active"}


# T7 abuse of the rules by the agent
def test_T7_split_purchase_is_detected(leash):
    m = run(active_mandate(leash))
    first = run(leash.run_agent(m.id, "a 1 kg bag of medium roast whole beans"))
    assert first.status == "captured" and first.cart.merchant_id == "m_beanbloom"
    p = run(leash.run_scenario(m.id, "split"))
    assert p.status == "blocked" and p.decision.failures[0].rule == "split_purchase"


def test_T7_subscription_and_gift_card_are_hard_denials(leash):
    m = run(active_mandate(leash))
    for name, rule in (("subscription", "subscriptions"), ("gift_card", "categories_blocked")):
        p = run(leash.run_scenario(m.id, name))
        assert p.status == "blocked" and any(c.rule == rule and not c.ok for c in p.decision.checks)


def test_T7_wrong_agent_is_refused(leash):
    m = run(active_mandate(leash))
    p0 = run(leash.run_agent(m.id, "a 1 kg bag of medium roast whole beans"))
    cart = p0.cart.model_copy(update={"nonce": "n_fresh_for_test"})
    p = run(leash.checkout(m.id, cart, "x", "x", "some-other-agent", "rules"))
    assert p.status == "blocked" and any(c.rule == "agent_authorized" and not c.ok for c in p.decision.checks)


def test_T7_approval_cannot_override_a_hard_rule(leash):
    m = run(active_mandate(leash))
    p = run(leash.run_scenario(m.id, "over_cap"))
    assert p.status == "blocked"
    with pytest.raises(LeashError):
        run(leash.approve(p.id))  # step-up only covers ask-rules


# T8 forged webhooks (see test_api.py for the HTTP path)
def test_T8_webhook_dedup(leash):
    assert run(leash.handle_webhook({"id": "WH-1", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {}}))["status"] == "processed"
    assert run(leash.handle_webhook({"id": "WH-1", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {}}))["status"] == "duplicate"
