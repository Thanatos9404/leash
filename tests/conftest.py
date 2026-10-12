"""Shared fixtures. Every test runs offline: fixture PayPal, no model key, a temporary database."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sauda.app import build_engine  # noqa: E402
from sauda.config import Settings  # noqa: E402
from sauda.llm import LLM  # noqa: E402


def make_settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        paypal_env="sandbox",
        paypal_client_id="",
        paypal_client_secret="",
        paypal_webhook_id="",
        paypal_mode_pref="fixture",
        nebius_api_key="",
        llm_disabled=True,
        public_base_url="http://testserver",
        data_dir=tmp_path,
        payments_frozen=False,
        platform_fee_pct="0.02",
        auto_release_daily_cap="600",
        buyer_email="anita.shah@sauda.example",
        seller_emails_raw="",
        secret="test-secret-key",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def engine(settings: Settings):
    eng = build_engine(settings)
    yield eng
    eng.store.close()


def run(coro):
    return asyncio.run(coro)


class ScriptedLLM(LLM):
    """An LLM stand-in that returns scripted JSON per purpose and records prompts."""

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
