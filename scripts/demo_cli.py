"""Run the whole Sauda flow headless and print what happens.

    python scripts/demo_cli.py            # fixture PayPal, offline rules (no keys needed)
    python scripts/demo_cli.py --llm      # use Nebius Token Factory if NEBIUS_API_KEY is configured
    python scripts/demo_cli.py --sandbox  # real PayPal sandbox for every call except the two human steps

In --sandbox mode the script stops at the buyer approval link (a person must log in to a sandbox buyer account)
and again at the invoice link. Re-run with --resume DEAL_ID after each step.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DISPATCH = {
    "MMF": "पूरा 150 किलो बाजरे का आटा भेज दिया है। India Post ट्रैकिंग EE123456789IN",
    "SHK": "Pura maal bhej diya, tracking EE555666777IN",
    "ETG": "50 கிலோ மஞ்சள் தூள் அனுப்பினோம், 10 கிலோ குறைவு. DHL 4821937465",
    "IDK": "20 കിലോ ഏലക്കായ അയച്ചു. ട്രാക്കിംഗ് EE987654321IN (India Post)",
    "KJF": "Sagla maal pathavla, tracking EE111222333IN",
    "SNG": "Sagla maal pathavla, tracking EE444555666IN",
    "BOD": "எல்லாம் அனுப்பினோம், டிராக்கிங் EE777888999IN",
}


def money(x) -> str:
    return f"${x}"


async def main(args: argparse.Namespace) -> None:
    if not args.sandbox:
        os.environ["SAUDA_PAYPAL_MODE"] = "fixture"
    if not args.llm:
        os.environ["SAUDA_OFFLINE"] = "1"
    if not args.keep:
        os.environ.setdefault("SAUDA_DATA_DIR", tempfile.mkdtemp(prefix="sauda-demo-"))
    from sauda.app import build_engine
    from sauda.mandate import SAMPLE_BRIEF

    eng = build_engine()
    print(f"PayPal mode: {eng.paypal.mode}   AI: {eng.llm.label()}   data: {eng.settings.data_dir}\n")

    if args.resume:
        deal = eng.get(args.resume)
    else:
        deal = await eng.create_deal(SAMPLE_BRIEF)
        m = deal.mandate
        print("1. MANDATE drafted by", deal.mandate_ai)
        for it in m.items:
            cap = f" (max ${it.max_unit_price}/kg)" if it.max_unit_price else ""
            print(f"   - {it.qty_kg} kg {it.product}{cap}")
        print(f"   budget {money(m.budget_total)}, max {m.max_sellers} sellers, certs {m.required_certs}, "
              f"advance {int(m.advance_pct * 100)}%, auto-release <= {money(m.auto_release_limit)}")
        for w in deal.mandate_warnings:
            print("   !", w)
        deal = await eng.sign_mandate(deal.id, None, "Anita Shah")
        print("   signed:", deal.mandate.signature[:16], "...\n")

        await eng.start_negotiation(deal.id)
        await asyncio.gather(*list(eng.tasks))
        deal = eng.get(deal.id)
        print("2. NEGOTIATION")
        for t in deal.threads:
            if t.status == "excluded":
                print(f"   x {t.seller_name} / {t.product}: {t.exclusion}")
                continue
            path = " -> ".join(f"{o.side[0]}:{o.action}{'@' + str(o.unit_price) if o.unit_price else ''}" for o in t.offers)
            print(f"   {'+' if t.status == 'agreed' else '-'} {t.seller_name} / {t.product}: {path}")
            for o in t.offers:
                if o.guardrail:
                    print(f"       guardrail: {o.guardrail}")
        print()
        if deal.status != "proposed":
            print("No proposal:", deal.error)
            return
        print("3. PROPOSAL")
        for l in deal.lines:
            print(f"   {l.line_id} {l.qty_kg} kg {l.product_name} from {l.seller_name} @ ${l.unit_price}/kg = ${l.value} (advance ${l.advance})")
        t = deal.totals
        print(f"   subtotal ${t['subtotal']} / budget ${t['budget']}; saved ${t['savings_vs_quotes']} vs quotes; "
              f"{t['farmer_members']} member farmers")
        print("   rationale:", deal.rationale)
        a = deal.actions[0]
        for c in a.policy:
            print(f"   [{'ok' if c.ok else 'FAIL'}] {c.name}: {c.detail}")
        deal = await eng.approve(deal.id, a.id, "Anita Shah")
        print(f"\n4. PAYPAL ORDER {deal.paypal.order_id} status {deal.paypal.order_status}")
        print("   approve at:", deal.paypal.approve_url)
        if eng.paypal.mode == "sandbox":
            print("\nOpen that link, log in with your sandbox PERSONAL account, approve, then run:")
            print(f"   python scripts/demo_cli.py --sandbox --keep --resume {deal.id}")
            return

    if deal.status == "awaiting_buyer":
        if eng.paypal.mode == "fixture":
            res = eng.paypal.approve_order(deal.paypal.order_id)
            deal = await eng.on_buyer_return(deal.paypal.order_id, res["payer_id"])
        else:
            deal = await eng.sync(deal.id)
        print(f"   captured ${deal.paypal.captured} (capture {deal.paypal.capture_id}, PayPal fee {deal.paypal.paypal_fee})\n")

    if deal.status == "in_fulfilment":
        print("5. DISPATCH (FPO managers write in their own language)")
        for sid in sorted({l.seller_id for l in deal.lines if l.status == "pending"}):
            text = DISPATCH.get(sid, "All shipped, tracking EE000111222IN")
            prev = await eng.preview_dispatch(deal.id, sid, text)
            print(f"   {sid}: {text}")
            print(f"      parsed by {prev['ai']}: {[(x['line_id'], x['shipped_kg']) for x in prev['lines']]} tracking {prev['tracking_number']} {prev.get('note_en') or ''}")
            deal = await eng.confirm_dispatch(deal.id, sid, prev, text)
        for a in deal.actions:
            if a.kind in ("PAYOUT", "REFUND", "ADD_TRACKING", "ISSUE_INVOICE"):
                print(f"   {a.id} {a.kind:12} {money(a.amount):>9} {a.status:15} {a.approval_rule[:90]}")
        for a in list(deal.actions):
            if a.status == "needs_approval":
                print(f"   -> buyer approves {a.id} ({a.summary})")
                deal = await eng.approve(deal.id, a.id, "Anita Shah")
        print()

    if deal.status == "invoiced":
        print(f"6. BALANCE INVOICE {deal.paypal.invoice_id} ${deal.paypal.invoice_amount}: {deal.paypal.invoice_url}")
        if eng.paypal.mode == "fixture":
            eng.paypal.pay_invoice(deal.paypal.invoice_id)
        else:
            print("\nPay it as the sandbox buyer, then run:")
            print(f"   python scripts/demo_cli.py --sandbox --keep --resume {deal.id}")
            return
        deal = await eng.sync(deal.id)

    if deal.status == "settling":
        for a in list(deal.actions):
            if a.status == "needs_approval":
                print(f"   -> buyer confirms receipt: {a.summary}")
                deal = await eng.approve(deal.id, a.id, "Anita Shah")
        for _ in range(4):
            deal = await eng.sync(deal.id)
            if deal.status == "settled":
                break
    print(f"\n7. STATUS {deal.status}: {eng.summary(deal)}")
    print("   ledger:", eng.store.verify_chain())
    print("   LLM usage:", eng.llm.usage.to_dict())
    await eng.paypal.aclose()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--llm", action="store_true", help="use Nebius Token Factory (needs NEBIUS_API_KEY)")
    p.add_argument("--sandbox", action="store_true", help="use the real PayPal sandbox (needs PAYPAL_CLIENT_ID/SECRET)")
    p.add_argument("--keep", action="store_true", help="use the normal data dir instead of a temp one")
    p.add_argument("--resume", help="continue an existing deal id")
    asyncio.run(main(p.parse_args()))
