"""Shared fixtures. Every test runs offline: fixture PayPal, no model key, a temporary data directory."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leash.app import build_leash  # noqa: E402
from leash.compiler import SAMPLES  # noqa: E402
from leash.config import Settings  # noqa: E402
from leash.llm import LLM  # noqa: E402


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        paypal_env="sandbox", paypal_client_id="", paypal_client_secret="", paypal_webhook_id="", paypal_mode_pref="fixture",
        nebius_api_key="", llm_disabled=True, channel3_api_key="", public_base_url="http://testserver", data_dir=tmp_path,
        payments_frozen=False, buyer_email="", owner_name="Test Owner", allow_reset=True,
    )
    base.update(overrides)
    return Settings(**base)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def leash(settings: Settings):
    l = build_leash(settings)
    yield l
    l.store.close()


async def active_mandate(l, sample: str = "coffee"):
    """Compile, sign (custodial demo key), fund and approve the allowance in fixture mode."""
    m = await l.compile(SAMPLES[sample])
    m = await l.sign(m.id)
    m = await l.fund(m.id)
    l.paypal.approve_order(m.envelope.order_id)
    await l.on_paypal_return("allowance", m.id)
    return l.mandate(m.id)


class ScriptedLLM(LLM):
    """Returns scripted JSON per purpose and records prompts."""

    def __init__(self, settings: Settings, replies: dict) -> None:
        super().__init__(settings)
        self.replies = replies
        self.seen: list[tuple[str, str, str]] = []

    @property
    def available(self) -> bool:  # type: ignore[override]
        return True

    def label(self, fast: bool = False, role=None) -> str:
        return "scripted"

    async def json(self, system, user, *, purpose, fast=False, role=None, max_tokens=700, temperature=0.4):
        self.seen.append((purpose, system, user))
        r = self.replies.get(purpose)
        return r(system, user) if callable(r) else r
