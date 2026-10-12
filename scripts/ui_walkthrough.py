"""Drive the web UI end to end in a headless browser and save screenshots (optional dev tool).

    pip install playwright && playwright install chromium
    python scripts/ui_walkthrough.py http://localhost:8000 docs/screenshots

Works in fixture mode (it clicks the simulated PayPal pages). Each step is saved as NN-name.png.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "docs/screenshots")
OUT.mkdir(parents=True, exist_ok=True)
n = 0


def shot(page: Page, name: str, full: bool = False) -> None:
    global n
    n += 1
    page.wait_for_timeout(400)
    page.screenshot(path=str(OUT / f"{n:02d}-{name}.png"), full_page=full)
    print("saved", name)


def wait_status(page: Page, statuses: tuple[str, ...], timeout: float = 180) -> str:
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = page.evaluate("() => (typeof S !== 'undefined' && S.deal) ? S.deal.status : null")
        if st in statuses:
            return st
        page.wait_for_timeout(700)
    raise TimeoutError(f"status never reached {statuses}")


def main() -> None:
    with sync_playwright() as p:
        exe = os.getenv("PLAYWRIGHT_CHROME")  # optional: point at an already-installed Chromium
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        page.goto(BASE)
        page.evaluate("() => localStorage.clear()")
        page.goto(BASE)
        page.click("#new-deal")
        page.click("#sample")
        shot(page, "brief")
        page.click("#draft")
        wait_status(page, ("mandate_draft",))
        shot(page, "mandate", full=True)
        page.click("#sign")
        wait_status(page, ("mandate_signed",))
        page.click("#negotiate")
        page.wait_for_timeout(6000)
        shot(page, "negotiating")
        wait_status(page, ("proposed",))
        page.evaluate("() => document.querySelector('.approval').scrollIntoView({block: 'center'})")
        shot(page, "proposal")
        page.locator("[data-approve]").first.click()
        page.wait_for_url("**/fixture/paypal/checkoutnow**", timeout=30000)
        shot(page, "paypal-approval")
        page.click("button.pay")
        page.wait_for_url("**/?deal=**", timeout=30000)
        wait_status(page, ("in_fulfilment",))
        shot(page, "advance-captured")

        page.click("[data-view=fpo]")
        page.wait_for_timeout(800)
        sellers = page.evaluate("() => [...new Set(S.deal.lines.map(l => l.seller_id))]")
        for i, sid in enumerate(sellers):
            page.click(f"[data-fpo={sid}]")
            page.wait_for_timeout(500)
            page.locator("[data-sample]").nth(1 if i == 1 else 0).click()  # second seller short-ships
            page.click("#read-dispatch")
            page.wait_for_selector("#confirm-dispatch", timeout=60000)
            if i == 1:
                shot(page, f"fpo-{sid}-preview")
            page.click("#confirm-dispatch")
            page.wait_for_timeout(1500)
            if i == 0:
                shot(page, f"fpo-{sid}-paid")

        page.click("[data-view=buyer]")
        page.wait_for_timeout(800)
        shot(page, "fulfilment", full=True)
        while page.locator("[data-approve]").count():
            page.locator("[data-approve]").first.click()
            page.wait_for_timeout(1500)
        inv = page.evaluate("() => S.deal.paypal.invoice_url")
        page.goto(inv)
        shot(page, "invoice")
        page.click("button.pay")
        page.wait_for_url("**/?deal=**", timeout=30000)
        wait_status(page, ("settling", "settled"))
        page.wait_for_timeout(1000)
        if page.locator("[data-approve]").count():
            page.locator("[data-approve]").first.scroll_into_view_if_needed()
            shot(page, "confirm-receipt")
            page.locator("[data-approve]").first.click()
        wait_status(page, ("settled",), timeout=60)
        shot(page, "settled", full=True)
        page.click("[data-view=money]")
        page.wait_for_timeout(1500)
        shot(page, "money-trail", full=True)
        page.click("[data-view=ledger]")
        page.click("#verify-chain")
        page.wait_for_timeout(1200)
        shot(page, "ledger")
        page.click("[data-view=about]")
        shot(page, "how-it-works", full=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.click("[data-view=buyer]")
        shot(page, "mobile")
        browser.close()


if __name__ == "__main__":
    main()
