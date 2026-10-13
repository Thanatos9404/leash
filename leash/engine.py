"""Leash engine.

    compile -> sign -> fund (PayPal AUTHORIZE allowance, approved once by the user) -> active
    agent proposes cart -> policy decides allow / ask / deny
        allow -> authorization receipt -> merchant verifies -> partial capture (final_capture=false) -> settlement receipt
        ask   -> user signs a cart approval -> same path
        deny  -> blocked with reasons; the user may approve a one-off exception, which is its own PayPal AUTHORIZE
                 order the user approves on PayPal, captured only after merchant verification, voided otherwise
    revoke / kill switch -> void the allowance at PayPal; nothing can be captured after that

The model never touches this file's money paths. Every PayPal write uses a PayPal-Request-Id derived from the
mandate or purchase id, and a per-mandate lock serialises captures so two purchases cannot race the allowance.
"""

from __future__ import annotations

import asyncio
import random
import string
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from .agent import SCENARIOS, Plan, ShoppingAgent
from .catalog import Catalog
from .compiler import compile_mandate, envelope_amount, render
from .config import Settings
from .crypto import KeyPair, SignatureError, jws_peek, jws_sign, jws_verify, sha256_hex
from .llm import LLM
from .models import Cart, CartItem, Mandate, Policy, Purchase, now
from .money import D, fmt
from .paypal import FixturePayPal, PayPalAPI, PayPalError, approve_link, first_authorization
from .policy import COMMITTED, Context, evaluate
from .receipts import RECEIPT_TYP, build_receipt, cart_hash, evidence_lines, receipt_hash, text_pdf
from .store import Store

MANDATE_TYP = "leash-mandate+jws"
APPROVAL_TYP = "leash-cart-approval+jws"
OPEN_AUTH = {"CREATED", "PARTIALLY_CAPTURED", "PENDING"}


class LeashError(RuntimeError):
    pass


def _rid(prefix: str, n: int = 10) -> str:
    return prefix + "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))


class Leash:
    def __init__(self, settings: Settings, store: Store, paypal: PayPalAPI, llm: LLM, catalog: Catalog,
                 receipt_key: KeyPair, custodial_key: KeyPair) -> None:
        self.settings = settings
        self.store = store
        self.paypal = paypal
        self.llm = llm
        self.catalog = catalog
        self.receipt_key = receipt_key
        self.custodial_key = custodial_key
        self.agent = ShoppingAgent(catalog, llm)
        self._locks: dict[str, asyncio.Lock] = {}
        self.frozen = settings.payments_frozen

    # ------------------------------------------------------------------ helpers

    def lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def log(self, subject: Optional[str], actor: str, kind: str, title: str, data: Optional[dict] = None) -> dict:
        return self.store.append(subject, actor, kind, title, data or {})

    def mandate(self, mid: str) -> Mandate:
        m = self.store.mandate(mid)
        if not m:
            raise LeashError(f"unknown mandate {mid}")
        return m

    def purchase(self, pid: str) -> Purchase:
        p = self.store.purchase(pid)
        if not p:
            raise LeashError(f"unknown purchase {pid}")
        return p

    @property
    def processor(self) -> str:
        return f"paypal-{self.paypal.mode}"

    def jwks(self) -> dict:
        keys = [self.receipt_key.public_jwk(), self.custodial_key.public_jwk()]
        keys += [k["jwk"] for k in (self.store.kv_get("device_keys") or {}).values()]
        return {"keys": keys}

    def signer_key(self, kid: str) -> Optional[dict]:
        if kid == self.custodial_key.kid:
            return self.custodial_key.public_jwk()
        dev = (self.store.kv_get("device_keys") or {}).get(kid)
        return dev["jwk"] if dev else None

    def register_device_key(self, jwk: dict, label: str) -> dict:
        from .crypto import public_key_from_jwk

        public_key_from_jwk(jwk)  # validates shape and length
        kid = "dev_" + sha256_hex(jwk["x"].encode())[:12]
        keys = self.store.kv_get("device_keys") or {}
        keys[kid] = {"jwk": {"kty": "OKP", "crv": "Ed25519", "x": jwk["x"], "kid": kid, "use": "sig", "alg": "EdDSA"}, "label": label[:60], "at": time.time()}
        self.store.kv_set("device_keys", keys)
        self.log(None, "human:owner", "key.registered", f"Registered signing key {kid} ({label[:40]})", {"kid": kid})
        return {"kid": kid}

    # ------------------------------------------------------------------ 1. mandates

    async def compile(self, text: str) -> Mandate:
        if not text.strip():
            raise LeashError("Describe what the agent may buy.")
        policy, report = await compile_mandate(text, self.catalog.directory, self.llm)
        m = Mandate(id=_rid("mdt_"), owner=self.settings.owner_name, source_text=text.strip()[:2000], policy=policy,
                    plain_language=render(policy, self.catalog.directory), policy_hash=sha256_hex(policy.model_dump(mode="json")), compiler=report)
        self.store.save_mandate(m)
        self.log(m.id, "agent:compiler", "mandate.compiled", f"Compiled '{policy.title}' from plain language",
                 {"ai": report["ai"], "warnings": report["warnings"], "policy_hash": m.policy_hash})
        return m

    def update_draft(self, mid: str, patch: dict) -> Mandate:
        m = self.mandate(mid)
        if m.status != "draft":
            raise LeashError("Only a draft can be edited. Revoke and create a new mandate instead.")
        data = m.policy.model_dump()
        for key in ("title", "purpose"):
            if patch.get(key):
                data[key] = str(patch[key])[:160]
        for key in ("per_purchase_max", "ask_above"):
            if key in patch:
                data[key] = D(patch[key]) if patch[key] not in (None, "") else None
        if "budget" in patch:
            b = patch["budget"]
            data["budget"] = {"amount": D(b["amount"]), "period": b.get("period", "month")} if b else None
        if "merchants_allow" in patch:
            ids = [x for x in patch["merchants_allow"] if (mm := self.catalog.directory.merchant(x)) and mm.verified]
            data["merchants_allow"] = ids
        for key in ("categories_allow", "categories_block"):
            if key in patch:
                data[key] = [str(c) for c in patch[key]]
        if "allow_subscriptions" in patch:
            data["allow_subscriptions"] = bool(patch["allow_subscriptions"])
        if "max_purchases" in patch:
            data["max_purchases"] = patch["max_purchases"] or None
        policy = Policy(**data)
        if "gift_cards" not in policy.categories_block:
            policy.categories_block.append("gift_cards")
        if policy.budget is None and policy.per_purchase_max is None:
            raise LeashError("A mandate needs a budget or a per-purchase cap.")
        m.policy = policy
        m.policy_hash = sha256_hex(policy.model_dump(mode="json"))
        m.plain_language = render(policy, self.catalog.directory)
        self.store.save_mandate(m)
        self.log(m.id, "human:owner", "mandate.edited", "Edited the draft mandate", {"policy_hash": m.policy_hash})
        return m

    def signing_payload(self, m: Mandate) -> dict:
        return {
            "typ": "leash.intent-mandate/1",
            "mandate_id": m.id,
            "owner": m.owner,
            "agent_id": m.agent_id,
            "policy_hash": m.policy_hash,
            "plain_language_hash": sha256_hex(m.plain_language),
            "plain_language": m.plain_language,
            "valid_until": m.policy.valid_until.isoformat(),
            "iat": int(time.time()),
        }

    def verify_mandate(self, m: Mandate) -> tuple[bool, str]:
        """Signature valid, signed payload matches the stored policy, signer key known."""
        if not m.token:
            return False, "the mandate has not been signed"
        try:
            header, _ = jws_peek(m.token)
            jwk = self.signer_key(header.get("kid", ""))
            if not jwk:
                return False, "unknown signing key"
            _, payload = jws_verify(m.token, jwk, MANDATE_TYP)
        except SignatureError as e:
            return False, str(e)
        if payload.get("mandate_id") != m.id or payload.get("agent_id") != m.agent_id:
            return False, "signed for a different mandate or agent"
        if payload.get("policy_hash") != sha256_hex(m.policy.model_dump(mode="json")):
            return False, "the policy changed after you signed it"
        return True, "ok"

    async def sign(self, mid: str, token: Optional[str] = None) -> Mandate:
        async with self.lock(mid):
            m = self.mandate(mid)
            if m.status != "draft":
                raise LeashError(f"Mandate is {m.status}; only drafts can be signed.")
            if m.policy.budget is None and m.policy.per_purchase_max is None:
                raise LeashError("Add a budget or per-purchase cap before signing.")
            if token is None:
                token = jws_sign(self.signing_payload(m), self.custodial_key, MANDATE_TYP)
                signer = {"kid": self.custodial_key.kid, "type": "custodial-demo"}
            else:
                header, _ = jws_peek(token)
                signer = {"kid": header.get("kid"), "type": "device"}
            m.token = token
            m.signer = signer
            ok, why = self.verify_mandate(m)
            if not ok:
                raise LeashError(f"Signature rejected: {why}")
            m.status = "signed"
            self.store.save_mandate(m)
            self.log(m.id, "human:owner", "mandate.signed", f"Signed '{m.policy.title}' ({signer['type']} key {signer['kid']})",
                     {"token_hash": sha256_hex(token.encode()), "policy_hash": m.policy_hash})
            return m

    async def fund(self, mid: str) -> Mandate:
        """Create the PayPal allowance: an AUTHORIZE order the user approves once."""
        async with self.lock(mid):
            m = self.mandate(mid)
            if m.status not in ("signed", "awaiting_funding"):
                raise LeashError(f"Mandate is {m.status}; sign it first.")
            if self.frozen:
                raise LeashError("Payments are frozen (kill switch).")
            if m.envelope.order_id and m.status == "awaiting_funding":
                return m
            amount = envelope_amount(m.policy)
            base = self.settings.public_base_url
            body = {
                "intent": "AUTHORIZE",
                "purchase_units": [{
                    "reference_id": m.id, "custom_id": m.id, "invoice_id": f"{m.id}-allowance",
                    "description": f"Leash allowance for your AI agent: {m.policy.title}"[:127],
                    "soft_descriptor": "LEASH ALLOWANCE",
                    "amount": {"currency_code": "USD", "value": fmt(amount), "breakdown": {"item_total": {"currency_code": "USD", "value": fmt(amount)}}},
                    "items": [{"name": f"Agent allowance: {m.policy.title}"[:127], "quantity": "1",
                               "description": "Held for purchases your signed Leash mandate allows. Unused funds are released."[:127],
                               "unit_amount": {"currency_code": "USD", "value": fmt(amount)}}],
                }],
                "payment_source": {"paypal": {"experience_context": {
                    "brand_name": "Leash (sandbox)", "user_action": "CONTINUE", "shipping_preference": "NO_SHIPPING",
                    "return_url": f"{base}/paypal/return?kind=allowance&ref={m.id}",
                    "cancel_url": f"{base}/paypal/cancel?kind=allowance&ref={m.id}"}}},
            }
            resp = await self.paypal.create_order(body, f"leash-{m.id}-allowance-order")
            m.envelope.order_id = resp["id"]
            m.envelope.order_status = resp.get("status")
            m.envelope.approve_url = approve_link(resp)
            m.envelope.amount = D(amount)
            m.status = "awaiting_funding"
            self.store.save_mandate(m)
            self.log(m.id, self.processor, "allowance.order_created", f"PayPal AUTHORIZE order {resp['id']} for ${fmt(amount)}",
                     {"order_id": resp["id"], "request_id": f"leash-{m.id}-allowance-order"})
            return m

    async def on_paypal_return(self, kind: str, ref: str) -> str:
        """The user came back from PayPal. Returns the id to show (mandate or purchase)."""
        if kind == "allowance":
            await self._authorize_allowance(ref)
            return ref
        if kind == "exception":
            await self._authorize_exception(ref)
            return ref
        raise LeashError("unknown return kind")

    async def _authorize_allowance(self, mid: str) -> Mandate:
        async with self.lock(mid):
            m = self.mandate(mid)
            if m.envelope.authorization_id or not m.envelope.order_id:
                return m
            order = await self.paypal.get_order(m.envelope.order_id)
            m.envelope.order_status = order.get("status")
            if order.get("status") != "APPROVED":
                self.store.save_mandate(m)
                return m
            res = await self.paypal.authorize_order(m.envelope.order_id, f"leash-{m.id}-allowance-authorize")
            auth = first_authorization(res)
            m.envelope.authorization_id = auth.get("id")
            m.envelope.authorization_status = auth.get("status")
            m.envelope.expires_at = auth.get("expiration_time")
            m.status = "active"
            m.activated_at = now()
            self.store.save_mandate(m)
            self.log(m.id, self.processor, "allowance.authorized", f"Allowance authorized: ${m.envelope.amount} held by PayPal",
                     {"authorization_id": m.envelope.authorization_id, "expires": m.envelope.expires_at})
            return m

    async def revoke(self, mid: str, by: str = "owner", reason: str = "Revoked by owner") -> Mandate:
        async with self.lock(mid):
            m = self.mandate(mid)
            if m.status in ("revoked",):
                return m
            m.status = "revoked"
            m.revoked_at = now()
            m.revoke_reason = reason[:200]
            await self._void_allowance(m, f"leash-{m.id}-allowance-void")
            self.store.save_mandate(m)
            self.log(m.id, f"human:{by}", "mandate.revoked", f"Revoked '{m.policy.title}': {reason[:80]}", {"authorization_status": m.envelope.authorization_status})
        for p in self.store.purchases(mid):
            if p.status in ("awaiting_approval", "awaiting_paypal"):
                await self._cancel_pending(p, "Mandate revoked")
        return m

    async def kill_switch(self, by: str = "owner") -> dict:
        """Freeze all payments and revoke every live mandate."""
        self.frozen = True
        revoked = []
        for m in self.store.mandates():
            if m.status in ("active", "awaiting_funding", "signed", "draft"):
                await self.revoke(m.id, by, "Kill switch")
                revoked.append(m.id)
        self.log(None, f"human:{by}", "kill_switch.on", f"Kill switch: payments frozen, {len(revoked)} mandates revoked", {"mandates": revoked})
        return {"frozen": True, "revoked": revoked}

    def unfreeze(self, by: str = "owner") -> dict:
        self.frozen = self.settings.payments_frozen
        self.log(None, f"human:{by}", "kill_switch.off", "Kill switch released (revoked mandates stay revoked)")
        return {"frozen": self.frozen}

    async def _void_allowance(self, m: Mandate, request_id: str) -> None:
        if not m.envelope.authorization_id or m.envelope.authorization_status not in OPEN_AUTH:
            return
        try:
            res = await self.paypal.void_authorization(m.envelope.authorization_id, request_id)
            m.envelope.authorization_status = res.get("status", "VOIDED") if res else "VOIDED"
            m.envelope.voided_at = now()
            self.log(m.id, self.processor, "allowance.voided", f"PayPal authorization {m.envelope.authorization_id} voided; ${self.allowance_left(m)} released",
                     {"authorization_id": m.envelope.authorization_id})
        except PayPalError as e:
            self.log(m.id, self.processor, "allowance.void_failed", "PayPal refused the void", {"error": str(e)})
            if e.issue in ("AUTHORIZATION_ALREADY_VOIDED", "PREVIOUSLY_CAPTURED"):
                m.envelope.authorization_status = "VOIDED" if "VOIDED" in e.issue else "CAPTURED"

    def allowance_left(self, m: Mandate) -> Decimal:
        if m.envelope.authorization_status not in OPEN_AUTH:
            return Decimal("0.00")
        return D(m.envelope.amount - m.envelope.captured)

    def expire_sweep(self) -> None:
        for m in self.store.mandates():
            if m.status in ("active", "awaiting_funding", "signed") and now() > m.policy.valid_until:
                m.status = "expired"
                self.store.save_mandate(m)
                self.log(m.id, "system", "mandate.expired", f"'{m.policy.title}' expired")

    # ------------------------------------------------------------------ 2. agent purchases

    def _make_cart(self, plan: Plan) -> Cart:
        """The merchant's cart service (PayPal agentic Cart API shape): the merchant, not the agent, prices the cart."""
        prod = plan.product
        merchant = self.catalog.directory.merchant(prod.merchant_id)
        unit = plan.price_override if plan.price_override is not None else prod.price
        items = [CartItem(product_id=prod.id, name=prod.title, quantity=plan.quantity, unit_price=D(unit), category=prod.category,
                          is_subscription=prod.is_subscription, item_url=prod.url)]
        subtotal = D(sum((i.unit_price * i.quantity for i in items), Decimal("0")))
        cart = Cart(id=_rid("cart_"), merchant_id=prod.merchant_id, merchant_name=merchant.name if merchant else prod.merchant_id,
                    merchant_domain=plan.merchant_domain_override or (merchant.domain if merchant else ""), items=items,
                    subtotal=subtotal, total=subtotal, nonce=_rid("n_", 16))
        cart.cart_hash = cart_hash(cart.model_dump(mode="json"))
        return cart

    async def run_agent(self, mid: str, task: str) -> Purchase:
        m = self.mandate(mid)
        self.log(mid, "agent:shopper", "agent.task", f"Agent task: {task[:100]}")
        plan = await self.agent.plan(task, m)
        if not plan.product:
            raise LeashError(plan.reasoning)
        self.log(mid, "agent:shopper", "agent.chose", f"Agent chose {plan.quantity} x {plan.product.title}",
                 {"ai": plan.ai, "query": plan.query, "candidates": [c.id for c in plan.candidates][:12]})
        return await self.checkout(mid, self._make_cart(plan), task, plan.reasoning, m.agent_id, plan.ai)

    async def run_scenario(self, mid: str, name: str) -> Purchase:
        if name == "replay":
            return await self.replay_last(mid)
        if name not in SCENARIOS:
            raise LeashError(f"unknown scenario {name}")
        m = self.mandate(mid)
        plan = self.agent.scenario(name)
        self.log(mid, "agent:red-team", "agent.scenario", f"Red-team: {SCENARIOS[name]['label']}")
        return await self.checkout(mid, self._make_cart(plan), f"Red-team: {SCENARIOS[name]['label']}", plan.reasoning, m.agent_id, plan.ai)

    async def replay_last(self, mid: str) -> Purchase:
        last = next((p for p in self.store.purchases(mid) if p.status == "captured"), None)
        if not last:
            raise LeashError("Make one purchase first; the replay scenario resubmits its cart.")
        self.log(mid, "agent:red-team", "agent.scenario", "Red-team: replaying a completed cart")
        return await self.checkout(mid, last.cart, "Red-team: replay a completed cart", "Resubmitting the exact cart (same nonce) a second time.", last.agent_id, "red-team script")

    def _decide(self, m: Mandate, cart: Cart, agent_id: str, history: list[Purchase]) -> Any:
        ok, why = self.verify_mandate(m)
        return evaluate(Context(
            mandate=m, cart=cart, agent_id=agent_id, directory=self.catalog.directory, history=history, now=now(),
            signature_ok=ok, signature_detail=why, used_nonces={cart.nonce} if self.store.nonce_used(cart.nonce) else set(),
            payments_frozen=self.frozen, allowance_left=self.allowance_left(m) if m.envelope.authorization_id else None,
        ))

    async def checkout(self, mid: str, cart: Cart, task: str, reasoning: str, agent_id: str, agent_ai: str) -> Purchase:
        async with self.lock(mid):
            m = self.mandate(mid)
            history = [p for p in self.store.purchases(mid)]
            decision = self._decide(m, cart, agent_id, history)
            self.store.use_nonce(cart.nonce, f"checkout:{cart.id}")
            status = {"allow": "verifying", "ask": "awaiting_approval", "deny": "blocked"}[decision.outcome]
            p = Purchase(id=_rid("pur_"), mandate_id=mid, agent_id=agent_id, task=task[:300], reasoning=reasoning[:1000], agent_ai=agent_ai,
                         cart=cart, decision=decision, status=status)
            if decision.outcome == "allow":
                p.approval = {"method": "auto", "by": "mandate", "at": now().isoformat()}
            self.store.save_purchase(p)
            self.log(mid, "policy", f"decision.{decision.outcome}", f"{decision.outcome.upper()}: ${cart.total} at {cart.merchant_name}. {decision.summary}",
                     {"purchase": p.id, "failed": [c.rule for c in decision.failures], "cart_hash": cart.cart_hash})
            if decision.outcome == "allow":
                p = await self._settle(m, p)
            return p

    async def approve(self, pid: str, by: str = "owner", token: Optional[str] = None) -> Purchase:
        """Step-up approval: the user signs this exact cart (AP2 v0.1 called the equivalent a cart mandate)."""
        p = self.purchase(pid)
        async with self.lock(p.mandate_id):
            p = self.purchase(pid)
            m = self.mandate(p.mandate_id)
            if p.status != "awaiting_approval":
                raise LeashError(f"Purchase is {p.status}, not waiting for approval.")
            payload = {"typ": "leash.cart-approval/1", "purchase_id": p.id, "mandate_id": m.id, "cart_hash": p.cart.cart_hash,
                       "merchant_id": p.cart.merchant_id, "total": str(p.cart.total), "iat": int(time.time())}
            if token is None:
                token = jws_sign(payload, self.custodial_key, APPROVAL_TYP)
            try:
                header, _ = jws_peek(token)
                jwk = self.signer_key(header.get("kid", ""))
                if not jwk:
                    raise SignatureError("unknown signing key")
                _, signed = jws_verify(token, jwk, APPROVAL_TYP)
            except SignatureError as e:
                raise LeashError(f"Approval signature rejected: {e}") from e
            if signed.get("cart_hash") != p.cart.cart_hash or signed.get("purchase_id") != p.id:
                raise LeashError("Approval was signed for a different cart.")
            # Re-check hard rules now: the approval covers ask-rules only, never a hard deny.
            history = [x for x in self.store.purchases(m.id) if x.id != p.id]
            again = self._decide(m, p.cart.model_copy(update={"nonce": p.cart.nonce + ":approved"}), p.agent_id, history)
            hard = [c for c in again.checks if not c.ok and c.severity == "deny"]
            if hard:
                p.status = "blocked"
                p.decision = again
                self.store.save_purchase(p)
                self.log(m.id, "policy", "decision.deny", f"Approval refused: {hard[0].detail}", {"purchase": p.id})
                return p
            p.approval = {"method": "user-signature", "by": by, "at": now().isoformat(), "token_hash": sha256_hex(token.encode()), "kid": header.get("kid")}
            p.status = "verifying"
            self.store.save_purchase(p)
            self.log(m.id, f"human:{by}", "purchase.approved", f"Approved ${p.cart.total} at {p.cart.merchant_name} (signed cart approval)", {"purchase": p.id})
            return await self._settle(m, p)

    async def reject(self, pid: str, by: str = "owner", reason: str = "") -> Purchase:
        p = self.purchase(pid)
        async with self.lock(p.mandate_id):
            p = self.purchase(pid)
            if p.status not in ("awaiting_approval", "awaiting_paypal", "blocked"):
                raise LeashError(f"Purchase is {p.status}.")
            await self._cancel_pending(p, reason or "Rejected by owner", by)
            return self.purchase(pid)

    async def _cancel_pending(self, p: Purchase, reason: str, by: str = "system") -> None:
        if p.payment.get("mode") == "exception" and p.payment.get("authorization_id") and p.status != "captured":
            await self._void_exception(p, reason)
        else:
            p.status = "rejected"
        p.error = reason[:200]
        p.updated_at = now()
        self.store.save_purchase(p)
        self.log(p.mandate_id, f"human:{by}" if by != "system" else "system", "purchase.rejected", f"Rejected ${p.cart.total} at {p.cart.merchant_name}: {reason[:80]}", {"purchase": p.id})

    # ------------------------------------------------------------------ 3. merchant verification and settlement

    def verify_receipt(self, token: str, merchant_id: Optional[str] = None, cart_hash_value: Optional[str] = None, consume: bool = True) -> dict:
        """What a merchant calls before fulfilling an agent's order (POST /v1/verify)."""
        checks: list[dict] = []

        def add(name: str, ok: bool, detail: str) -> None:
            checks.append({"check": name, "ok": bool(ok), "detail": detail})

        try:
            header, payload = jws_verify(token, self.receipt_key.public, RECEIPT_TYP)
            add("signature", header.get("kid") == self.receipt_key.kid, "Signed by Leash's receipt key")
        except SignatureError as e:
            return {"valid": False, "checks": [{"check": "signature", "ok": False, "detail": str(e)}]}
        stage_ok = payload.get("typ") == "leash.intent-receipt/authorization"
        add("stage", stage_ok, "Pre-capture authorization receipt" if stage_ok else "Only authorization receipts can be used to fulfil")
        exp_ok = int(payload.get("exp", 0)) >= int(time.time())
        add("fresh", exp_ok, "Not expired" if exp_ok else "Receipt expired")
        m = self.store.mandate(payload.get("mandate", {}).get("id", ""))
        if not m:
            add("mandate", False, "Unknown mandate")
        else:
            add("mandate_active", m.status == "active", f"Mandate is {m.status}")
            add("mandate_unchanged", payload["mandate"].get("policy_hash") == m.policy_hash and payload["mandate"].get("token_hash") == sha256_hex((m.token or '').encode()),
                "Mandate and signature unchanged since the receipt was issued")
            left = self.allowance_left(m) if m.envelope.authorization_id else None
            if payload.get("payment", {}).get("mode") == "allowance":
                add("funds", left is not None and D(payload["cart"]["total"]) <= left, f"${left} left on the PayPal allowance")
        if merchant_id is not None:
            add("merchant", payload.get("cart", {}).get("merchant_id") == merchant_id, "Receipt is for this merchant" if payload.get("cart", {}).get("merchant_id") == merchant_id else "Receipt was issued for a different merchant")
        if cart_hash_value is not None:
            add("cart", payload.get("cart", {}).get("hash") == cart_hash_value, "Cart matches what the user's agent was authorized to buy" if payload.get("cart", {}).get("hash") == cart_hash_value else "Cart differs from the authorized cart")
        valid_so_far = all(c["ok"] for c in checks)
        if consume and valid_so_far:
            fresh = self.store.use_nonce("receipt:" + payload["jti"], "verify")
            add("single_use", fresh, "First presentation of this receipt" if fresh else "This receipt was already used: replay refused")
        valid = all(c["ok"] for c in checks)
        return {"valid": valid, "checks": checks, "jti": payload.get("jti"), "mandate": payload.get("mandate", {}).get("id"),
                "amount": payload.get("cart", {}).get("total"), "merchant_id": payload.get("cart", {}).get("merchant_id")}

    def inspect_receipt(self, token: str) -> dict:
        """Read-only verification of any receipt (for people and auditors; does not consume anything)."""
        checks: list[dict] = []
        try:
            header, payload = jws_verify(token.strip(), self.receipt_key.public, RECEIPT_TYP)
            checks.append({"check": "signature", "ok": True, "detail": f"Valid Ed25519 signature by {header.get('kid')}"})
        except SignatureError as e:
            return {"valid": False, "checks": [{"check": "signature", "ok": False, "detail": str(e)}], "payload": None}
        m = self.store.mandate(payload.get("mandate", {}).get("id", ""))
        if m:
            same = payload["mandate"].get("token_hash") == sha256_hex((m.token or "").encode())
            checks.append({"check": "mandate", "ok": same, "detail": "Bound to the mandate you signed" if same else "Mandate token differs"})
        else:
            checks.append({"check": "mandate", "ok": False, "detail": "Mandate not found on this server"})
        pid = payload.get("jti", "").split(":")[0]
        p = self.store.purchase(pid)
        if p and payload.get("typ", "").endswith("settlement"):
            ok = p.payment.get("capture_id") == payload.get("payment", {}).get("capture_id") and p.cart.cart_hash == payload.get("cart", {}).get("hash")
            checks.append({"check": "purchase", "ok": ok, "detail": f"Matches PayPal capture {p.payment.get('capture_id')}" if ok else "Does not match the recorded purchase"})
        prev = payload.get("prev")
        if prev:
            linked = any(x.receipt_hash == prev for x in self.store.purchases(payload["mandate"]["id"]))
            checks.append({"check": "chain", "ok": linked, "detail": "Links to the previous receipt in this mandate's chain" if linked else "Previous receipt not found"})
        else:
            checks.append({"check": "chain", "ok": True, "detail": "First receipt in this mandate's chain"})
        return {"valid": all(c["ok"] for c in checks), "checks": checks, "payload": payload, "hash": receipt_hash(token.strip())}

    async def _settle(self, m: Mandate, p: Purchase, mode: str = "allowance") -> Purchase:
        """Issue the authorization receipt, have the merchant verify it, then capture. Caller holds the mandate lock."""
        p.payment.update({"mode": mode})
        if mode == "allowance":
            p.payment.update({"order_id": m.envelope.order_id, "authorization_id": m.envelope.authorization_id})
        auth_receipt = build_receipt("authorization", p, m, self.receipt_key, self.processor, m.receipt_head)
        merchant = self.catalog.directory.merchant(p.cart.merchant_id)
        result = self.verify_receipt(auth_receipt, merchant_id=merchant.id if merchant else None, cart_hash_value=p.cart.cart_hash)
        p.verification = {"ok": result["valid"], "at": now().isoformat(), "checks": result["checks"], "verifier": merchant.domain if merchant else "unknown"}
        self.log(m.id, f"merchant:{merchant.domain if merchant else '?'}", "merchant.verified" if result["valid"] else "merchant.refused",
                 f"Merchant {'verified' if result['valid'] else 'could not verify'} the agent's authority", {"purchase": p.id, "checks": result["checks"]})
        if not result["valid"]:
            if mode == "exception":
                await self._void_exception(p, "Merchant verification failed")
            else:
                p.status = "rejected"
            p.error = "Merchant verification failed: " + "; ".join(c["detail"] for c in result["checks"] if not c["ok"])
            self.store.save_purchase(p)
            return p
        if self.frozen:
            p.status = "rejected"
            p.error = "Payments frozen before capture."
            self.store.save_purchase(p)
            return p
        if mode == "allowance":
            left = self.allowance_left(m)
            if p.cart.total > left:  # PayPal would allow up to 115% / +$75; Leash never captures past the allowance
                p.status, p.error = "rejected", f"Capture of ${p.cart.total} exceeds the ${left} left on the allowance."
                self.store.save_purchase(p)
                return p
        auth_id = p.payment["authorization_id"]
        body = {
            "amount": {"currency_code": "USD", "value": fmt(p.cart.total)},
            "invoice_id": p.id,
            "final_capture": mode == "exception",
            "note_to_payer": f"{p.cart.merchant_name} via your Leash agent: " + ", ".join(f"{i.quantity} x {i.name}" for i in p.cart.items),
            "soft_descriptor": ("LEASH*" + "".join(ch for ch in p.cart.merchant_name.upper() if ch.isalnum()))[:22],
        }
        body["note_to_payer"] = body["note_to_payer"][:255]
        try:
            cap = await self.paypal.capture_authorization(auth_id, body, f"leash-{p.id}-capture")
        except PayPalError as e:
            p.status, p.error = "failed", str(e)
            self.store.save_purchase(p)
            self.log(m.id, self.processor, "capture.failed", f"PayPal refused the capture: {e.name}", {"purchase": p.id, "issue": e.issue})
            return p
        fee = (cap.get("seller_receivable_breakdown") or {}).get("paypal_fee", {}).get("value")
        p.payment.update({"capture_id": cap["id"], "capture_status": cap.get("status"), "paypal_fee": fee, "captured_at": now().isoformat()})
        p.status = "captured"
        if mode == "allowance":
            m.envelope.captured = D(m.envelope.captured + p.cart.total)
            m.envelope.authorization_status = "CAPTURED" if m.envelope.captured >= m.envelope.amount else "PARTIALLY_CAPTURED"
        p.receipt = build_receipt("settlement", p, m, self.receipt_key, self.processor, m.receipt_head)
        p.receipt_hash = receipt_hash(p.receipt)
        m.receipt_head = p.receipt_hash
        p.updated_at = now()
        self.store.save_purchase(p)
        self.store.save_mandate(m)
        self.log(m.id, self.processor, "capture.completed", f"Captured ${p.cart.total} at {p.cart.merchant_name}",
                 {"purchase": p.id, "capture_id": cap["id"], "request_id": f"leash-{p.id}-capture", "receipt_hash": p.receipt_hash})
        return p

    # ------------------------------------------------------------------ 4. one-off exceptions (hard-rule overrides by the human)

    async def approve_exception(self, pid: str, by: str = "owner") -> Purchase:
        """The human (never the model) allows a blocked cart once. It gets its own AUTHORIZE order approved on PayPal."""
        p = self.purchase(pid)
        async with self.lock(p.mandate_id):
            p = self.purchase(pid)
            m = self.mandate(p.mandate_id)
            if p.status != "blocked":
                raise LeashError("Only blocked purchases can be approved as exceptions.")
            if self.frozen:
                raise LeashError("Payments are frozen (kill switch).")
            fatal = [c.rule for c in p.decision.failures if c.rule in ("merchant_verified", "cart_integrity", "price_check", "replay", "agent_authorized", "mandate_signature", "kill_switch", "mandate_active")]
            if m.status != "active":
                fatal.append("mandate_active")
            if fatal:
                raise LeashError("This cart cannot be approved: " + ", ".join(fatal).replace("_", " ") + " failed. Those protect you even from yourself.")
            base = self.settings.public_base_url
            items = [{"name": i.name[:127], "quantity": str(i.quantity), "sku": i.product_id[:127],
                      "unit_amount": {"currency_code": "USD", "value": fmt(i.unit_price)}} for i in p.cart.items]
            body = {
                "intent": "AUTHORIZE",
                "purchase_units": [{"reference_id": p.id, "custom_id": m.id, "invoice_id": f"{p.id}-exception",
                                    "description": f"One-off purchase your Leash agent proposed at {p.cart.merchant_name}"[:127],
                                    "amount": {"currency_code": "USD", "value": fmt(p.cart.total), "breakdown": {"item_total": {"currency_code": "USD", "value": fmt(p.cart.subtotal)}}},
                                    "items": items}],
                "payment_source": {"paypal": {"experience_context": {
                    "brand_name": "Leash (sandbox)", "user_action": "CONTINUE", "shipping_preference": "NO_SHIPPING",
                    "return_url": f"{base}/paypal/return?kind=exception&ref={p.id}", "cancel_url": f"{base}/paypal/cancel?kind=exception&ref={p.id}"}}},
            }
            resp = await self.paypal.create_order(body, f"leash-{p.id}-exception-order")
            p.payment = {"mode": "exception", "order_id": resp["id"], "approve_url": approve_link(resp)}
            p.approval = {"method": "paypal-exception", "by": by, "at": now().isoformat()}
            p.status = "awaiting_paypal"
            self.store.save_purchase(p)
            self.log(m.id, f"human:{by}", "exception.requested", f"Exception requested for ${p.cart.total} at {p.cart.merchant_name}; approve it on PayPal",
                     {"purchase": p.id, "order_id": resp["id"]})
            return p

    async def _authorize_exception(self, pid: str) -> Purchase:
        p = self.purchase(pid)
        async with self.lock(p.mandate_id):
            p = self.purchase(pid)
            m = self.mandate(p.mandate_id)
            if p.status != "awaiting_paypal":
                return p
            order = await self.paypal.get_order(p.payment["order_id"])
            if order.get("status") != "APPROVED":
                return p
            res = await self.paypal.authorize_order(p.payment["order_id"], f"leash-{p.id}-exception-authorize")
            p.payment["authorization_id"] = first_authorization(res).get("id")
            self.log(m.id, self.processor, "exception.authorized", f"One-off authorization {p.payment['authorization_id']} placed", {"purchase": p.id})
            p.status = "verifying"
            if m.status != "active":
                await self._void_exception(p, f"Mandate is {m.status}")
                self.store.save_purchase(p)
                return p
            return await self._settle(m, p, mode="exception")

    async def _void_exception(self, p: Purchase, reason: str) -> None:
        auth_id = p.payment.get("authorization_id")
        if auth_id:
            try:
                await self.paypal.void_authorization(auth_id, f"leash-{p.id}-exception-void")
                self.log(p.mandate_id, self.processor, "exception.voided", f"Voided authorization {auth_id}: {reason}", {"purchase": p.id})
            except PayPalError as e:
                self.log(p.mandate_id, self.processor, "exception.void_failed", str(e), {"purchase": p.id})
        p.status = "voided" if auth_id else "rejected"
        p.error = reason

    # ------------------------------------------------------------------ 5. refunds and disputes

    async def refund(self, pid: str, amount: Optional[str] = None, reason: str = "Refund requested") -> Purchase:
        p = self.purchase(pid)
        async with self.lock(p.mandate_id):
            p = self.purchase(pid)
            if p.status != "captured" or not p.payment.get("capture_id"):
                raise LeashError("Only captured purchases can be refunded.")
            refunded = sum((D(r["amount"]) for r in p.refunds), Decimal("0"))
            value = D(amount) if amount else D(p.cart.total - refunded)
            if value <= 0 or value > D(p.cart.total - refunded):
                raise LeashError("Refund amount must be positive and no more than what was captured.")
            n = len(p.refunds) + 1
            res = await self.paypal.refund_capture(p.payment["capture_id"], {"amount": {"currency_code": "USD", "value": fmt(value)},
                                                                            "note_to_payer": reason[:255], "invoice_id": f"{p.id}-refund-{n}"}, f"leash-{p.id}-refund-{n}")
            p.refunds.append({"id": res["id"], "status": res.get("status"), "amount": fmt(value), "reason": reason[:120], "at": now().isoformat()})
            if D(refunded + value) >= p.cart.total:
                p.status = "refunded"
            self.store.save_purchase(p)
            self.log(p.mandate_id, self.processor, "refund.completed", f"Refunded ${fmt(value)} to the buyer", {"purchase": p.id, "refund_id": res["id"]})
            return p

    def simulate_dispute(self, pid: str) -> Purchase:
        if not isinstance(self.paypal, FixturePayPal):
            raise LeashError("In sandbox mode, open the dispute as the sandbox buyer on sandbox.paypal.com, then sync.")
        p = self.purchase(pid)
        if not p.payment.get("capture_id"):
            raise LeashError("Only captured purchases can be disputed.")
        d = self.paypal.simulate_dispute(p.payment["capture_id"])
        self.log(p.mandate_id, "paypal:fixture", "dispute.opened", f"Buyer opened dispute {d['dispute_id']} ({d['reason']}) - simulated", {"purchase": p.id})
        p.dispute = {"id": d["dispute_id"], "reason": d["reason"], "status": d["status"], "amount": d["dispute_amount"]["value"], "simulated": True}
        self.store.save_purchase(p)
        return p

    async def sync_disputes(self) -> list[dict]:
        res = await self.paypal.list_disputes()
        linked = []
        by_capture = {p.payment.get("capture_id"): p for p in self.store.purchases() if p.payment.get("capture_id")}
        for item in res.get("items", []):
            d = await self.paypal.get_dispute(item["dispute_id"])
            for tx in d.get("disputed_transactions", []):
                p = by_capture.get(tx.get("seller_transaction_id"))
                if p:
                    p.dispute.update({"id": d["dispute_id"], "reason": d.get("reason"), "status": d.get("status"), "amount": (d.get("dispute_amount") or {}).get("value")})
                    self.store.save_purchase(p)
                    linked.append({"dispute_id": d["dispute_id"], "purchase": p.id, "status": d.get("status")})
        return linked

    async def dispute_statement(self, p: Purchase, m: Mandate) -> tuple[str, str]:
        approval = p.approval.get("method")
        facts = (f"Purchase {p.id}: ${p.cart.total} at {p.cart.merchant_name} ({p.cart.merchant_domain}) on {p.created_at:%d %b %Y}. "
                 f"Mandate {m.id} '{m.policy.title}' signed {m.signer.get('type')} key {m.signer.get('kid')}. "
                 f"Policy checks passed: {len([c for c in p.decision.checks if c.ok])} of {len(p.decision.checks)}. Approval: {approval}. "
                 f"Merchant verified receipt: {p.verification.get('ok')}. PayPal capture {p.payment.get('capture_id')}. Rules: {' '.join(m.plain_language)}")
        fallback = (f"This ${p.cart.total} purchase at {p.cart.merchant_name} was placed by the account holder's AI shopping agent under "
                    f"a spending mandate the account holder signed ({m.id}, '{m.policy.title}'). The cart passed every one of "
                    f"{len(p.decision.checks)} deterministic policy checks"
                    + (", and the account holder approved this exact cart with a signed approval" if approval == "user-signature" else "")
                    + f". Before capture the merchant verified Leash's signed intent receipt for this cart. PayPal capture "
                    f"{p.payment.get('capture_id')}. The signed mandate and receipt chain are attached and can be verified with the "
                    "public key published at /.well-known/jwks.json.")
        reply = await self.llm.json(
            "You draft a short, factual dispute response for a merchant. Use only the facts given; do not add claims. "
            'Return JSON: {"statement": string (max 110 words)}.', facts, purpose="dispute_statement", role="writer", max_tokens=300, temperature=0.2)
        text = str(reply.get("statement") or "") if reply else ""
        if text and str(p.cart.total) in text and p.cart.merchant_name.split()[0] in text:
            return text[:1200], self.llm.label(role="writer")
        return fallback, "template"

    async def evidence(self, pid: str) -> tuple[dict, bytes]:
        p = self.purchase(pid)
        m = self.mandate(p.mandate_id)
        if not p.receipt:
            raise LeashError("No settlement receipt for this purchase.")
        statement, ai = await self.dispute_statement(p, m)
        bundle = {
            "format": "leash.evidence/1",
            "generated_at": now().isoformat(),
            "statement": statement,
            "statement_ai": ai,
            "mandate": {"id": m.id, "token": m.token, "plain_language": m.plain_language, "policy": m.policy.model_dump(mode="json"), "signer": m.signer},
            "purchase": p.model_dump(mode="json", exclude={"receipt"}),
            "receipt": p.receipt,
            "receipt_payload": jws_peek(p.receipt)[1],
            "chain": [x.receipt_hash for x in sorted(self.store.purchases(m.id), key=lambda x: x.created_at) if x.receipt_hash],
            "jwks": self.jwks(),
            "verify": "Check each JWS with the Ed25519 key whose kid matches its header (RFC 8037 EdDSA). The receipt's mandate.token_hash is SHA-256 of the mandate token.",
        }
        pdf = text_pdf(evidence_lines(p, m, statement), f"Leash evidence {p.id}")
        return bundle, pdf

    async def respond_to_dispute(self, pid: str) -> Purchase:
        p = self.purchase(pid)
        if not p.dispute.get("id"):
            raise LeashError("This purchase has no open dispute.")
        bundle, pdf = await self.evidence(pid)
        evidence = {"evidences": [{"evidence_type": "OTHER", "notes": bundle["statement"][:2000]}]}
        await self.paypal.provide_evidence(p.dispute["id"], evidence, f"leash-receipt-{p.id}.pdf", pdf, f"leash-{p.id}-evidence")
        d = await self.paypal.get_dispute(p.dispute["id"])
        p.dispute.update({"status": d.get("status"), "evidence_submitted": now().isoformat(), "statement_ai": bundle["statement_ai"]})
        self.store.save_purchase(p)
        self.log(p.mandate_id, self.processor, "dispute.evidence", f"Submitted the signed intent receipt as evidence on {p.dispute['id']}", {"purchase": p.id, "status": d.get("status")})
        return p

    # ------------------------------------------------------------------ 6. webhooks and sync

    async def handle_webhook(self, event: dict) -> dict:
        eid = event.get("id", "")
        if eid and not self.store.use_nonce("webhook:" + eid, "webhook"):
            return {"status": "duplicate"}
        etype = event.get("event_type", "")
        res = event.get("resource") or {}
        self.log(None, "paypal:webhook", "webhook.received", f"Webhook {etype}", {"event_id": eid, "resource_id": res.get("id")})
        if etype == "CHECKOUT.ORDER.APPROVED":
            oid = res.get("id")
            for m in self.store.mandates():
                if m.envelope.order_id == oid:
                    await self._authorize_allowance(m.id)
                    return {"status": "processed", "mandate": m.id}
            for p in self.store.purchases():
                if p.payment.get("order_id") == oid and p.status == "awaiting_paypal":
                    await self._authorize_exception(p.id)
                    return {"status": "processed", "purchase": p.id}
        if etype == "PAYMENT.AUTHORIZATION.VOIDED":
            for m in self.store.mandates():
                if m.envelope.authorization_id == res.get("id"):
                    m.envelope.authorization_status = "VOIDED"
                    self.store.save_mandate(m)
        if etype.startswith("CUSTOMER.DISPUTE"):
            await self.sync_disputes()
        return {"status": "processed"}

    async def refresh_allowance(self, mid: str) -> Mandate:
        m = self.mandate(mid)
        if m.status == "awaiting_funding":
            await self._authorize_allowance(mid)
            m = self.mandate(mid)
        if m.envelope.authorization_id:
            a = await self.paypal.get_authorization(m.envelope.authorization_id)
            m.envelope.authorization_status = a.get("status", m.envelope.authorization_status)
            self.store.save_mandate(m)
        return m

    # ------------------------------------------------------------------ 7. views

    def mandate_view(self, m: Mandate) -> dict:
        ps = self.store.purchases(m.id)
        spent = D(sum((p.cart.total - sum((D(r["amount"]) for r in p.refunds), Decimal("0")) for p in ps if p.status in COMMITTED), Decimal("0")))
        data = m.model_dump(mode="json", exclude={"token"})
        data["token_preview"] = (m.token or "")[:48]
        data["signature_ok"], data["signature_detail"] = self.verify_mandate(m) if m.token else (False, "unsigned")
        data["spent"] = fmt(spent)
        data["allowance_left"] = fmt(self.allowance_left(m)) if m.envelope.authorization_id else None
        data["counts"] = {s: len([p for p in ps if p.status == s]) for s in ("captured", "blocked", "awaiting_approval", "rejected", "refunded", "voided", "awaiting_paypal")}
        data["merchants"] = [self.catalog.directory.merchant(x).model_dump() for x in m.policy.merchants_allow if self.catalog.directory.merchant(x)]
        return data

    def purchase_view(self, p: Purchase) -> dict:
        data = p.model_dump(mode="json")
        data["receipt_payload"] = jws_peek(p.receipt)[1] if p.receipt else None
        merchant = self.catalog.directory.merchant(p.cart.merchant_id)
        data["merchant_verified"] = bool(merchant and merchant.verified)
        return data

    def dashboard(self) -> dict:
        self.expire_sweep()
        mandates = self.store.mandates()
        purchases = self.store.purchases(limit=300)
        cutoff = now().timestamp() - 30 * 86400
        recent = [p for p in purchases if p.created_at.timestamp() >= cutoff]
        captured = [p for p in recent if p.status in ("captured", "refunded")]
        return {
            "owner": self.settings.owner_name,
            "frozen": self.frozen,
            "stats": {
                "active_mandates": len([m for m in mandates if m.status == "active"]),
                "spent_30d": fmt(sum((p.cart.total for p in captured), Decimal("0"))),
                "purchases_30d": len(captured),
                "blocked_30d": len([p for p in recent if p.status == "blocked"]),
                "pending": len([p for p in purchases if p.status in ("awaiting_approval", "awaiting_paypal")]),
                "held": fmt(sum((self.allowance_left(m) for m in mandates if m.status == "active"), Decimal("0"))),
                "receipts": len([p for p in purchases if p.receipt]),
                "disputes": len([p for p in purchases if p.dispute.get("id")]),
            },
            "mandates": [self.mandate_view(m) for m in mandates],
            "purchases": [self.purchase_view(p) for p in purchases[:100]],
            "timeline": self.store.entries(limit=80),
        }
