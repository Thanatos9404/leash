"""Money helpers. All amounts are Decimal with two places; nothing money-related is a float."""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Iterable, Sequence

CENT = Decimal("0.01")


def D(value) -> Decimal:
    """Parse a value into a 2dp Decimal (half-up). Floats go through str() to avoid binary noise."""
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, float):
        d = Decimal(str(value))
    else:
        try:
            d = Decimal(str(value).strip().replace(",", "").replace("$", ""))
        except (InvalidOperation, AttributeError) as e:
            raise ValueError(f"not a money amount: {value!r}") from e
    if not d.is_finite():
        raise ValueError(f"not a finite amount: {value!r}")
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def pct(value) -> Decimal:
    """Parse a fraction like 0.3, '30%', or 30 into a Decimal in [0, 1] with 4 places."""
    if isinstance(value, str) and value.strip().endswith("%"):
        d = Decimal(value.strip()[:-1]) / 100
    else:
        d = Decimal(str(value))
        if d > 1:
            d = d / 100
    return d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def fmt(value) -> str:
    """PayPal wants amounts as strings with exactly two decimals for USD."""
    return f"{D(value):.2f}"


def mul(a, b) -> Decimal:
    return D(Decimal(str(a)) * Decimal(str(b)))


def floor_cents(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_DOWN)


def allocate(total, weights: Sequence) -> list[Decimal]:
    """Split `total` across `weights` so the parts sum to exactly `total` (largest remainder method)."""
    total = D(total)
    w = [Decimal(str(x)) for x in weights]
    s = sum(w)
    if not w:
        return []
    if s == 0:
        w = [Decimal(1)] * len(w)
        s = Decimal(len(w))
    raw = [total * x / s for x in w]
    parts = [floor_cents(r) for r in raw]
    remainder = int(((total - sum(parts)) / CENT).to_integral_value())
    order = sorted(range(len(raw)), key=lambda i: (raw[i] - parts[i]), reverse=True)
    for i in order[:remainder]:
        parts[i] += CENT
    return parts


def total(values: Iterable) -> Decimal:
    return D(sum((D(v) for v in values), Decimal("0")))
