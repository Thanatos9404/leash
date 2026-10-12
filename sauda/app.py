"""HTTP API and web UI."""

from __future__ import annotations

import html
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .catalog import Catalog
from .config import ROOT, Settings
from .engine import DealError, Engine
from .llm import LLM
from .mandate import SAMPLE_BRIEF
from .models import Deal
from .paypal import FixturePayPal, PayPalAPI, PayPalError, build_paypal
from .store import Store

WEB = ROOT / "web"


def build_engine(settings: Optional[Settings] = None, paypal: Optional[PayPalAPI] = None, llm: Optional[LLM] = None) -> Engine:
    settings = settings or Settings()
    store = Store(settings.db_path)
    catalog = Catalog.load(seller_emails=settings.seller_emails)
    if paypal is None:
        paypal = build_paypal(settings, fixture_state=store.kv_get("fixture_paypal"), persist=lambda s: store.kv_set("fixture_paypal", s))
    return Engine(settings, store, paypal, llm or LLM(settings), catalog)


def deal_view(engine: Engine, deal: Deal) -> dict[str, Any]:
    data = deal.model_dump(mode="json")
    live = engine.live_threads.get(deal.id)
    if live and deal.status == "negotiating":
        data["threads"] = [t.model_dump(mode="json") for t in live.values()]
    data["summary"] = engine.summary(deal)
    data["pending"] = [a.id for a in deal.actions if a.status == "needs_approval"]
    data["mandate"]["signature_short"] = (deal.mandate.signature or "")[:16]
    data["sellers"] = {sid: engine.catalog.seller(sid).public() for sid in {t.seller_id for t in deal.threads} | {l.seller_id for l in deal.lines}}
    return data


def create_app(engine: Optional[Engine] = None) -> FastAPI:
    holder: dict[str, Engine] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        holder["engine"] = engine or build_engine()
        yield
        await holder["engine"].paypal.aclose()

    app = FastAPI(title="Sauda", version="1.0.0", lifespan=lifespan)
    if engine is not None:
        holder["engine"] = engine

    def E() -> Engine:
        return holder["engine"]

    @app.exception_handler(DealError)
    async def _deal_error(_: Request, exc: DealError):
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(PayPalError)
    async def _pp_error(_: Request, exc: PayPalError):
        return JSONResponse({"error": str(exc), "details": exc.details}, status_code=502)

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError):
        return JSONResponse({"error": str(exc)}, status_code=422)

    # ------------------------------------------------------------------ UI

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(WEB / "index.html")

    if WEB.exists():
        app.mount("/static", StaticFiles(directory=WEB), name="static")

    # ------------------------------------------------------------------ status & catalog

    @app.get("/api/status")
    async def status():
        e = E()
        s = e.settings
        return {
            "paypal_mode": e.paypal.mode,
            "paypal_env": s.paypal_env,
            "ai": e.llm.label(),
            "ai_fast": e.llm.label(fast=True),
            "llm_usage": e.llm.usage.to_dict(),
            "payments_frozen": s.payments_frozen,
            "platform_fee_pct": s.platform_fee_pct,
            "auto_release_cap": s.auto_release_daily_cap,
            "sample_brief": SAMPLE_BRIEF,
            "buyer_email_set": bool(s.buyer_email),
            "webhook_verification": bool(s.paypal_webhook_id) if e.paypal.mode == "sandbox" else "fixture",
        }

    @app.get("/api/catalog")
    async def catalog():
        e = E()
        return {
            "products": [p.model_dump() for p in e.catalog.products.values()],
            "sellers": [s.public() for s in e.catalog.sellers.values()],
        }

    # ------------------------------------------------------------------ deals

    @app.get("/api/deals")
    async def deals():
        e = E()
        return [
            {"id": d.id, "status": d.status, "created_at": d.created_at, "buyer": d.mandate.buyer_business or d.mandate.buyer_name,
             "subtotal": d.totals.get("subtotal"), "lines": len(d.lines)}
            for d in e.store.list_deals()
        ]

    @app.post("/api/deals")
    async def create_deal(payload: dict = Body(...)):
        e = E()
        deal = await e.create_deal(str(payload.get("brief", "")))
        return deal_view(e, deal)

    @app.get("/api/deals/{deal_id}")
    async def get_deal(deal_id: str):
        e = E()
        return deal_view(e, e.get(deal_id))

    @app.post("/api/deals/{deal_id}/mandate")
    async def sign_mandate(deal_id: str, payload: dict = Body(default={})):
        e = E()
        deal = await e.sign_mandate(deal_id, payload.get("edits"), str(payload.get("signer") or "buyer")[:60])
        return deal_view(e, deal)

    @app.post("/api/deals/{deal_id}/negotiate")
    async def negotiate(deal_id: str):
        e = E()
        deal = await e.start_negotiation(deal_id)
        return deal_view(e, deal)

    @app.post("/api/deals/{deal_id}/actions/{action_id}/approve")
    async def approve(deal_id: str, action_id: str, payload: dict = Body(default={})):
        e = E()
        deal = await e.approve(deal_id, action_id, str(payload.get("approver") or "buyer")[:60])
        return deal_view(e, deal)

    @app.post("/api/deals/{deal_id}/actions/{action_id}/reject")
    async def reject(deal_id: str, action_id: str, payload: dict = Body(default={})):
        e = E()
        deal = await e.reject(deal_id, action_id, str(payload.get("approver") or "buyer")[:60], str(payload.get("reason") or "")[:300])
        return deal_view(e, deal)

    @app.post("/api/deals/{deal_id}/actions/{action_id}/retry")
    async def retry(deal_id: str, action_id: str, payload: dict = Body(default={})):
        e = E()
        deal = await e.retry(deal_id, action_id, str(payload.get("approver") or "operator")[:60])
        return deal_view(e, deal)

    @app.post("/api/deals/{deal_id}/dispatch/preview")
    async def dispatch_preview(deal_id: str, payload: dict = Body(...)):
        e = E()
        return await e.preview_dispatch(deal_id, str(payload["seller_id"]), str(payload.get("text", "")))

    @app.post("/api/deals/{deal_id}/dispatch")
    async def dispatch(deal_id: str, payload: dict = Body(...)):
        e = E()
        deal = await e.confirm_dispatch(deal_id, str(payload["seller_id"]), payload.get("report") or {}, str(payload.get("text", "")))
        return deal_view(e, deal)

    @app.post("/api/deals/{deal_id}/sync")
    async def sync(deal_id: str):
        e = E()
        deal = await e.sync(deal_id)
        return deal_view(e, deal)

    # ------------------------------------------------------------------ ledger & PayPal call log

    @app.get("/api/ledger")
    async def ledger(deal_id: Optional[str] = None, after: int = 0):
        return E().store.entries(deal_id=deal_id, after=after)

    @app.get("/api/ledger/verify")
    async def ledger_verify():
        return E().store.verify_chain()

    @app.get("/api/paypal/calls")
    async def paypal_calls():
        return [c.to_dict() for c in E().paypal.calls][-200:]

    # ------------------------------------------------------------------ PayPal redirects & webhooks

    @app.get("/paypal/return", include_in_schema=False)
    async def paypal_return(token: str, deal: str = "", PayerID: str = ""):
        e = E()
        try:
            d = await e.on_buyer_return(token, PayerID)
            return RedirectResponse(f"/?deal={d.id}&paid=1", status_code=303)
        except (DealError, PayPalError) as exc:
            return RedirectResponse(f"/?deal={deal}&error={html.escape(str(exc))[:200]}", status_code=303)

    @app.get("/paypal/cancel", include_in_schema=False)
    async def paypal_cancel(deal: str, token: str = ""):
        e = E()
        await e.on_buyer_cancel(deal)
        return RedirectResponse(f"/?deal={deal}&cancelled=1", status_code=303)

    @app.post("/webhooks/paypal")
    async def webhook(request: Request):
        e = E()
        event = await request.json()
        if not await e.paypal.verify_webhook(dict(request.headers), event):
            e.store.append(None, "paypal:webhook", "webhook.rejected", "Webhook failed signature verification", {"event_type": event.get("event_type")})
            raise HTTPException(status_code=401, detail="webhook signature verification failed")
        return await e.handle_webhook(event)

    # ------------------------------------------------------------------ fixture-only simulated PayPal pages

    def fixture() -> FixturePayPal:
        p = E().paypal
        if not isinstance(p, FixturePayPal):
            raise HTTPException(status_code=404, detail="fixture pages exist only in fixture mode")
        return p

    @app.get("/fixture/paypal/checkoutnow", include_in_schema=False)
    async def fixture_checkout(token: str):
        p = fixture()
        order = p.state["orders"].get(token)
        if not order:
            raise HTTPException(404, "unknown fixture order")
        pu = order["purchase_units"][0]
        rows = "".join(
            f"<tr><td>{html.escape(i['name'])}</td><td class=r>${html.escape(i['unit_amount']['value'])}</td></tr>" for i in pu.get("items", [])
        )
        return HTMLResponse(_fixture_page(
            "Simulated PayPal approval",
            f"""<p class=muted>Order <code>{html.escape(token)}</code> &middot; {html.escape(pu.get('description', ''))}</p>
            <table>{rows}<tr class=t><td>Total</td><td class=r>${html.escape(pu['amount']['value'])} USD</td></tr></table>
            <form method=post action="/fixture/paypal/checkoutnow/approve?token={html.escape(token)}">
              <button class=pay type=submit>Approve and pay (simulated)</button></form>
            <form method=post action="/fixture/paypal/checkoutnow/cancel?token={html.escape(token)}">
              <button class=ghost type=submit>Cancel and return</button></form>""",
        ))

    @app.post("/fixture/paypal/checkoutnow/approve", include_in_schema=False)
    async def fixture_approve(token: str):
        p = fixture()
        res = p.approve_order(token)
        sep = "&" if "?" in res["return_url"] else "?"
        target = res["return_url"].replace(E().settings.public_base_url, "") or "/"
        return RedirectResponse(f"{target}{sep}token={token}&PayerID={res['payer_id']}", status_code=303)

    @app.post("/fixture/paypal/checkoutnow/cancel", include_in_schema=False)
    async def fixture_cancel(token: str):
        p = fixture()
        url = p.cancel_url(token).replace(E().settings.public_base_url, "") or "/"
        sep = "&" if "?" in url else "?"
        return RedirectResponse(f"{url}{sep}token={token}", status_code=303)

    @app.get("/fixture/paypal/invoice/{invoice_id}", include_in_schema=False)
    async def fixture_invoice(invoice_id: str):
        p = fixture()
        inv = p.state["invoices"].get(invoice_id)
        if not inv:
            raise HTTPException(404, "unknown fixture invoice")
        rows = "".join(
            f"<tr><td>{html.escape(i['name'])}<div class=muted>{html.escape(i.get('description', ''))}</div></td><td class=r>${html.escape(i['unit_amount']['value'])}</td></tr>"
            for i in inv["items"]
        )
        paid = inv["status"] == "PAID"
        action = (
            "<p class=ok>Paid. You can close this tab.</p>" if paid else
            f"<form method=post action='/fixture/paypal/invoice/{html.escape(invoice_id)}/pay'><button class=pay type=submit>Pay invoice (simulated)</button></form>"
        )
        return HTMLResponse(_fixture_page(
            "Simulated PayPal invoice",
            f"""<p class=muted>Invoice <code>{html.escape(inv['detail'].get('invoice_number', ''))}</code> from {html.escape(inv['invoicer'].get('business_name', ''))}
            &middot; status <b>{html.escape(inv['status'])}</b></p><table>{rows}
            <tr class=t><td>Amount due</td><td class=r>${html.escape(inv['due_amount']['value'])} USD</td></tr></table>{action}""",
        ))

    @app.post("/fixture/paypal/invoice/{invoice_id}/pay", include_in_schema=False)
    async def fixture_pay(invoice_id: str):
        p = fixture()
        inv = p.pay_invoice(invoice_id)
        e = E()
        # Deliver the same event a real sandbox webhook would, through the normal verified webhook path.
        event = {"id": f"WH-FX-{invoice_id}", "event_type": "INVOICING.INVOICE.PAID", "resource": {"invoice": {"id": invoice_id, "status": inv["status"]}}}
        if await p.verify_webhook({"paypal-transmission-sig": "fixture-signature"}, event):
            await e.handle_webhook(event)
        d = e.store.find_deal_by(lambda x: x.paypal.invoice_id == invoice_id)
        return RedirectResponse(f"/?deal={d.id if d else ''}&invoice_paid=1", status_code=303)

    @app.post("/api/demo/reset")
    async def reset():
        e = E()
        if e.paypal.mode != "fixture" and os.getenv("SAUDA_ALLOW_RESET") != "1":
            raise HTTPException(403, "reset is only available in fixture mode")
        e.store.reset()
        if isinstance(e.paypal, FixturePayPal):
            e.paypal.state = {"orders": {}, "captures": {}, "payouts": {}, "invoices": {}, "idem": {}, "batch_ids": []}
        return {"ok": True}

    return app


def _fixture_page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{title}</title><style>
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f4f1ea;color:#1d2a22;margin:0;padding:24px}}
.card{{max-width:520px;margin:6vh auto;background:#fff;border-radius:14px;padding:28px;box-shadow:0 6px 30px rgba(0,0,0,.08)}}
.banner{{background:#fff3cd;border:1px solid #e0c36a;color:#5c4500;padding:10px 12px;border-radius:8px;font-size:14px;margin-bottom:18px}}
h1{{font-size:22px;margin:0 0 6px}} table{{width:100%;border-collapse:collapse;margin:14px 0 20px}} td{{padding:8px 0;border-bottom:1px solid #eee;font-size:14px}}
.r{{text-align:right;white-space:nowrap}} .t td{{font-weight:700;border-bottom:0}} .muted{{color:#6b756e;font-size:13px}} .ok{{color:#1f7a45;font-weight:600}}
button{{width:100%;padding:13px;border-radius:999px;font-size:15px;font-weight:600;cursor:pointer;margin-top:8px}}
.pay{{background:#1d2a22;color:#fff;border:0}} .ghost{{background:#fff;border:1px solid #c9cfca;color:#1d2a22}} code{{font-size:12px}}
</style></head><body><div class=card><div class=banner><b>Fixture mode.</b> This page simulates PayPal so the demo runs without sandbox keys.
No PayPal API was called and no money moved. With sandbox keys configured, buyers approve on sandbox.paypal.com instead.</div>
<h1>{title}</h1>{body}</div></body></html>"""


app = create_app()
