"""Check your PayPal sandbox keys in under a minute.

    python scripts/sandbox_smoke.py

Reads PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET from the environment or .env, gets an OAuth token from the sandbox,
creates a $1.00 AUTHORIZE order and prints the approval link. Nothing is captured; an unapproved order simply
expires. Refuses to run against anything but the sandbox.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leash.config import Settings  # noqa: E402
from leash.paypal import PayPalError, RestPayPal, approve_link  # noqa: E402


async def main() -> None:
    s = Settings()
    if not (s.paypal_client_id and s.paypal_client_secret):
        raise SystemExit("Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET first (see KEYS_NEEDED.md).")
    pp = RestPayPal(s)
    try:
        body = {
            "intent": "AUTHORIZE",
            "purchase_units": [{"reference_id": "leash-smoke", "description": "Leash sandbox key check", "amount": {"currency_code": "USD", "value": "1.00"}}],
            "payment_source": {"paypal": {"experience_context": {"user_action": "CONTINUE", "shipping_preference": "NO_SHIPPING",
                                                                  "return_url": f"{s.public_base_url}/app", "cancel_url": f"{s.public_base_url}/app"}}},
        }
        order = await pp.create_order(body, "leash-smoke-check")
        print("OAuth token: ok")
        print(f"Created sandbox order {order['id']} ({order['status']})")
        print("Approve as your sandbox personal account (optional):", approve_link(order))
    except PayPalError as e:
        print("PayPal refused:", e)
        sys.exit(1)
    finally:
        await pp.aclose()


if __name__ == "__main__":
    asyncio.run(main())
