"""Seller and product catalog (fixture data shipped with the repo)."""

from __future__ import annotations

import json
from pathlib import Path

from .config import ROOT
from .models import Product, Seller

CATALOG_PATH = ROOT / "data" / "catalog.json"


class Catalog:
    def __init__(self, products: list[Product], sellers: list[Seller]) -> None:
        self.products = {p.key: p for p in products}
        self.sellers = {s.id: s for s in sellers}

    @classmethod
    def load(cls, path: Path = CATALOG_PATH, seller_emails: dict[str, str] | None = None) -> "Catalog":
        raw = json.loads(path.read_text(encoding="utf-8"))
        products = [Product(**p) for p in raw["products"]]
        sellers = [Seller(**s) for s in raw["sellers"]]
        for s in sellers:
            if seller_emails and s.id in seller_emails:
                s.paypal_email = seller_emails[s.id]
        return cls(products, sellers)

    def product(self, key: str) -> Product:
        return self.products[key]

    def product_name(self, key: str) -> str:
        p = self.products.get(key)
        return p.name if p else key

    def local_name(self, key: str, lang: str) -> str:
        p = self.products.get(key)
        if not p:
            return key
        return p.local.get(lang) or p.local.get("en") or p.name

    def seller(self, sid: str) -> Seller:
        return self.sellers[sid]

    def sellers_for(self, product: str) -> list[Seller]:
        return [s for s in self.sellers.values() if s.listing(product)]

    def match_product(self, text: str) -> str | None:
        """Map free text to a product key using the longest matching synonym."""
        t = text.lower()
        best: tuple[int, str] | None = None
        for p in self.products.values():
            for syn in [p.name.lower(), *p.synonyms, *[v.lower() for v in p.local.values()]]:
                if syn and syn in t and (best is None or len(syn) > best[0]):
                    best = (len(syn), p.key)
        return best[1] if best else None

