"""Unit tests: crypto, compiler, catalog identity, receipts and the ledger."""

from __future__ import annotations

from decimal import Decimal

import pytest

from leash.catalog import Directory, normalise_name
from leash.compiler import SAMPLES, compile_mandate, parse_rules, render
from leash.crypto import KeyPair, SignatureError, b64u, canonical, jws_sign, jws_verify
from leash.receipts import text_pdf
from leash.store import Store

from .conftest import ScriptedLLM, run


@pytest.fixture
def directory() -> Directory:
    return Directory.load()


# ------------------------------------------------------------------ crypto


def test_jws_roundtrip_and_tamper():
    k = KeyPair.generate("k1")
    tok = jws_sign({"a": 1, "amount": "10.00"}, k, "t+jws")
    header, payload = jws_verify(tok, k.public_jwk(), "t+jws")
    assert header["alg"] == "EdDSA" and payload == {"a": 1, "amount": "10.00"}
    h, p, s = tok.split(".")
    forged = h + "." + b64u(canonical({"a": 1, "amount": "99.00"})) + "." + s
    with pytest.raises(SignatureError):
        jws_verify(forged, k.public_jwk())
    with pytest.raises(SignatureError):
        jws_verify(tok, KeyPair.generate("k2").public_jwk())
    with pytest.raises(SignatureError):
        jws_verify(tok, k.public_jwk(), "other+jws")


def test_alg_none_is_rejected():
    k = KeyPair.generate("k1")
    tok = jws_sign({"a": 1}, k, "t")
    none_header = b64u(canonical({"alg": "none", "typ": "t", "kid": "k1"}))
    with pytest.raises(SignatureError):
        jws_verify(none_header + "." + tok.split(".")[1] + ".", k.public_jwk())


# ------------------------------------------------------------------ compiler


def test_rules_parser_reads_coffee_sample(directory):
    r = parse_rules(SAMPLES["coffee"], directory)
    assert r["budget"] == {"amount": "80", "period": "month"}
    assert r["per_purchase_max"] == "30" and r["ask_above"] == "25"
    assert r["merchants"] == ["m_beanbloom", "m_dailygrind", "m_northside"]
    assert r["allow_subscriptions"] is False and r["max_purchases"] == {"count": 4, "period": "month"}
    assert r["categories_allow"] == ["coffee"]


def test_rules_parser_other_samples(directory):
    g = parse_rules(SAMPLES["gift"], directory)
    assert g["ask_above"] == "0" and g["budget"] == {"amount": "60", "period": "total"} and g["merchants"] == ["m_kiteandkin"]
    o = parse_rules(SAMPLES["office"], directory)
    assert o["merchants"] == [] and "electronics" in o["categories_block"] and o["time_window"]["days"] == [0, 1, 2, 3, 4]
    assert "tea" not in o["categories_allow"]  # "team" is not "tea"


def test_model_numbers_must_be_grounded(settings, directory):
    llm = ScriptedLLM(settings, {"compile": {
        "title": "Coffee", "per_purchase_max": 300, "ask_above": 25, "budget": {"amount": 8000, "period": "month"},
        "merchants": ["m_beanbloom", "m_goldreserve_fake"], "categories_allow": ["coffee"], "allow_subscriptions": True,
    }})
    policy, report = run(compile_mandate(SAMPLES["coffee"], directory, llm))
    assert policy.per_purchase_max == Decimal("30") and policy.budget.amount == Decimal("80")
    assert policy.allow_subscriptions is False  # "Never subscriptions" in the text wins
    assert any("not in your text" in w for w in report["warnings"])
    assert any("m_goldreserve_fake" in w for w in report["warnings"])


def test_plain_language_is_deterministic(settings, directory):
    from leash.llm import LLM

    p1, _ = run(compile_mandate(SAMPLES["coffee"], directory, LLM(settings)))
    lines = render(p1, directory)
    assert lines == render(p1, directory)
    assert "Never spends more than $30.00 on a single purchase." in lines
    assert any("Never signs you up for subscriptions" in l for l in lines)
    assert any("$80.00 PayPal allowance" in l for l in lines)


def test_unknown_merchant_is_reported(settings, directory):
    from leash.llm import LLM

    policy, report = run(compile_mandate("Buy coffee only from Bean & Bloom or Acme Beans. Max $20 per order.", directory, LLM(settings)))
    assert policy.merchants_allow == ["m_beanbloom"]
    assert "Acme Beans" in report["unknown_merchants"]


# ------------------------------------------------------------------ merchant identity


def test_lookalike_names_normalise_together(directory):
    assert normalise_name("Bean & Bl00m Coffee") == normalise_name("Bean & Bloom Coffee") == normalise_name("BEAN & B1OOM COFFEE")
    spoof = directory.merchant("m_beanbl00m")
    assert [m.id for m in directory.lookalikes(spoof)] == ["m_beanbloom"]
    assert directory.match_merchant("beanandbloom.example").id == "m_beanbloom"
    assert directory.match_merchant("Bean & Bloom").id == "m_beanbloom"  # never resolves to the unverified twin


# ------------------------------------------------------------------ evidence pdf and ledger


def test_pdf_is_valid_shape():
    pdf = text_pdf([f"line {i} (with parens) \\ and unicode é" for i in range(130)], "t")
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF") and b"/Count 3" in pdf


def test_ledger_chain_detects_edits(tmp_path):
    s = Store(tmp_path / "l.db")
    for i in range(5):
        s.append("mdt_x", "policy", "k", f"entry {i}", {"n": i, "amount": Decimal("1.10")})
    assert s.verify_chain()["ok"]
    s._db.execute("UPDATE ledger SET title='edited' WHERE seq=3")
    assert s.verify_chain() == {"ok": False, "entries": 5, "broken_at": 3}
    s.close()


def test_nonce_is_single_use(tmp_path):
    s = Store(tmp_path / "n.db")
    assert s.use_nonce("abc", "x") and not s.use_nonce("abc", "y")
    s.close()
