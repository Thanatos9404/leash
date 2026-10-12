"""Product discovery: a fixture catalog of demo storefronts, or Channel3's product search API when a key is set.

Merchant identity is the merchant *id* (fixture) or offer *domain* (Channel3). Display names are never used
for authorisation, because anyone can call their shop "Bean & Bloom Coffee".
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Optional

import httpx

from .config import ROOT
from .models import Merchant, Product
from .money import D

CATALOG_PATH = ROOT / "data" / "catalog.json"
CHANNEL3_SEARCH = "https://api.trychannel3.com/v1/search"


def normalise_name(name: str) -> str:
    """Fold lookalike characters so 'Bean & B1oom' and 'Bean & Bl00m' compare equal to 'Bean & Bloom'."""
    table = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s", "@": "a", "|": "l"})
    return re.sub(r"[^a-z]", "", name.lower().translate(table))


class Directory:
    """Verified merchants plus the fixture products."""

    def __init__(self, merchants: list[Merchant], products: list[Product]) -> None:
        self.merchants = {m.id: m for m in merchants}
        self.products = {p.id: p for p in products}

    @classmethod
    def load(cls, path: Path = CATALOG_PATH) -> "Directory":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls([Merchant(**m) for m in raw["merchants"]], [Product(**p) for p in raw["products"]])

    def merchant(self, mid: str) -> Optional[Merchant]:
        return self.merchants.get(mid)

    def add_merchant(self, m: Merchant) -> None:
        self.merchants.setdefault(m.id, m)

    def add_product(self, p: Product) -> None:
        self.products[p.id] = p

    def match_merchant(self, text: str) -> Optional[Merchant]:
        """Map a name or domain the user typed to a verified merchant."""
        t = normalise_name(text)
        dom = text.strip().lower()
        best = None
        for m in self.merchants.values():
            if not m.verified:
                continue
            if dom and (dom == m.domain or dom.endswith("." + m.domain)):
                return m
            n = normalise_name(m.name)
            if t and (t == n or (len(t) >= 5 and (t in n or n in t))):
                if best is None or len(n) < len(normalise_name(best.name)):
                    best = m
        return best

    def lookalikes(self, merchant: Merchant) -> list[Merchant]:
        """Verified merchants whose normalised name matches this one but whose identity differs."""
        n = normalise_name(merchant.name)
        return [m for m in self.merchants.values() if m.id != merchant.id and m.verified and normalise_name(m.name) == n]

    def search_fixture(self, query: str, max_price: Optional[Decimal] = None, limit: int = 12) -> list[Product]:
        words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2]
        scored = []
        for p in self.products.values():
            if not p.in_stock or (max_price is not None and p.price > max_price):
                continue
            hay = f"{p.title} {p.description} {p.category} {p.brand}".lower()
            score = sum(1 for w in words if w in hay)
            if score or not words:
                scored.append((score, p))
        scored.sort(key=lambda x: (-x[0], x[1].price))
        return [p for _, p in scored[:limit]]


class Channel3:
    """Channel3 product search (POST /v1/search, x-api-key). Offers become products; offer domains become merchants."""

    def __init__(self, api_key: str, directory: Directory, transport: Optional[httpx.BaseTransport] = None) -> None:
        self.api_key = api_key
        self.directory = directory
        self._http = httpx.Client(timeout=20.0, transport=transport)

    def search(self, query: str, max_price: Optional[Decimal] = None, limit: int = 10) -> list[Product]:
        body: dict = {"query": query[:200], "limit": limit, "filters": {"availability": ["InStock"]}}
        if max_price is not None:
            body["filters"]["price"] = {"max_price": float(max_price)}
        r = self._http.post(CHANNEL3_SEARCH, json=body, headers={"x-api-key": self.api_key, "Content-Type": "application/json"})
        r.raise_for_status()
        out: list[Product] = []
        for item in r.json().get("products", []):
            for offer in item.get("offers", [])[:2]:
                price = offer.get("price") or {}
                if price.get("currency", "USD") != "USD" or offer.get("availability") != "InStock":
                    continue
                domain = str(offer.get("domain", "")).lower()
                mid = "c3:" + domain
                self.directory.add_merchant(Merchant(id=mid, name=domain, domain=domain, category=(item.get("category") or {}).get("slug", "general"), verified=True, note="Channel3 retailer"))
                images = item.get("images") or []
                main = next((i for i in images if i.get("is_main_image")), images[0] if images else {})
                p = Product(
                    id=f"c3:{item['id']}:{domain}",
                    merchant_id=mid,
                    title=str(item.get("title", ""))[:200],
                    description=str(item.get("description") or "")[:600],
                    price=D(price.get("price", 0)),
                    category=((item.get("category") or {}).get("slug") or "general").replace("-", "_"),
                    url=offer.get("url", ""),
                    image=main.get("cleaned_url") or main.get("url", ""),
                    brand=", ".join(b.get("name", "") for b in item.get("brands") or [])[:80],
                    source="channel3",
                )
                self.directory.add_product(p)
                out.append(p)
        return out


class Catalog:
    def __init__(self, directory: Directory, channel3: Optional[Channel3] = None) -> None:
        self.directory = directory
        self.channel3 = channel3

    @property
    def source(self) -> str:
        return "channel3" if self.channel3 else "fixture"

    def search(self, query: str, max_price: Optional[Decimal] = None, limit: int = 12) -> list[Product]:
        if self.channel3:
            try:
                found = self.channel3.search(query, max_price, limit)
                if found:
                    return found
            except httpx.HTTPError:
                pass  # fall back to the fixture storefronts rather than failing the agent
        return self.directory.search_fixture(query, max_price, limit)
