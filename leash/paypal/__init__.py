"""PayPal access: the real sandbox REST client, or the labelled fixture simulator when no keys exist."""

from __future__ import annotations

from typing import Callable, Optional, Union

from ..config import Settings
from .client import PayPalError, RestPayPal, approve_link, first_authorization
from .fixture import FixturePayPal, empty_state

PayPalAPI = Union[RestPayPal, FixturePayPal]


def build_paypal(settings: Settings, fixture_state: Optional[dict] = None, persist: Optional[Callable[[dict], None]] = None) -> PayPalAPI:
    if settings.paypal_mode == "sandbox":
        return RestPayPal(settings)
    return FixturePayPal(settings.public_base_url, state=fixture_state, persist=persist)


__all__ = ["PayPalAPI", "PayPalError", "RestPayPal", "FixturePayPal", "build_paypal", "approve_link", "first_authorization", "empty_state"]
