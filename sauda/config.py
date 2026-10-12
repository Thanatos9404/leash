"""Runtime settings.

Two hard rules live here:
  * PayPal is sandbox-only. There is no live base URL anywhere in this codebase and
    PAYPAL_ENV=live (or anything other than "sandbox") stops the app at startup.
  * Secrets are read from the environment at runtime and never written to disk or logs.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Optional pointer to another .env that holds NEBIUS_API_KEY (for local development).
# Only that one key is read from it; nothing is copied.
_extra = os.getenv("SAUDA_EXTRA_ENV_FILE", "").strip()
if _extra and Path(_extra).is_file() and not os.getenv("NEBIUS_API_KEY"):
    _val = (dotenv_values(_extra).get("NEBIUS_API_KEY") or "").strip()
    if _val:
        os.environ["NEBIUS_API_KEY"] = _val

PAYPAL_SANDBOX_API = "https://api-m.sandbox.paypal.com"
PAYPAL_SANDBOX_WEB = "https://www.sandbox.paypal.com"


class SandboxOnlyError(RuntimeError):
    """Raised when anything tries to point Sauda at live PayPal."""


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    paypal_env: str = field(default_factory=lambda: os.getenv("PAYPAL_ENV", "sandbox").strip().lower())
    paypal_client_id: str = field(default_factory=lambda: os.getenv("PAYPAL_CLIENT_ID", "").strip())
    paypal_client_secret: str = field(default_factory=lambda: os.getenv("PAYPAL_CLIENT_SECRET", "").strip())
    paypal_webhook_id: str = field(default_factory=lambda: os.getenv("PAYPAL_WEBHOOK_ID", "").strip())
    # "auto" uses the sandbox when both credentials are present, otherwise fixtures.
    paypal_mode_pref: str = field(default_factory=lambda: os.getenv("SAUDA_PAYPAL_MODE", "auto").strip().lower())

    nebius_api_key: str = field(default_factory=lambda: os.getenv("NEBIUS_API_KEY", "").strip())
    nebius_base_url: str = field(
        default_factory=lambda: os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/").strip()
    )
    # Buyer agent, mandate drafting and decision memos.
    llm_model: str = field(
        default_factory=lambda: os.getenv("SAUDA_LLM_MODEL", "Qwen/Qwen3-30B-A3B-Instruct-2507").strip()
    )
    # Seller agents write to FPO managers in Hindi, Marathi, Tamil and Malayalam: a strong multilingual model.
    llm_seller_model: str = field(
        default_factory=lambda: os.getenv("SAUDA_LLM_SELLER_MODEL", "google/gemma-3-27b-it").strip()
    )
    # Dispatch-report extraction and the fallback when another model fails.
    llm_fast_model: str = field(
        default_factory=lambda: os.getenv("SAUDA_LLM_FAST_MODEL", "Qwen/Qwen3-30B-A3B-Instruct-2507").strip()
    )
    llm_disabled: bool = field(default_factory=lambda: _flag("SAUDA_OFFLINE"))
    llm_timeout_s: float = field(default_factory=lambda: float(os.getenv("SAUDA_LLM_TIMEOUT", "40")))
    llm_concurrency: int = field(default_factory=lambda: int(os.getenv("SAUDA_LLM_CONCURRENCY", "6")))

    # Where PayPal sends the buyer back to. Render sets RENDER_EXTERNAL_URL automatically.
    public_base_url: str = field(
        default_factory=lambda: (
            os.getenv("SAUDA_PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "http://localhost:8000"
        ).rstrip("/")
    )
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("SAUDA_DATA_DIR", str(ROOT / "var"))))
    # Kill switch: when set, every money-moving action is blocked by policy.
    payments_frozen: bool = field(default_factory=lambda: _flag("SAUDA_PAYMENTS_FROZEN"))
    platform_fee_pct: str = field(default_factory=lambda: os.getenv("SAUDA_PLATFORM_FEE_PCT", "0.02"))
    # Ceiling on automatic (mandate-approved) releases per deal; anything beyond needs a human.
    auto_release_daily_cap: str = field(default_factory=lambda: os.getenv("SAUDA_AUTO_RELEASE_DAILY_CAP", "600"))
    buyer_email: str = field(default_factory=lambda: os.getenv("SAUDA_BUYER_EMAIL", "").strip())
    seller_emails_raw: str = field(default_factory=lambda: os.getenv("SAUDA_SELLER_EMAILS", "").strip())
    secret: str = field(default_factory=lambda: os.getenv("SAUDA_SECRET", "").strip())

    def __post_init__(self) -> None:
        if self.paypal_env != "sandbox":
            raise SandboxOnlyError(
                f"PAYPAL_ENV={self.paypal_env!r}: Sauda only talks to the PayPal sandbox. Live mode is not supported."
            )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.secret:
            # Per-install signing secret for mandates, kept out of git under var/.
            path = self.data_dir / "mandate_secret"
            if path.exists():
                self.secret = path.read_text(encoding="utf-8").strip()
            else:
                self.secret = secrets.token_hex(32)
                path.write_text(self.secret, encoding="utf-8")

    @property
    def paypal_mode(self) -> str:
        """'sandbox' or 'fixture'."""
        if self.paypal_mode_pref == "fixture":
            return "fixture"
        if self.paypal_client_id and self.paypal_client_secret:
            return "sandbox"
        if self.paypal_mode_pref == "sandbox":
            raise SandboxOnlyError("SAUDA_PAYPAL_MODE=sandbox needs PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET.")
        return "fixture"

    @property
    def has_llm(self) -> bool:
        return bool(self.nebius_api_key) and not self.llm_disabled

    @property
    def seller_emails(self) -> dict[str, str]:
        """SAUDA_SELLER_EMAILS="MMF=fpo1@example.com,ETG=fpo2@example.com" maps seller ids to sandbox emails."""
        out: dict[str, str] = {}
        for part in self.seller_emails_raw.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                if k.strip() and v.strip():
                    out[k.strip().upper()] = v.strip()
        return out

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sauda.db"
