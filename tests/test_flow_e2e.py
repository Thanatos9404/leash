"""End to end in fixture mode: brief -> mandate -> negotiation -> PayPal order -> dispatch -> payouts/refund
-> balance invoice -> balance payouts -> settled, with money conservation checked at the end."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from sauda.mandate import SAMPLE_BRIEF
from sauda.money import D

from .conftest import run


async def negotiate_to_proposal(engine):
    deal = await engine.create_deal(SAMPLE_BRIEF)
    deal = await engine.sign_mandate(deal.id, None, "Anita")
    await engine.start_negotiation(deal.id)
    await asyncio.gather(*list(engine.tasks))
    return engine.get(deal.id)


async def pay_advance(engine, deal):
    a = deal.actions[0]
    deal = await engine.approve(deal.id, a.id, "Anita")
    res = engine.paypal.approve_order(deal.paypal.order_id)
    return await engine.on_buyer_return(deal.paypal.order_id, res["payer_id"])


def report(deal, seller_id, shipped, tracking):
    lines = deal.lines_for(seller_id)
    return {"lines": [{"line_id": l.line_id, "shipped_kg": shipped.get(l.product, l.qty_kg)} for l in lines],
            "tracking_number": tracking, "carrier": "INDIA_POST"}


async def full_flow(engine):
    deal = await negotiate_to_proposal(engine)
    deal = await pay_advance(engine, deal)
    assert deal.status == "in_fulfilment"
    sellers = sorted({l.seller_id for l in deal.lines})
    for sid in sellers:
        shipped = {"turmeric_powder": 50} if sid == "ETG" else {}
        deal = await engine.confirm_dispatch(deal.id, sid, report(deal, sid, shipped, f"EE{abs(hash(sid)) % 10**9:09d}IN"), "test")
    # Approve anything above the auto-release limit
    for a in list(deal.actions):
        if a.status == "needs_approval":
            deal = await engine.approve(deal.id, a.id, "Anita")
    return deal


def test_negotiation_produces_policy_checked_proposal(engine):
    deal = run(negotiate_to_proposal(engine))
    assert deal.status == "proposed", deal.error
    m = deal.mandate
    sellers = {l.seller_id for l in deal.lines}
    assert len(sellers) <= m.max_sellers
    assert D(deal.totals["subtotal"]) <= m.budget_total
    # Excluded sellers carry a reason (organic certification, advance terms)
    excluded = {t.seller_id: t.exclusion for t in deal.threads if t.status == "excluded"}
    assert "SNG" in excluded and "organic" in excluded["SNG"]
    assert "BOD" in excluded and "advance" in excluded["BOD"]
    a = deal.actions[0]
    assert a.kind == "CREATE_ORDER" and a.status == "needs_approval" and a.requires_human
    assert all(c.ok for c in a.policy), [c for c in a.policy if not c.ok]
    body = a.payload["request"]
    pu = body["purchase_units"][0]
    assert pu["amount"]["value"] == f"{deal.advance_total:.2f}"
    assert sum(Decimal(i["unit_amount"]["value"]) for i in pu["items"]) == deal.advance_total


def test_full_flow_settles_and_conserves_money(engine):
    deal = run(full_flow(engine))
    assert deal.status == "invoiced", (deal.status, [(a.kind, a.status, a.error) for a in deal.actions])
    inv = deal.paypal.invoice_id
    assert inv and deal.paypal.invoice_url
    # Short shipment produced a refund of exactly the unshipped share of the advance
    etg = next(l for l in deal.lines if l.seller_id == "ETG")
    assert etg.status == "short" and etg.shipped_kg == 50
    assert D(sum(Decimal(r["amount"]) for r in deal.paypal.refunds)) == D(etg.advance - etg.released_advance)
    # Buyer pays the invoice; sync picks it up and proposes the balance release, which needs a human
    engine.paypal.pay_invoice(inv)
    deal = run(engine.sync(deal.id))
    bal = [a for a in deal.actions if a.kind == "PAYOUT" and a.payload.get("stage") == "balance"]
    assert len(bal) == 1 and bal[0].status == "needs_approval"
    deal = run(engine.approve(deal.id, bal[0].id, "Anita"))
    for _ in range(3):
        deal = run(engine.sync(deal.id))
    assert deal.status == "settled", deal.status
    s = engine.summary(deal)
    assert D(s["held"]) == Decimal("0.00"), s
    assert D(s["buyer_net_paid"]) == D(s["shipped_value"]), s
    assert D(s["paid_to_fpos"]) + D(s["platform_fees"]) == D(s["buyer_net_paid"])
    # The ledger chain is intact
    assert engine.store.verify_chain()["ok"]


def test_large_dispatch_payout_needs_human(engine):
    deal = run(negotiate_to_proposal(engine))
    deal = run(pay_advance(engine, deal))
    big = max(deal.lines, key=lambda l: l.advance)
    deal = run(engine.confirm_dispatch(deal.id, big.seller_id, report(deal, big.seller_id, {}, "EE123456789IN"), ""))
    payout = [a for a in deal.actions if a.kind == "PAYOUT"][-1]
    if payout.amount > deal.mandate.auto_release_limit:
        assert payout.status == "needs_approval"
    else:
        assert payout.status == "executed" and payout.approved_by == "mandate"
