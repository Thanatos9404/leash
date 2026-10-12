"""Choose the basket from agreed threads. Deterministic: the model explains the choice, it does not make it."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from itertools import combinations
from typing import Optional

from .catalog import Catalog
from .models import Mandate, Thread
from .money import D


@dataclass
class Pick:
    seller_id: str
    product: str
    qty_kg: int
    unit_price: Decimal
    list_price: Decimal

    @property
    def value(self) -> Decimal:
        return D(self.unit_price * self.qty_kg)


@dataclass
class Plan:
    picks: list[Pick]
    cost: Decimal
    sellers: list[str]
    covered: dict[str, int]
    complete: bool
    notes: list[str] = field(default_factory=list)


def _fill(mandate: Mandate, agreed: list[Thread], subset: set[str], catalog: Catalog) -> Optional[Plan]:
    picks: list[Pick] = []
    covered: dict[str, int] = {}
    complete = True
    for item in mandate.items:
        need = item.qty_kg
        options = sorted(
            (t for t in agreed if t.product == item.product and t.seller_id in subset),
            key=lambda t: (t.agreed_price, -t.qty_kg),
        )
        got = 0
        for t in options:
            if got >= need:
                break
            listing = catalog.seller(t.seller_id).listing(t.product)
            take = min(t.qty_kg, need - got)
            if take < listing.min_order_kg:
                continue
            picks.append(Pick(t.seller_id, t.product, take, t.agreed_price, t.list_price))
            got += take
        covered[item.product] = got
        if got < need:
            complete = False
    used = sorted({p.seller_id for p in picks})
    if set(used) != subset:
        return None  # a seller in the subset contributed nothing; the smaller subset covers this case
    cost = D(sum((p.value for p in picks), Decimal("0")))
    return Plan(picks, cost, used, covered, complete)


def choose(mandate: Mandate, threads: list[Thread], catalog: Catalog) -> tuple[Optional[Plan], list[dict]]:
    """Return (best plan, alternatives considered) under budget, seller-count and per-seller caps."""
    agreed = [t for t in threads if t.status == "agreed" and t.agreed_price is not None]
    sellers = sorted({t.seller_id for t in agreed})
    plans: list[Plan] = []
    for k in range(1, len(sellers) + 1):
        for subset in combinations(sellers, k):
            plan = _fill(mandate, agreed, set(subset), catalog)
            if plan:
                plans.append(plan)
    if not plans:
        return None, []

    def per_seller_ok(p: Plan) -> bool:
        if mandate.max_per_seller is None:
            return True
        for sid in p.sellers:
            if sum((x.value for x in p.picks if x.seller_id == sid), Decimal("0")) > mandate.max_per_seller:
                return False
        return True

    def ok(p: Plan) -> bool:
        return p.complete and len(p.sellers) <= mandate.max_sellers and p.cost <= mandate.budget_total and per_seller_ok(p)

    def members(p: Plan) -> int:
        return sum(catalog.seller(s).members for s in p.sellers)

    feasible = sorted((p for p in plans if ok(p)), key=lambda p: (p.cost, len(p.sellers), -members(p)))
    alternatives: list[dict] = []
    cheapest_any = min((p for p in plans if p.complete), key=lambda p: p.cost, default=None)
    if feasible:
        best = feasible[0]
        if cheapest_any and cheapest_any.cost < best.cost:
            reasons = []
            if len(cheapest_any.sellers) > mandate.max_sellers:
                reasons.append(f"needs {len(cheapest_any.sellers)} sellers (mandate max {mandate.max_sellers})")
            if not per_seller_ok(cheapest_any):
                reasons.append("breaks the per-seller cap")
            alternatives.append({"sellers": cheapest_any.sellers, "cost": str(cheapest_any.cost), "rejected_because": "; ".join(reasons) or "constraint"})
        for p in feasible[1:3]:
            alternatives.append({"sellers": p.sellers, "cost": str(p.cost), "rejected_because": f"costs ${D(p.cost - best.cost)} more"})
        return best, alternatives
    # Nothing satisfies every constraint: return the closest plan so the buyer can see why, policy will block it.
    fallback = sorted(plans, key=lambda p: (not p.complete, p.cost > mandate.budget_total, p.cost))[0]
    if not fallback.complete:
        fallback.notes.append("Could not source the full quantity from agreed sellers.")
    if fallback.cost > mandate.budget_total:
        fallback.notes.append(f"Cheapest complete basket ${fallback.cost} exceeds the ${mandate.budget_total} budget.")
    if len(fallback.sellers) > mandate.max_sellers:
        fallback.notes.append(f"Needs {len(fallback.sellers)} sellers; mandate allows {mandate.max_sellers}.")
    return fallback, alternatives
