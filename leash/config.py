"""Runtime settings.

Hard rules that live here:
  * PayPal is sandbox-only. There is no live PayPal host anywhere in this codebase, and PAYPAL_ENV set to
    anything other than "sandbox" stops the app at startup.
  * Secrets are read from the environment at runtime and never written to logs or responses.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Optional pointer to another .env that already holds NEBIUS_API_KEY (local development).
# Only that one key is read from it; nothing is copied into this project.
_extra = os.getenv("LEASH_EXTRA_ENV_FILE", "").strip()
if _extra and Path(_extra).is_file() and not os.getenv("NEBIUS_API_KEY"):
    _val = (dotenv_values(_extra).get("NEBIUS_API_KEY") or "").strip()
    if _val:
        os.environ["NEBIUS_API_KEY"] = _val

PAYPAL_SANDBOX_API = "https://api-m.sandbox.paypal.com"


class SandboxOnlyError(RuntimeError):
    """Raised when anything tries to point Leash at live PayPal."""


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    paypal_env: str = field(default_factory=lambda: os.getenv("PAYPAL_ENV", "sandbox").strip().lower())
    paypal_client_id: str = field(default_factory=lambda: os.getenv("PAYPAL_CLIENT_ID", "").strip())
    paypal_client_secret: str = field(default_factory=lambda: os.getenv("PAYPAL_CLIENT_SECRET", "").strip())
    paypal_webhook_id: str = field(default_factory=lambda: os.getenv("PAYPAL_WEBHOOK_ID", "").strip())
    # "auto" uses the sandbox when both credentials are present, otherwise the labelled fixture simulator.
    paypal_mode_pref: str = field(default_factory=lambda: os.getenv("LEASH_PAYPAL_MODE", "auto").strip().lower())

    nebius_api_key: str = field(default_factory=lambda: os.getenv("NEBIUS_API_KEY", "").strip())
    nebius_base_url: str = field(
        default_factory=lambda: os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/").strip()
    )
    # Mandate compiler and shopping agent.
    llm_model: str = field(default_factory=lambda: os.getenv("LEASH_LLM_MODEL", "Qwen/Qwen3-30B-A3B-Instruct-2507").strip())
    # Plain-language explanations and dispute statements.
    llm_writer_model: str = field(default_factory=lambda: os.getenv("LEASH_LLM_WRITER_MODEL", "google/gemma-3-27b-it").strip())
    # Fallback when another model fails.
    llm_fast_model: str = field(default_factory=lambda: os.getenv("LEASH_LLM_FAST_MODEL", "Qwen/Qwen3-30B-A3B-Instruct-2507").strip())
    llm_disabled: bool = field(default_factory=lambda: _flag("LEASH_OFFLINE"))
    llm_timeout_s: float = field(default_factory=lambda: float(os.getenv("LEASH_LLM_TIMEOUT", "30")))
    llm_concurrency: int = field(default_factory=lambda: int(os.getenv("LEASH_LLM_CONCURRENCY", "4")))

    channel3_api_key: str = field(default_factory=lambda: os.getenv("CHANNEL3_API_KEY", "").strip())

    # Where PayPal sends the user back after approving. Render sets RENDER_EXTERNAL_URL automatically.
    public_base_url: str = field(
        default_factory=lambda: (os.getenv("LEASH_PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "http://localhost:8000").rstrip("/")
    )
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("LEASH_DATA_DIR", str(ROOT / "var"))))
    # Global kill switch: when set, every capture and new authorization is refused.
    payments_frozen: bool = field(default_factory=lambda: _flag("LEASH_PAYMENTS_FROZEN"))
    # The sandbox personal (buyer) account used in the demo; shown on receipts.
    buyer_email: str = field(default_factory=lambda: os.getenv("LEASH_BUYER_EMAIL", "").strip())
    owner_name: str = field(default_factory=lambda: os.getenv("LEASH_OWNER_NAME", "Yashvardhan").strip())
    allow_reset: bool = field(default_factory=lambda: _flag("LEASH_ALLOW_RESET"))

    def __post_init__(self) -> None:
        if self.paypal_env != "sandbox":
            raise SandboxOnlyError(
                f"PAYPAL_ENV={self.paypal_env!r}: Leash only talks to the PayPal sandbox. Live mode is not supported."
            )
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def paypal_mode(self) -> str:
        """'sandbox' or 'fixture'."""
        if self.paypal_mode_pref == "fixture":
            return "fixture"
        if self.paypal_client_id and self.paypal_client_secret:
            return "sandbox"
        if self.paypal_mode_pref == "sandbox":
            raise SandboxOnlyError("LEASH_PAYPAL_MODE=sandbox needs PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET.")
        return "fixture"

    @property
    def has_llm(self) -> bool:
        return bool(self.nebius_api_key) and not self.llm_disabled

    @property
    def db_path(self) -> Path:
        return self.data_dir / "leash.db"
