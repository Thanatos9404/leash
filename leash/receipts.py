"""Intent receipts and dispute evidence.

A receipt is a compact JWS signed with Leash's receipt key. It binds the user's signed mandate, the agent's stated
reasoning, the exact cart, the policy decision, the approval and the PayPal ids together, and each receipt carries
the hash of the previous one under the same mandate, so the purchase history of a mandate is tamper-evident.

Two stages per purchase:
  * "authorization": issued before capture, handed to the merchant, verified once (its jti is single use).
  * "settlement": issued after capture with the PayPal capture id; this is the one exported as evidence.
"""

from __future__ import annotations

import textwrap
import time
from typing import Any, Optional

from .crypto import KeyPair, jws_peek, jws_sign, sha256_hex
from .models import Mandate, Purchase

RECEIPT_TYP = "leash-receipt+jws"
AUTH_RECEIPT_TTL = 15 * 60


def cart_hash(cart_dict: dict) -> str:
    """Hash over what the merchant will charge for: merchant, items, totals, nonce."""
    keep = {k: cart_dict[k] for k in ("id", "merchant_id", "merchant_domain", "items", "subtotal", "shipping", "tax", "total", "currency", "nonce")}
    for item in keep["items"]:
        for k in list(item):
            if k not in ("product_id", "variant_id", "name", "quantity", "unit_price", "category", "is_subscription"):
                item.pop(k)
    return sha256_hex(keep)


def build_receipt(stage: str, purchase: Purchase, mandate: Mandate, key: KeyPair, processor: str, prev: Optional[str]) -> str:
    now = int(time.time())
    pay = purchase.payment
    payload: dict[str, Any] = {
        "iss": "leash",
        "typ": f"leash.intent-receipt/{stage}",
        "jti": f"{purchase.id}:{stage}",
        "iat": now,
        "mandate": {
            "id": mandate.id,
            "title": mandate.policy.title,
            "token_hash": sha256_hex((mandate.token or "").encode()),
            "policy_hash": mandate.policy_hash,
            "signer": mandate.signer.get("kid"),
            "signer_type": mandate.signer.get("type"),
        },
        "agent": {
            "id": purchase.agent_id,
            "model": purchase.agent_ai,
            "task": purchase.task[:200],
            "reasoning_hash": sha256_hex(purchase.reasoning.encode()),
            "reasoning": purchase.reasoning[:500],
        },
        "cart": {
            "id": purchase.cart.id,
            "hash": purchase.cart.cart_hash,
            "merchant_id": purchase.cart.merchant_id,
            "merchant_domain": purchase.cart.merchant_domain,
            "total": str(purchase.cart.total),
            "currency": purchase.cart.currency,
            "items": [{"name": i.name, "quantity": i.quantity, "unit_price": str(i.unit_price)} for i in purchase.cart.items],
        },
        "decision": {
            "outcome": purchase.decision.outcome,
            "rules_checked": len(purchase.decision.checks),
            "rules_passed": len([c for c in purchase.decision.checks if c.ok]),
            "summary": purchase.decision.summary,
        },
        "approval": {k: purchase.approval.get(k) for k in ("method", "by", "at", "token_hash") if purchase.approval.get(k)},
        "payment": {
            "processor": processor,
            "mode": pay.get("mode"),
            "order_id": pay.get("order_id"),
            "authorization_id": pay.get("authorization_id"),
            "capture_id": pay.get("capture_id"),
            "amount": str(purchase.cart.total),
        },
        "prev": prev,
    }
    if stage == "authorization":
        payload["exp"] = now + AUTH_RECEIPT_TTL
    return jws_sign(payload, key, RECEIPT_TYP)


def receipt_hash(token: str) -> str:
    return sha256_hex(token.encode())


def evidence_lines(purchase: Purchase, mandate: Mandate, statement: str) -> list[str]:
    _, payload = jws_peek(purchase.receipt or "")
    pay = payload.get("payment", {})
    lines = [
        "LEASH INTENT RECEIPT - DISPUTE EVIDENCE",
        "",
        f"Purchase: {purchase.id}    Status: {purchase.status}",
        f"Merchant: {purchase.cart.merchant_name} ({purchase.cart.merchant_domain})",
        f"Amount: ${purchase.cart.total} {purchase.cart.currency}",
        f"PayPal order: {pay.get('order_id')}   authorization: {pay.get('authorization_id')}   capture: {pay.get('capture_id')}",
        "",
        "Statement",
        *textwrap.wrap(statement, 92),
        "",
        "1. The account holder's signed mandate",
        f"   Mandate {mandate.id} '{mandate.policy.title}', signed by key {mandate.signer.get('kid')} ({mandate.signer.get('type')}).",
        f"   Policy hash {mandate.policy_hash}",
        *[f"   - {line}" for line in mandate.plain_language],
        "",
        "2. What the agent bought and why",
        *[f"   - {i.quantity} x {i.name} at ${i.unit_price}" for i in purchase.cart.items],
        *textwrap.wrap(f"   Agent reasoning: {purchase.reasoning}", 92, subsequent_indent="   "),
        "",
        "3. Policy decision (deterministic)",
        f"   {purchase.decision.summary}",
        *[f"   [{'pass' if c.ok else 'FAIL'}] {c.rule}: {c.detail}" for c in purchase.decision.checks],
        "",
        "4. Approval and merchant verification",
        f"   Approval: {purchase.approval.get('method')} by {purchase.approval.get('by')} at {purchase.approval.get('at')}",
        f"   Merchant verified the agent's authority: {purchase.verification.get('ok')} at {purchase.verification.get('at')}",
        "",
        "5. Signed receipt (verify with the public key at /.well-known/jwks.json)",
        f"   Receipt hash: {purchase.receipt_hash}",
        f"   Previous receipt in this mandate's chain: {payload.get('prev')}",
        *textwrap.wrap(purchase.receipt or "", 92),
    ]
    return lines


def text_pdf(lines: list[str], title: str = "Leash evidence") -> bytes:
    """A small, dependency-free PDF writer (Helvetica, A4, wraps onto new pages)."""
    def esc(s: str) -> str:
        s = s.encode("latin-1", "replace").decode("latin-1")
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    per_page = 60
    pages = [lines[i:i + per_page] for i in range(0, max(len(lines), 1), per_page)]
    objs: list[bytes] = []
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    font_obj = 3 + len(pages) * 2
    for i, chunk in enumerate(pages):
        stream_lines = ["BT", "/F1 9 Tf", "11 TL", "50 800 Td"]
        for j, line in enumerate(chunk):
            if j == 0 and i == 0:
                stream_lines += ["/F1 13 Tf", f"({esc(line)}) Tj", "/F1 9 Tf", "T*"]
            else:
                stream_lines.append(f"({esc(line)}) Tj T*")
        stream_lines.append("ET")
        stream = "\n".join(stream_lines).encode("latin-1")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 {font_obj} 0 R >> >> /Contents {4 + i * 2} 0 R >>".encode())
        objs.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info << /Title ({esc(title)}) >> >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
