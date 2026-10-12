"""Unit tests: money, mandate parsing and signing, dispatch parsing, optimizer, ledger."""

from __future__ import annotations

from decimal import Decimal

import pytest

from sauda.catalog import Catalog
from sauda.dispatch import parse_rules as parse_dispatch_rules
from sauda.mandate import SAMPLE_BRIEF, apply_edits, parse_rules, sign, verify
from sauda.models import DealLine, Thread
from sauda.money import D, allocate, fmt, pct
from sauda.optimizer import choose
from sauda.store import Store

from .conftest import ScriptedLLM, run


@pytest.fixture
def catalog() -> Catalog:
    return Catalog.load()


# ------------------------------------------------------------------ money


def test_allocate_sums_exactly():
    parts = allocate(D("336.51"), [D("304.50"), D("269.40"), D("547.80")])
    assert sum(parts) == D("336.51")
    assert parts == [D("91.35"), D("80.82"), D("164.34")]
    assert sum(allocate(D("0.05"), [1, 1, 1])) == D("0.05")


def test_money_parsing():
    assert D("1,150") == Decimal("1150.00")
    assert D(0.1 + 0.2) == Decimal("0.30")
    assert fmt("5") == "5.00"
    assert pct("30%") == Decimal("0.3000") and pct(30) == Decimal("0.3000") and pct("0.3") == Decimal("0.3000")
    with pytest.raises(ValueError):
        D("abc")
    with pytest.raises(ValueError):
        D("NaN")


# ------------------------------------------------------------------ mandate


def test_rules_parser_reads_sample_brief(catalog):
    r = parse_rules(SAMPLE_BRIEF, catalog)
    assert r["buyer_name"] == "Anita Shah" and r["buyer_business"] == "Green Basket Grocers"
    assert [(i["product"], i["qty_kg"]) for i in r["items"]] == [
        ("pearl_millet_flour", 150), ("turmeric_powder", 60), ("green_cardamom", 20)]
    assert r["items"][2]["max_unit_price"] == "30"
    assert r["budget_total"] == "1150" and r["max_sellers"] == 3 and r["required_certs"] == ["organic"]
    assert r["max_lead_time_days"] == 10 and r["advance_pct"] == "0.3000" and r["auto_release_limit"] == "150"


def test_model_numbers_must_be_grounded_in_brief(settings, catalog):
    from sauda.mandate import draft_mandate

    # A model that invents a bigger budget and quantity: both are rejected in favour of the brief's numbers.
    llm = ScriptedLLM(settings, {"mandate": {
        "items": [{"product": "pearl_millet_flour", "qty_kg": 999}, {"product": "turmeric_powder", "qty_kg": 60}],
        "budget_total": 9999, "max_sellers": 3, "advance_pct": 0.3, "required_certs": ["organic"],
    }})
    m, ai, warnings = run(draft_mandate(SAMPLE_BRIEF, catalog, llm))
    assert m.budget_total == D("1150")
    assert {i.product: i.qty_kg for i in m.items}["pearl_millet_flour"] == 150
    assert any("budget_total" in w for w in warnings)


def test_signature_detects_tampering(settings, catalog):
    m = run(_draft(settings, catalog))
    signed = sign(m, settings.secret, "Anita")
    assert verify(signed, settings.secret)
    tampered = signed.model_copy(update={"budget_total": D("99999")})
    assert not verify(tampered, settings.secret)
    assert not verify(signed, "another-secret")
    edited = apply_edits(signed, {"budget_total": "1200"}, catalog)
    assert edited.signature is None and not verify(edited, settings.secret)


def test_apply_edits_rejects_bad_values(settings, catalog):
    m = run(_draft(settings, catalog))
    with pytest.raises(ValueError):
        apply_edits(m, {"items": [{"product": "gold_bars", "qty_kg": 1}]}, catalog)
    with pytest.raises(ValueError):
        apply_edits(m, {"advance_pct": "0.05"}, catalog)
    with pytest.raises(ValueError):
        apply_edits(m, {"budget_total": "0"}, catalog)


async def _draft(settings, catalog):
    from sauda.llm import LLM
    from sauda.mandate import draft_mandate

    m, _, _ = await draft_mandate(SAMPLE_BRIEF, catalog, LLM(settings))
    return m


# ------------------------------------------------------------------ dispatch parsing (offline rules)


def _line(qty=150, product="pearl_millet_flour", lid="L1"):
    return DealLine(line_id=lid, seller_id="MMF", seller_name="x", product=product, product_name="p", qty_kg=qty,
                    unit_price=D("2"), list_price=D("2.4"), value=D(qty * 2))


@pytest.mark.parametrize("text,shipped,tracking", [
    ("पूरा 150 किलो बाजरे का आटा भेज दिया है। India Post ट्रैकिंग EE123456789IN", 150, "EE123456789IN"),
    ("120 kg bheja, 30 kg kam hai. tracking EE123456789IN", 120, "EE123456789IN"),
    ("30 किलो कम है, बाकी भेज दिया। EE123456789IN", 120, "EE123456789IN"),
    ("50 கிலோ மஞ்சள் தூள் அனுப்பினோம், 10 கிலோ குறைவு. DHL 4821937465", 50, "4821937465"),
    ("130 കിലോ അയച്ചു. ട്രാക്കിംഗ് EE987654321IN", 130, "EE987654321IN"),
    ("140 किलो पाठवले, 10 किलो कमी आहे. ट्रॅकिंग EE111222333IN", 140, "EE111222333IN"),
    ("Shipped 500 kg!! tracking EE123456789IN", 150, "EE123456789IN"),  # never more than ordered
])
def test_dispatch_rules_parser(catalog, text, shipped, tracking):
    r = parse_dispatch_rules(text, [_line()], catalog)
    assert r["lines"][0]["shipped_kg"] == shipped
    assert r["tracking_number"] == tracking


# ------------------------------------------------------------------ optimizer


def _thread(sid, product, qty, price, list_price="3"):
    return Thread(seller_id=sid, seller_name=sid, product=product, qty_kg=qty, list_price=D(list_price), status="agreed", agreed_price=D(price))


def test_optimizer_respects_seller_cap_and_budget(settings, catalog):
    m = run(_draft(settings, catalog))
    threads = [
        _thread("MMF", "pearl_millet_flour", 150, "2.03"),
        _thread("SHK", "pearl_millet_flour", 120, "1.90"),  # cheaper but cannot cover 150 kg alone
        _thread("ETG", "turmeric_powder", 60, "4.49"),
        _thread("IDK", "green_cardamom", 20, "27.39"),
    ]
    plan, alternatives = choose(m, threads, catalog)
    assert plan.complete and len(plan.sellers) <= 3 and plan.cost <= m.budget_total
    assert "SHK" not in plan.sellers  # the SHK + MMF split would need 4 sellers
    assert alternatives and "4 sellers" in alternatives[0]["rejected_because"]


# ------------------------------------------------------------------ ledger


def test_ledger_chain_detects_edits(tmp_path):
    s = Store(tmp_path / "l.db")
    for i in range(5):
        s.append("SD-1", "agent:buyer", "k", f"entry {i}", {"n": i, "amount": D("1.10")})
    assert s.verify_chain()["ok"]
    s._db.execute("UPDATE ledger SET title='edited' WHERE seq=3")
    res = s.verify_chain()
    assert not res["ok"] and res["broken_at"] == 3
    s.close()
