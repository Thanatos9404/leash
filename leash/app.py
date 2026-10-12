"""HTTP API (FastAPI) and static hosting for the React frontend."""

from __future__ import annotations

import html
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import quote

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .agent import SCENARIOS
from .catalog import Catalog, Channel3, Directory
from .compiler import SAMPLES
from .config import ROOT, Settings
from .crypto import KeyPair
from .engine import Leash, LeashError
from .llm import LLM
from .paypal import FixturePayPal, PayPalAPI, PayPalError, build_paypal, empty_state
from .store import Store

DIST = ROOT / "frontend" / "dist"


def build_leash(settings: Optional[Settings] = None, paypal: Optional[PayPalAPI] = None, llm: Optional[LLM] = None) -> Leash:
    settings = settings or Settings()
    store = Store(settings.db_path)
    directory = Directory.load()
    channel3 = Channel3(settings.channel3_api_key, directory) if settings.channel3_api_key else None
    if paypal is None:
        paypal = build_paypal(settings, fixture_state=store.kv_get("fixture_paypal"), persist=lambda s: store.kv_set("fixture_paypal", s))
    keys = settings.data_dir / "keys"
    return Leash(settings, store, paypal, llm or LLM(settings), Catalog(directory, channel3),
                 KeyPair.load_or_create(keys / "receipt.pem", "leash-receipt-1"),
                 KeyPair.load_or_create(keys / "custodial.pem", "cust_demo"))


def create_app(leash: Optional[Leash] = None) -> FastAPI:
    holder: dict[str, Leash] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if "l" not in holder:
            holder["l"] = build_leash()
        yield
        await holder["l"].paypal.aclose()

    app = FastAPI(title="Leash", version="1.0.0", lifespan=lifespan,
                  description="Enforceable spending mandates for AI shopping agents, settled on PayPal (sandbox).")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["*"], allow_headers=["*"])
    if leash is not None:
        holder["l"] = leash

    def L() -> Leash:
        return holder["l"]

    @app.exception_handler(LeashError)
    async def _leash_error(_: Request, exc: LeashError):
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(PayPalError)
    async def _pp_error(_: Request, exc: PayPalError):
        return JSONResponse({"error": f"PayPal said: {exc.message}", "name": exc.name, "issue": exc.issue, "debug_id": exc.debug_id}, status_code=502)

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError):
        return JSONResponse({"error": str(exc)}, status_code=422)

    # ------------------------------------------------------------------ status

    @app.get("/api/status")
    async def status():
        l = L()
        return {
            "paypal_mode": l.paypal.mode, "paypal_env": l.settings.paypal_env, "ai": l.llm.label(), "ai_writer": l.llm.label(role="writer"),
            "llm_usage": l.llm.usage.to_dict(), "catalog": l.catalog.source, "frozen": l.frozen, "owner": l.settings.owner_name,
            "samples": SAMPLES, "scenarios": {k: v["label"] for k, v in SCENARIOS.items()} | {"replay": "Replay a completed cart"},
            "receipt_kid": l.receipt_key.kid, "custodial_kid": l.custodial_key.kid,
            "webhook_verification": bool(l.settings.paypal_webhook_id) if l.paypal.mode == "sandbox" else "fixture",
        }

    @app.get("/api/dashboard")
    async def dashboard():
        return L().dashboard()

    @app.get("/api/merchants")
    async def merchants():
        return [m.model_dump() for m in L().catalog.directory.merchants.values()]

    @app.get("/api/products")
    async def products(q: str = ""):
        return [p.model_dump(mode="json") for p in L().catalog.search(q, limit=20)]

    # ------------------------------------------------------------------ mandates

    @app.post("/api/mandates")
    async def compile_mandate(payload: dict = Body(...)):
        l = L()
        return l.mandate_view(await l.compile(str(payload.get("text", ""))))

    @app.get("/api/mandates/{mid}")
    async def get_mandate(mid: str):
        l = L()
        m = l.mandate(mid)
        return {**l.mandate_view(m), "purchases": [l.purchase_view(p) for p in l.store.purchases(mid)], "timeline": l.store.entries(subject=mid, limit=200)}

    @app.patch("/api/mandates/{mid}")
    async def edit_mandate(mid: str, payload: dict = Body(...)):
        l = L()
        return l.mandate_view(l.update_draft(mid, payload))

    @app.get("/api/mandates/{mid}/signing-payload")
    async def signing_payload(mid: str):
        l = L()
        return {"payload": l.signing_payload(l.mandate(mid)), "typ": "leash-mandate+jws", "alg": "EdDSA"}

    @app.post("/api/mandates/{mid}/sign")
    async def sign(mid: str, payload: dict = Body(default={})):
        l = L()
        return l.mandate_view(await l.sign(mid, payload.get("token")))

    @app.post("/api/mandates/{mid}/fund")
    async def fund(mid: str):
        l = L()
        return l.mandate_view(await l.fund(mid))

    @app.post("/api/mandates/{mid}/refresh")
    async def refresh(mid: str):
        l = L()
        return l.mandate_view(await l.refresh_allowance(mid))

    @app.post("/api/mandates/{mid}/revoke")
    async def revoke(mid: str, payload: dict = Body(default={})):
        l = L()
        return l.mandate_view(await l.revoke(mid, "owner", str(payload.get("reason") or "Revoked by owner")))

    @app.post("/api/mandates/{mid}/agent")
    async def agent(mid: str, payload: dict = Body(...)):
        l = L()
        return l.purchase_view(await l.run_agent(mid, str(payload.get("task", ""))[:300]))

    @app.post("/api/mandates/{mid}/scenario")
    async def scenario(mid: str, payload: dict = Body(...)):
        l = L()
        return l.purchase_view(await l.run_scenario(mid, str(payload.get("name", ""))))

    # ------------------------------------------------------------------ purchases

    @app.get("/api/purchases/{pid}")
    async def get_purchase(pid: str):
        l = L()
        return l.purchase_view(l.purchase(pid))

    @app.post("/api/purchases/{pid}/approve")
    async def approve(pid: str, payload: dict = Body(default={})):
        l = L()
        return l.purchase_view(await l.approve(pid, "owner", payload.get("token")))

    @app.post("/api/purchases/{pid}/approval-payload")
    async def approval_payload(pid: str):
        l = L()
        p = l.purchase(pid)
        import time as _t
        return {"typ": "leash-cart-approval+jws", "payload": {"typ": "leash.cart-approval/1", "purchase_id": p.id, "mandate_id": p.mandate_id,
                                                              "cart_hash": p.cart.cart_hash, "merchant_id": p.cart.merchant_id, "total": str(p.cart.total), "iat": int(_t.time())}}

    @app.post("/api/purchases/{pid}/reject")
    async def reject(pid: str, payload: dict = Body(default={})):
        l = L()
        return l.purchase_view(await l.reject(pid, "owner", str(payload.get("reason") or "")))

    @app.post("/api/purchases/{pid}/exception")
    async def exception(pid: str):
        l = L()
        return l.purchase_view(await l.approve_exception(pid))

    @app.post("/api/purchases/{pid}/refund")
    async def refund(pid: str, payload: dict = Body(default={})):
        l = L()
        return l.purchase_view(await l.refund(pid, payload.get("amount"), str(payload.get("reason") or "Refund requested")))

    @app.post("/api/purchases/{pid}/dispute/simulate")
    async def simulate_dispute(pid: str):
        l = L()
        return l.purchase_view(l.simulate_dispute(pid))

    @app.post("/api/purchases/{pid}/dispute/respond")
    async def respond(pid: str):
        l = L()
        return l.purchase_view(await l.respond_to_dispute(pid))

    @app.get("/api/purchases/{pid}/evidence.json")
    async def evidence_json(pid: str):
        bundle, _ = await L().evidence(pid)
        return JSONResponse(bundle, headers={"Content-Disposition": f'attachment; filename="leash-evidence-{pid}.json"'})

    @app.get("/api/purchases/{pid}/evidence.pdf")
    async def evidence_pdf(pid: str):
        _, pdf = await L().evidence(pid)
        return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="leash-evidence-{pid}.pdf"'})

    @app.post("/api/disputes/sync")
    async def disputes_sync():
        return {"linked": await L().sync_disputes()}

    # ------------------------------------------------------------------ owner controls

    @app.post("/api/kill-switch")
    async def kill_switch(payload: dict = Body(default={})):
        l = L()
        return await l.kill_switch() if payload.get("on", True) else l.unfreeze()

    @app.post("/api/keys")
    async def register_key(payload: dict = Body(...)):
        return L().register_device_key(payload.get("jwk") or {}, str(payload.get("label") or "browser"))

    @app.get("/api/ledger")
    async def ledger(subject: Optional[str] = None, after: int = 0, limit: int = 300):
        return L().store.entries(subject=subject, after=after, limit=min(limit, 1000))

    @app.get("/api/ledger/verify")
    async def ledger_verify():
        return L().store.verify_chain()

    @app.get("/api/paypal/calls")
    async def paypal_calls():
        return [c.to_dict() for c in L().paypal.calls][-200:]

    # ------------------------------------------------------------------ merchant-facing verification

    @app.post("/v1/verify")
    async def verify(payload: dict = Body(...)):
        """Merchants call this before fulfilling an agent's order. Receipts are single use."""
        return L().verify_receipt(str(payload.get("receipt", "")), payload.get("merchant_id"), payload.get("cart_hash"), consume=bool(payload.get("consume", True)))

    @app.get("/.well-known/jwks.json")
    async def jwks():
        return L().jwks()

    # ------------------------------------------------------------------ PayPal redirects and webhooks

    @app.get("/paypal/return", include_in_schema=False)
    async def paypal_return(kind: str, ref: str, token: str = "", PayerID: str = ""):
        l = L()
        try:
            await l.on_paypal_return(kind, ref)
            target = f"/#/app/mandates/{ref}?funded=1" if kind == "allowance" else f"/#/app/purchases/{ref}?approved=1"
        except (LeashError, PayPalError) as e:
            target = f"/#/app?error={quote(str(e)[:160])}"
        return RedirectResponse(target, status_code=303)

    @app.get("/paypal/cancel", include_in_schema=False)
    async def paypal_cancel(kind: str, ref: str, token: str = ""):
        L().log(ref, "human:owner", "paypal.cancelled", "Left PayPal without approving")
        return RedirectResponse(f"/#/app/{'mandates' if kind == 'allowance' else 'purchases'}/{ref}?cancelled=1", status_code=303)

    @app.post("/webhooks/paypal")
    async def webhook(request: Request):
        l = L()
        event = await request.json()
        if not await l.paypal.verify_webhook(dict(request.headers), event):
            l.log(None, "paypal:webhook", "webhook.rejected", "Webhook failed signature verification", {"event_type": event.get("event_type")})
            raise HTTPException(status_code=401, detail="webhook signature verification failed")
        return await l.handle_webhook(event)

    # ------------------------------------------------------------------ fixture-only simulated PayPal approval page

    def fixture() -> FixturePayPal:
        p = L().paypal
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
        rows = "".join(f"<tr><td>{html.escape(i['name'])}</td><td class=r>{html.escape(i['quantity'])} x ${html.escape(i['unit_amount']['value'])}</td></tr>" for i in pu.get("items", []))
        return HTMLResponse(_fixture_page(token, html.escape(pu.get("description", "")), rows, html.escape(pu["amount"]["value"])))

    @app.post("/fixture/paypal/checkoutnow/approve", include_in_schema=False)
    async def fixture_approve(token: str):
        res = fixture().approve_order(token)
        return RedirectResponse(_relative(res["return_url"]) + f"&token={token}&PayerID={res['payer_id']}", status_code=303)

    @app.post("/fixture/paypal/checkoutnow/cancel", include_in_schema=False)
    async def fixture_cancel(token: str):
        return RedirectResponse(_relative(fixture().cancel_url(token)) + f"&token={token}", status_code=303)

    def _relative(url: str) -> str:
        base = L().settings.public_base_url
        return url[len(base):] if url.startswith(base) else url

    @app.post("/api/demo/reset")
    async def reset():
        l = L()
        if l.paypal.mode != "fixture" and not l.settings.allow_reset:
            raise HTTPException(403, "reset is only available in fixture mode")
        l.store.reset()
        if isinstance(l.paypal, FixturePayPal):
            l.paypal.state = empty_state()
        l.frozen = l.settings.payments_frozen
        return {"ok": True}

    # ------------------------------------------------------------------ frontend

    if (DIST / "assets").exists():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    async def index():
        if (DIST / "index.html").exists():
            return FileResponse(DIST / "index.html")
        return HTMLResponse("<p>Frontend not built. Run <code>cd frontend && npm install && npm run build</code>, or use the API at <a href='/docs'>/docs</a>.</p>")

    @app.get("/{path:path}", include_in_schema=False)
    async def static_files(path: str):
        f = (DIST / path).resolve()
        if DIST.exists() and f.is_file() and DIST.resolve() in f.parents:
            return FileResponse(f)
        raise HTTPException(404)

    return app


def _fixture_page(token: str, description: str, rows: str, total: str) -> str:
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Simulated PayPal approval</title><style>
body{{margin:0;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif;color:#1a1615;min-height:100vh;
background:linear-gradient(180deg,#b7d5f1 0%,#dce6f0 46%,#f4e6da 100%);display:grid;place-items:center;padding:24px}}
.card{{width:100%;max-width:460px;background:#fff;border:1px solid rgba(26,22,21,.08);border-radius:24px;padding:28px;box-shadow:0 24px 60px -18px rgba(26,22,21,.28)}}
.chip{{display:inline-block;font:12px ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase;color:#757170;margin-bottom:14px}}
.warn{{background:#fbefe9;color:#8a3a20;border-radius:14px;padding:12px 14px;font-size:13.5px;line-height:1.5;margin-bottom:18px}}
h1{{font-size:22px;letter-spacing:-.02em;margin:0 0 6px}} p.m{{color:#757170;font-size:13.5px;margin:0 0 14px}}
table{{width:100%;border-collapse:collapse;margin:8px 0 18px}} td{{padding:9px 0;border-bottom:1px solid rgba(26,22,21,.08);font-size:14px}}
.r{{text-align:right;white-space:nowrap}} .t td{{font-weight:600;border:0}}
button{{width:100%;height:50px;border-radius:999px;font-size:15px;font-weight:600;cursor:pointer;margin-top:8px;font-family:inherit}}
.go{{background:#1a1615;color:#f9f8f8;border:0}} .no{{background:#fff;border:1px solid rgba(26,22,21,.14);color:#1a1615}}
button:focus-visible{{outline:2px solid #c9502e;outline-offset:3px}}</style></head><body><div class=card>
<div class=chip>Fixture mode</div><div class=warn><b>This page simulates PayPal.</b> No sandbox keys are configured, so no PayPal API was
called and no money moves. With sandbox keys, you approve on sandbox.paypal.com instead.</div>
<h1>Approve this authorization</h1><p class=m>{description}<br>Order {html.escape(token)}</p>
<table>{rows}<tr class=t><td>Authorize up to</td><td class=r>${total} USD</td></tr></table>
<form method=post action="/fixture/paypal/checkoutnow/approve?token={html.escape(token)}"><button class=go>Approve (simulated)</button></form>
<form method=post action="/fixture/paypal/checkoutnow/cancel?token={html.escape(token)}"><button class=no>Cancel</button></form>
</div></body></html>"""


app = create_app()
