"""Turn a buyer's plain-language brief into a structured, signed spending mandate.

The model drafts; deterministic code validates; the human edits and signs. Numbers the model returns for
quantities, budget and limits must also appear in the brief, otherwise the rules-based value wins and a
warning is shown (a cheap guard against invented numbers).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import timedelta
from decimal import Decimal
from typing import Any, Optional

from .catalog import Catalog
from .llm import LLM
from .models import Mandate, MandateItem, now
from .money import D, pct

SAMPLE_BRIEF = (
    "I'm Anita Shah and I run Green Basket Grocers in Edison, New Jersey. For our Diwali shelf I need 150 kg pearl millet flour, "
    "60 kg turmeric powder and 20 kg green cardamom. Total budget $1,150. Organic only, max 3 suppliers, "
    "must ship within 10 days. Pay at most 30% advance and never more than $30/kg for cardamom. "
    "Auto-release payments under $150 when dispatch is verified; ask me for anything bigger."
)

KNOWN_CERTS = {"organic", "gi_tag", "fair_trade"}
_NUM = r"(\d[\d,]*(?:\.\d+)?)"


def _num(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def _numbers_in(text: str) -> set[Decimal]:
    return {_num(m) for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def parse_rules(brief: str, catalog: Catalog) -> dict[str, Any]:
    """Deterministic extraction. Good enough for structured briefs; the LLM handles messier ones."""
    text = " ".join(brief.split())
    low = text.lower()
    out: dict[str, Any] = {"items": [], "required_certs": []}

    m = re.search(r"\b(?:I run|I own|I manage|we run|we own)\s+([A-Z][\w&'.\- ]+?)(?: in |,|\.|;)", text)
    if m:
        out["buyer_business"] = m.group(1).strip()
    m = re.search(r"\b(?:I'm|I am|my name is|this is)\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\b", text)
    if m:
        out["buyer_name"] = m.group(1).strip()

    seen: set[str] = set()
    for m in re.finditer(
        _NUM + r"\s*(?:kg|kgs|kilo|kilos|kilograms)\b\s*(?:of\s+)?([^,.;]+?)(?=,|\.|;| and |$)", text, re.I
    ):
        key = catalog.match_product(m.group(2))
        if key and key not in seen:
            seen.add(key)
            out["items"].append({"product": key, "qty_kg": int(_num(m.group(1)))})

    m = re.search(r"budget[^$\d]{0,20}\$?\s*" + _NUM, low) or re.search(
        r"\$\s*" + _NUM + r"\s*(?:total|budget|all[- ]in)", low
    )
    if m:
        out["budget_total"] = str(_num(m.group(1)))

    m = re.search(
        r"(?:max(?:imum)?|at most|no more than|up to)\s*(\d+)\s*(?:suppliers|sellers|vendors|fpos|producers)", low
    )
    if m:
        out["max_sellers"] = int(m.group(1))

    m = re.search(
        r"(?:max(?:imum)?|at most|no more than|up to)\s*\$\s*" + _NUM + r"\s*(?:per|from any one|with any one)\s*"
        r"(?:supplier|seller|fpo|producer)",
        low,
    )
    if m:
        out["max_per_seller"] = str(_num(m.group(1)))

    if "organic" in low:
        out["required_certs"].append("organic")
    if re.search(r"\bgi[- ]?(?:tag|tagged)\b", low):
        out["required_certs"].append("gi_tag")
    if "fair trade" in low or "fair-trade" in low:
        out["required_certs"].append("fair_trade")

    m = re.search(r"(?:within|in under|in less than)\s*(\d+)\s*days", low)
    if m:
        out["max_lead_time_days"] = int(m.group(1))

    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:advance|upfront|up front|deposit)", low) or re.search(
        r"(?:advance|upfront|deposit)[^\d%]{0,20}(\d+(?:\.\d+)?)\s*%", low
    )
    if m:
        out["advance_pct"] = str(pct(m.group(1) + "%"))
    elif re.search(r"pay (?:in )?full (?:upfront|up front)|100% upfront", low):
        out["advance_pct"] = "1"

    caps: dict[str, str] = {}
    for m in re.finditer(
        r"(?:never more than|no more than|max(?:imum)?|at most|up to|ceiling of)\s*\$\s*" + _NUM
        + r"\s*(?:/|per)\s*(?:kg|kilo)\s*(?:for|on)\s+([a-z ()-]+)",
        low,
    ):
        key = catalog.match_product(m.group(2))
        if key:
            caps[key] = str(_num(m.group(1)))
    for item in out["items"]:
        if item["product"] in caps:
            item["max_unit_price"] = caps[item["product"]]

    m = re.search(r"auto[- ]?(?:release|approve|pay)[^$]{0,40}\$\s*" + _NUM, low)
    if m:
        out["auto_release_limit"] = str(_num(m.group(1)))

    m = re.search(r"(?:expires?|valid) (?:in|for) (\d+) days", low)
    if m:
        out["expires_in_days"] = int(m.group(1))
    return out


MANDATE_SYSTEM = """You turn a small business owner's purchasing brief into a JSON spending mandate for Sauda,
a marketplace where an AI buying agent negotiates with farmer producer organisations (FPOs) in India and pays
through PayPal. Only use facts stated in the brief. Use null for anything not stated. Never invent numbers.

Product keys you may use (map the buyer's words to these):
{products}

Certifications you may use: organic, gi_tag, fair_trade.

Return exactly this JSON shape:
{{"buyer_name": str|null  (the person's name only), "buyer_business": str|null  (business name only, no location),
  "items": [{{"product": key, "qty_kg": int, "max_unit_price": number|null}}],
  "unknown_products": [str],
  "budget_total": number|null, "max_sellers": int|null, "max_per_seller": number|null,
  "required_certs": [str], "max_lead_time_days": int|null,
  "advance_pct": number|null  (fraction, e.g. 0.3 for 30%),
  "auto_release_limit": number|null  (USD; releases at or under this amount need no extra approval),
  "expires_in_days": int|null,
  "notes": str  (one sentence on anything the mandate cannot express)}}"""


async def draft_mandate(brief: str, catalog: Catalog, llm: LLM, buyer_email: str = "") -> tuple[Mandate, str, list[str]]:
    """Return (mandate, ai_label, warnings)."""
    rules = parse_rules(brief, catalog)
    warnings: list[str] = []
    ai = "rules"
    data: dict[str, Any] = dict(rules)

    products = "\n".join(f"- {p.key}: {p.name} (also: {', '.join(p.synonyms[:4])})" for p in catalog.products.values())
    reply = await llm.json(
        MANDATE_SYSTEM.format(products=products),
        f"Brief:\n{brief}",
        purpose="mandate",
        max_tokens=600,
        temperature=0.1,
    )
    if reply:
        ai = llm.label()
        data = _merge(rules, reply, brief, catalog, warnings)

    mandate = _build(data, buyer_email, catalog, warnings)
    return mandate, ai, warnings


def _merge(rules: dict, reply: dict, brief: str, catalog: Catalog, warnings: list[str]) -> dict:
    """Prefer the model's reading, but only keep numbers that are present in the brief."""
    nums = _numbers_in(brief)
    out = dict(rules)

    def grounded(value) -> bool:
        try:
            v = Decimal(str(value))
        except Exception:
            return False
        return v in nums or (v * 100) in nums  # 0.3 is grounded by "30%"

    items = []
    for it in reply.get("items") or []:
        key = it.get("product")
        if key not in catalog.products:
            warnings.append(f"Model returned unknown product {key!r}; ignored.")
            continue
        qty = it.get("qty_kg")
        if qty is None or not grounded(qty):
            fallback = next((r for r in rules["items"] if r["product"] == key), None)
            if fallback:
                qty = fallback["qty_kg"]
            else:
                warnings.append(f"Quantity for {catalog.product_name(key)} is not in the brief; skipped.")
                continue
        item = {"product": key, "qty_kg": int(Decimal(str(qty)))}
        cap = it.get("max_unit_price")
        if cap is not None and grounded(cap):
            item["max_unit_price"] = str(cap)
        else:
            rule_item = next((r for r in rules["items"] if r["product"] == key), {})
            if rule_item.get("max_unit_price"):
                item["max_unit_price"] = rule_item["max_unit_price"]
        items.append(item)
    if items:
        out["items"] = items
    for unk in reply.get("unknown_products") or []:
        warnings.append(f"No seller in the catalog offers '{unk}'.")

    for key in ("budget_total", "max_per_seller", "auto_release_limit"):
        val = reply.get(key)
        if val is None:
            continue
        if grounded(val):
            out[key] = str(val)
        elif key in rules:
            warnings.append(f"Model's {key} ({val}) is not in the brief; kept {rules[key]} from the text.")
        else:
            warnings.append(f"Model's {key} ({val}) is not in the brief; ignored.")
    for key in ("max_sellers", "max_lead_time_days", "expires_in_days"):
        val = reply.get(key)
        if isinstance(val, (int, float)) and grounded(val):
            out[key] = int(val)
    adv = reply.get("advance_pct")
    if adv is not None and grounded(adv if Decimal(str(adv)) > 1 else Decimal(str(adv)) * 100):
        out["advance_pct"] = str(pct(adv))
    certs = [c for c in (reply.get("required_certs") or []) if c in KNOWN_CERTS]
    if certs:
        out["required_certs"] = sorted(set(certs) | set(rules.get("required_certs", [])))
    for key in ("buyer_name", "buyer_business", "notes"):
        # Names matched by the deterministic parser win; the model fills gaps.
        if reply.get(key) and not rules.get(key):
            out[key] = str(reply[key])[:200]
    return out


def _build(data: dict, buyer_email: str, catalog: Catalog, warnings: list[str]) -> Mandate:
    items = [MandateItem(**it) for it in data.get("items", [])]
    if not items:
        warnings.append("No products recognised. Add at least one item before signing.")
    budget = data.get("budget_total")
    if budget is None:
        est = Decimal("0")
        for it in items:
            prices = [s.listing(it.product).list_price for s in catalog.sellers_for(it.product)]
            if prices:
                est += min(prices) * it.qty_kg
        budget = D(est)
        warnings.append(f"No budget stated. Defaulted to the list-price estimate ${budget}; edit before signing.")
    advance = pct(data.get("advance_pct", "0.30"))
    if advance < Decimal("0.10") or advance > 1:
        warnings.append("Advance must be between 10% and 100%; set to 30%.")
        advance = Decimal("0.3000")
    return Mandate(
        buyer_name=data.get("buyer_name") or data.get("buyer_business") or "Buyer",
        buyer_business=data.get("buyer_business") or "",
        buyer_email=buyer_email,
        items=items,
        budget_total=D(budget),
        max_sellers=int(data.get("max_sellers") or 3),
        max_per_seller=D(data["max_per_seller"]) if data.get("max_per_seller") else None,
        required_certs=sorted(set(data.get("required_certs") or [])),
        max_lead_time_days=data.get("max_lead_time_days"),
        advance_pct=advance,
        auto_release_limit=D(data.get("auto_release_limit") or "100"),
        expires_at=now() + timedelta(days=int(data.get("expires_in_days") or 14)),
        notes=data.get("notes") or "",
    )


# --------------------------------------------------------------------------- signing


def _canonical(m: Mandate) -> bytes:
    return json.dumps(m.signable(), sort_keys=True, separators=(",", ":"), default=str).encode()


def sign(m: Mandate, secret: str, signer: str) -> Mandate:
    m = m.model_copy(deep=True)
    m.signed_by = signer
    m.signed_at = now()
    m.signature = hmac.new(secret.encode(), _canonical(m), hashlib.sha256).hexdigest()
    return m


def verify(m: Mandate, secret: str) -> bool:
    if not m.signature or not m.signed_at:
        return False
    expected = hmac.new(secret.encode(), _canonical(m), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, m.signature)


def apply_edits(m: Mandate, edits: Optional[dict], catalog: Catalog) -> Mandate:
    """Apply the buyer's edits from the review form. Unknown products are rejected."""
    if not edits:
        return m
    data = m.model_dump()
    for key in ("buyer_name", "buyer_business", "buyer_email", "notes"):
        if key in edits and edits[key] is not None:
            data[key] = str(edits[key])[:200]
    for key in ("budget_total", "max_per_seller", "auto_release_limit"):
        if key in edits:
            data[key] = D(edits[key]) if edits[key] not in (None, "") else (None if key == "max_per_seller" else data[key])
    for key in ("max_sellers", "max_lead_time_days"):
        if key in edits and edits[key] not in (None, ""):
            data[key] = int(edits[key])
    if "advance_pct" in edits and edits["advance_pct"] not in (None, ""):
        data["advance_pct"] = pct(edits["advance_pct"])
    if "required_certs" in edits and edits["required_certs"] is not None:
        data["required_certs"] = sorted({c for c in edits["required_certs"] if c in KNOWN_CERTS})
    if "items" in edits and edits["items"] is not None:
        items = []
        for it in edits["items"]:
            if it.get("product") not in catalog.products:
                raise ValueError(f"unknown product {it.get('product')!r}")
            cap = it.get("max_unit_price")
            items.append(
                {"product": it["product"], "qty_kg": int(it["qty_kg"]), "max_unit_price": D(cap) if cap not in (None, "") else None}
            )
        data["items"] = items
    data["signature"] = None
    data["signed_at"] = None
    data["signed_by"] = None
    out = Mandate(**data)
    if out.advance_pct < Decimal("0.10") or out.advance_pct > 1:
        raise ValueError("advance_pct must be between 0.10 and 1.00")
    if out.budget_total <= 0:
        raise ValueError("budget must be positive")
    return out
