"""Mandate compiler: plain language in, typed deterministic policy out.

Pipeline:
  1. A deterministic parser reads the obvious structure (amounts, periods, merchants, "never ...").
  2. The model reads the same text and proposes a policy as JSON.
  3. Code merges them: every number the model returns must appear in the user's text, every merchant must
     resolve to a verified merchant id, and anything ungrounded is dropped with a visible warning.
  4. The policy is rendered back to plain language by deterministic code, not by the model, so the sentence
     the user confirms is exactly the rule that will be enforced.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from .catalog import Directory, normalise_name
from .crypto import sha256_hex
from .llm import LLM
from .models import Budget, CountLimit, Policy, TimeWindow, now
from .money import D

SAMPLES = {
    "coffee": (
        "Keep my coffee stocked. Up to $80 a month, max $30 per order, only from Bean & Bloom, Daily Grind or "
        "Northside Pantry. Never subscriptions. Ask me before anything over $25. At most 4 orders a month."
    ),
    "gift": (
        "Buy a birthday present for my 9-year-old niece from Kite & Kin Toys. Spend no more than $60 in total "
        "and ask me before every purchase. Valid for 7 days."
    ),
    "office": (
        "Office supplies for the team: $200 a month from any verified store, no electronics or gift cards, "
        "weekdays only, ask me above $50."
    ),
}

CATEGORY_WORDS = {
    "coffee": "coffee", "espresso": "coffee", "beans": "coffee", "tea": "tea",
    "groceries": "groceries", "grocery": "groceries", "pantry": "groceries", "snacks": "groceries",
    "office supplies": "office_supplies", "stationery": "office_supplies", "office": "office_supplies",
    "toys": "toys", "toy": "toys", "gift for": "toys", "present for": "toys",
    "electronics": "electronics", "gadgets": "electronics",
    "gift cards": "gift_cards", "gift card": "gift_cards", "prepaid cards": "gift_cards",
    "household": "household", "alcohol": "alcohol", "wine": "alcohol",
}
KNOWN_CATEGORIES = sorted(set(CATEGORY_WORDS.values()))
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MAX_VALIDITY_DAYS = 28  # a PayPal authorization holds funds for up to 29 days
_N = r"\$\s?(\d[\d,]*(?:\.\d+)?)"


def _num(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def _numbers_in(text: str) -> set[Decimal]:
    return {_num(m) for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def _period(word: str) -> str:
    w = word.lower()
    return {"daily": "day", "day": "day", "weekly": "week", "week": "week", "monthly": "month", "month": "month"}.get(w, "month")


def parse_rules(text: str, directory: Directory) -> dict[str, Any]:
    t = " ".join(text.split())
    low = t.lower()
    out: dict[str, Any] = {"merchants": [], "unknown_merchants": [], "categories_block": [], "categories_allow": []}

    m = re.search(_N + r"\s*(?:a|per|each|every|/)\s*(day|week|month)", low) or re.search(
        r"(day|week|month)ly (?:budget|limit|cap) (?:of )?" + _N, low)
    if m:
        groups = m.groups()
        amt, per = (groups[0], groups[1]) if groups[0][0].isdigit() else (groups[1], groups[0])
        out["budget"] = {"amount": str(_num(amt)), "period": _period(per)}
    else:
        m = re.search(_N + r"\s*(?:in total|total|overall|all[- ]in)", low) or re.search(r"(?:total|overall) (?:of |budget of )?" + _N, low)
        if m:
            out["budget"] = {"amount": str(_num(m.group(1))), "period": "total"}

    m = re.search(r"(?:max(?:imum)?|at most|no more than|up to|cap of)\s*" + _N + r"\s*(?:per|an|each|a|for any)\s*(?:order|purchase|item|transaction|basket)", low)
    if m:
        out["per_purchase_max"] = str(_num(m.group(1)))

    if re.search(r"ask (?:me )?(?:before|about|for) (?:every|each|any) (?:purchase|order)", low):
        out["ask_above"] = "0"
    else:
        m = re.search(r"ask (?:me )?(?:before |first )?(?:for |about )?(?:anything|any purchase|purchases|orders|spending)?\s*(?:over|above|more than|exceeding)\s*" + _N, low)
        if m:
            out["ask_above"] = str(_num(m.group(1)))

    m = re.search(r"at most (\d+) (?:orders|purchases)\s*(?:a|per|each) (day|week|month)", low)
    if m:
        out["max_purchases"] = {"count": int(m.group(1)), "period": _period(m.group(2))}
    m = re.search(r"(?:one|1) (?:order|purchase) (?:every|per) (\d+) ?(minutes|minute|hours|hour|days|day)", low)
    if m:
        mult = {"minute": 1, "hour": 60, "day": 1440}[m.group(2).rstrip("s")]
        out["min_interval_minutes"] = int(m.group(1)) * mult

    m = re.search(r"\bonly (?:from|at|buy from|shop at)\s+([^.;]+)", t, re.I) or re.search(r"\bfrom ([A-Z][^.;]*?(?:Toys|Coffee|Roasters|Pantry|Co\.?|Store|Shop|Electronics))\b", t)
    if m and "any" not in m.group(1).lower()[:8]:
        for part in re.split(r",|\bor\b|\band\b", m.group(1)):
            part = part.strip(" .")
            if not part:
                continue
            found = directory.match_merchant(part)
            if found and found.id not in out["merchants"]:
                out["merchants"].append(found.id)
            elif not found:
                out["unknown_merchants"].append(part)

    if re.search(r"\b(?:never|no)\s+(?:subscriptions?|recurring)", low):
        out["allow_subscriptions"] = False
    elif re.search(r"subscriptions? (?:are )?(?:ok|fine|allowed)", low):
        out["allow_subscriptions"] = True

    for m in re.finditer(r"\b(?:no|never(?: buy)?|not)\s+([a-z ,]+?)(?=\.|;|$| weekdays| ask)", low):
        for chunk in re.split(r",|\bor\b|\band\b", m.group(1)):
            cat = CATEGORY_WORDS.get(chunk.strip())
            if cat and cat not in out["categories_block"]:
                out["categories_block"].append(cat)
    head = low.split(".")[0]
    for word, cat in CATEGORY_WORDS.items():
        if re.search(r"\b" + re.escape(word) + r"\b", head) and cat not in out["categories_block"] and cat not in out["categories_allow"] and cat != "gift_cards":
            out["categories_allow"].append(cat)

    m = re.search(r"(?:max(?:imum)?|at most|no more than) (\d+) (?:units|items|bags|of any item|of each)", low)
    if m:
        out["max_quantity"] = int(m.group(1))

    if "weekdays only" in low or "only on weekdays" in low or "on weekdays" in low:
        out["time_window"] = {"days": [0, 1, 2, 3, 4], "start_hour": 0, "end_hour": 24}
    m = re.search(r"between (\d{1,2}) ?(am|pm)? and (\d{1,2}) ?(am|pm)", low)
    if m:
        sh = int(m.group(1)) % 12 + (12 if m.group(2) == "pm" else 0)
        eh = int(m.group(3)) % 12 + (12 if m.group(4) == "pm" else 0)
        tw = out.get("time_window") or {"days": list(range(7))}
        tw.update({"start_hour": sh, "end_hour": max(eh, sh + 1)})
        out["time_window"] = tw

    m = re.search(r"valid (?:for|over) (?:the next )?(\d+) (days|day|weeks|week)", low)
    if m:
        out["valid_days"] = int(m.group(1)) * (7 if m.group(2).startswith("week") else 1)
    m = re.search(r"(?:valid )?until (" + "|".join(WEEKDAYS) + r")", low)
    if m:
        today = datetime.now(timezone.utc)
        delta = (WEEKDAYS.index(m.group(1)) - today.weekday()) % 7 or 7
        out["valid_days"] = delta
    out["title"] = _title(t, out)
    out["purpose"] = t.split(".")[0][:160]
    return out


def _title(text: str, rules: dict) -> str:
    low = text.lower()
    if "coffee" in low:
        return "Coffee restock"
    if "office" in low:
        return "Office supplies"
    if "gift" in low or "present" in low or "birthday" in low:
        return "Birthday gift"
    words = re.findall(r"[A-Za-z]+", text)[:4]
    return " ".join(words).capitalize() or "Shopping mandate"


SYSTEM = """You compile a person's instructions for their AI shopping agent into a JSON spending policy.
Use only facts in the text. Use null for anything not stated. Never invent amounts or merchants.

Known merchants (name -> id): {merchants}
Known categories: {categories}

Return exactly:
{{"title": str (2-4 words), "purpose": str (what the agent may buy, one short sentence),
  "per_purchase_max": number|null, "ask_above": number|null (0 means ask before every purchase),
  "budget": {{"amount": number, "period": "day"|"week"|"month"|"total"}}|null,
  "max_purchases": {{"count": int, "period": "day"|"week"|"month"}}|null,
  "min_interval_minutes": int|null,
  "merchants": [merchant ids], "unknown_merchants": [names the text mentions that are not in the list],
  "categories_allow": [categories]|[], "categories_block": [categories]|[],
  "allow_subscriptions": bool|null, "max_quantity": int|null,
  "weekdays_only": bool|null, "valid_days": int|null}}"""


async def compile_mandate(text: str, directory: Directory, llm: LLM) -> tuple[Policy, dict]:
    """Return (policy, report). The report lists the AI label, warnings, and what was grounded."""
    text = text.strip()[:2000]
    rules = parse_rules(text, directory)
    warnings: list[str] = []
    ai = "rules"
    data = dict(rules)
    merchants = ", ".join(f"{m.name} ({m.domain}) -> {m.id}" for m in directory.merchants.values() if m.verified)
    reply = await llm.json(SYSTEM.format(merchants=merchants, categories=", ".join(KNOWN_CATEGORIES)), text, purpose="compile", max_tokens=600, temperature=0.1)
    if reply:
        ai = llm.label()
        data = _merge(rules, reply, text, directory, warnings)
    policy = _build(data, warnings)
    report = {
        "ai": ai,
        "warnings": warnings,
        "unknown_merchants": data.get("unknown_merchants", []),
        "policy_hash": sha256_hex(policy.model_dump(mode="json")),
    }
    return policy, report


def _merge(rules: dict, reply: dict, text: str, directory: Directory, warnings: list[str]) -> dict:
    nums = _numbers_in(text)
    out = dict(rules)

    def grounded(v: Any) -> bool:
        try:
            return Decimal(str(v)) in nums
        except Exception:
            return False

    # Numbers the parser already assigned to a role. The model may fill an empty field, but not by re-using a
    # number the text gives a different meaning ("ask me above $50" is not a $50 cap).
    claimed = {Decimal(str(rules[k])): k for k in ("per_purchase_max", "ask_above") if rules.get(k) is not None}
    if rules.get("budget"):
        claimed[Decimal(rules["budget"]["amount"])] = "budget"
    for key in ("per_purchase_max", "ask_above"):
        v = reply.get(key)
        if v is None:
            continue
        try:
            dv = Decimal(str(v))
        except Exception:
            continue
        if key not in rules and dv in claimed and claimed[dv] != key:
            warnings.append(f"The model read ${v} as the {key.replace('_', ' ')}, but your text uses ${v} for the {claimed[dv].replace('_', ' ')}; not added.")
            continue
        if grounded(v) or (key == "ask_above" and dv == 0 and rules.get("ask_above") == "0"):
            out[key] = str(v)
        elif key in rules:
            warnings.append(f"The model read {key.replace('_', ' ')} as ${v}, which is not in your text; kept ${rules[key]}.")
        else:
            warnings.append(f"The model suggested {key.replace('_', ' ')} ${v}, which is not in your text; ignored.")
    b = reply.get("budget")
    if isinstance(b, dict) and b.get("amount") is not None:
        if grounded(b["amount"]) and b.get("period") in ("day", "week", "month", "total"):
            out["budget"] = {"amount": str(b["amount"]), "period": b["period"]}
        else:
            warnings.append(f"The model read a budget of ${b.get('amount')} that is not in your text; ignored.")
    mp = reply.get("max_purchases")
    if isinstance(mp, dict) and mp.get("count") and grounded(mp["count"]) and mp.get("period") in ("day", "week", "month"):
        out["max_purchases"] = {"count": int(mp["count"]), "period": mp["period"]}
    v = reply.get("min_interval_minutes")
    if isinstance(v, int) and v > 0 and rules.get("min_interval_minutes"):
        out["min_interval_minutes"] = rules["min_interval_minutes"]  # unit conversion is the parser's job
    ids = []
    folded = normalise_name(text)
    for mid in reply.get("merchants") or []:
        m = directory.merchant(str(mid))
        if not (m and m.verified):
            if mid:
                warnings.append(f"Ignored merchant {mid!r}: not a verified merchant id.")
            continue
        # The model may only add a store the user actually named (by name or domain).
        named = normalise_name(m.name) in folded or normalise_name(m.name.rsplit(" ", 1)[0]) in folded or m.domain in text.lower()
        if not named:
            warnings.append(f"Ignored {m.name}: the model added it, but you did not name it.")
        elif m.id not in ids:
            ids.append(m.id)
    if ids or not rules["merchants"]:
        out["merchants"] = sorted(set(ids) | set(rules["merchants"]))
    out["unknown_merchants"] = sorted(set(rules["unknown_merchants"]) | {str(x) for x in reply.get("unknown_merchants") or []})
    for key in ("categories_allow", "categories_block"):
        cats = [c for c in reply.get(key) or [] if c in KNOWN_CATEGORIES]
        out[key] = sorted(set(cats) | set(rules.get(key, [])))
    if reply.get("allow_subscriptions") is not None and "allow_subscriptions" not in rules:
        out["allow_subscriptions"] = bool(reply["allow_subscriptions"]) and bool(re.search(r"subscription", text, re.I))
    q = reply.get("max_quantity")
    if isinstance(q, int) and grounded(q):
        out["max_quantity"] = q
    if reply.get("weekdays_only") and "time_window" not in rules and "weekday" in text.lower():
        out["time_window"] = {"days": [0, 1, 2, 3, 4], "start_hour": 0, "end_hour": 24}
    vd = reply.get("valid_days")
    if isinstance(vd, int) and vd > 0 and "valid_days" not in rules and grounded(vd):
        out["valid_days"] = vd
    for key in ("title", "purpose"):
        if reply.get(key):
            out[key] = str(reply[key])[:120 if key == "title" else 200]
    return out


def _build(data: dict, warnings: list[str]) -> Policy:
    for name in data.get("unknown_merchants", []):
        warnings.append(f"'{name}' is not a verified merchant, so it is not on the allow list.")
    valid_days = int(data.get("valid_days") or MAX_VALIDITY_DAYS)
    if valid_days > MAX_VALIDITY_DAYS:
        warnings.append(f"Validity capped at {MAX_VALIDITY_DAYS} days, the life of a PayPal authorization. Renew it monthly.")
        valid_days = MAX_VALIDITY_DAYS
    budget = Budget(amount=D(data["budget"]["amount"]), period=data["budget"]["period"]) if data.get("budget") else None
    per = D(data["per_purchase_max"]) if data.get("per_purchase_max") else None
    if budget is None and per is None:
        warnings.append("No spending limit found. Add a budget before signing: Leash will not fund an open-ended mandate.")
    ask = D(data["ask_above"]) if data.get("ask_above") is not None else None
    if ask is not None and per is not None and ask > per:
        warnings.append("Your ask-above amount is higher than the per-purchase cap, so it never applies.")
    blocks = sorted(set(data.get("categories_block") or []) | {"gift_cards"})
    allow = [c for c in data.get("categories_allow") or [] if c not in blocks]
    start = now()
    tw = TimeWindow(**data["time_window"]) if data.get("time_window") else None
    return Policy(
        title=data.get("title") or "Shopping mandate",
        purpose=data.get("purpose") or "",
        per_purchase_max=per,
        ask_above=ask,
        budget=budget,
        max_purchases=CountLimit(**data["max_purchases"]) if data.get("max_purchases") else None,
        min_interval_minutes=data.get("min_interval_minutes"),
        merchants_allow=list(data.get("merchants") or []),
        categories_allow=allow,
        categories_block=blocks,
        max_quantity=data.get("max_quantity"),
        allow_subscriptions=bool(data.get("allow_subscriptions", False)),
        valid_from=start,
        valid_until=start + timedelta(days=valid_days),
        time_window=tw,
    )


def envelope_amount(p: Policy) -> Optional[Decimal]:
    """How much the PayPal allowance authorization holds for this mandate."""
    if p.budget:
        return p.budget.amount
    return p.per_purchase_max


def render(p: Policy, directory: Directory) -> list[str]:
    """Deterministic plain-language rendering. This, not model prose, is what the user signs."""
    lines: list[str] = []
    if p.per_purchase_max is not None:
        lines.append(f"Never spends more than ${p.per_purchase_max} on a single purchase.")
    if p.ask_above is not None:
        lines.append("Asks you to approve every purchase." if p.ask_above == 0 else f"Asks you first before any purchase over ${p.ask_above}.")
    if p.budget:
        per = {"day": "per day", "week": "per week", "month": "per month", "total": "in total"}[p.budget.period]
        lines.append(f"Spends at most ${p.budget.amount} {per}.")
    if p.max_purchases:
        lines.append(f"Asks you before placing more than {p.max_purchases.count} orders per {p.max_purchases.period}.")
    if p.min_interval_minutes:
        lines.append(f"Waits at least {p.min_interval_minutes} minutes between purchases.")
    if p.merchants_allow:
        names = [directory.merchant(m).name + f" ({directory.merchant(m).domain})" for m in p.merchants_allow if directory.merchant(m)]
        tail = "Every other store is blocked." if p.unknown_merchant == "deny" else "Other stores need your approval."
        lines.append("Buys only from " + ", ".join(names) + ". " + tail)
    else:
        lines.append("Buys from any verified merchant; unverified or lookalike stores are blocked.")
    if p.categories_allow:
        lines.append("Only buys: " + ", ".join(c.replace("_", " ") for c in p.categories_allow) + ".")
    if p.categories_block:
        lines.append("Never buys: " + ", ".join(c.replace("_", " ") for c in p.categories_block) + ".")
    if not p.allow_subscriptions:
        lines.append("Never signs you up for subscriptions or recurring charges.")
    if p.max_quantity:
        lines.append(f"At most {p.max_quantity} of any item per order.")
    if p.time_window:
        days = "weekdays" if p.time_window.days == [0, 1, 2, 3, 4] else ", ".join(WEEKDAYS[d].title()[:3] for d in p.time_window.days)
        hours = "" if (p.time_window.start_hour, p.time_window.end_hour) == (0, 24) else f", {p.time_window.start_hour:02d}:00 to {p.time_window.end_hour:02d}:00"
        lines.append(f"Only shops on {days}{hours}.")
    lines.append(f"Valid until {p.valid_until:%d %b %Y}.")
    env = envelope_amount(p)
    if env is not None:
        lines.append(f"Paid from a ${env} PayPal allowance you approve once. Leash can never capture more than that.")
    return lines
