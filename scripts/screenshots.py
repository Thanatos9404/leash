"""Drive the UI end to end in headless Chromium and save screenshots (optional dev tool, fixture mode).

    pip install playwright && playwright install chromium
    python scripts/screenshots.py http://localhost:8000 docs/screenshots

Set PLAYWRIGHT_CHROME to use an already-installed Chromium. The run creates a mandate, funds it on the simulated
PayPal page, runs the agent, approves a step-up cart, runs the red team, opens a receipt and a dispute, and
captures desktop (1440) and phone (375) views.
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


def shot(page: Page, name: str, full: bool = False) -> None:
    page.wait_for_timeout(900)
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=full)
    print("saved", name)


def wait_text(page: Page, text: str, timeout: float = 90) -> None:
    page.get_by_text(text, exact=False).first.wait_for(timeout=timeout * 1000)


def main() -> None:
    with sync_playwright() as p:
        exe = os.getenv("PLAYWRIGHT_CHROME")
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        page = ctx.new_page()
        page.request.post(f"{BASE}/api/demo/reset")

        page.goto(BASE)
        page.wait_for_timeout(2500)
        shot(page, "01-landing-hero")
        page.mouse.wheel(0, 900)
        page.wait_for_timeout(1500)
        shot(page, "02-landing-product")
        page.goto(BASE)
        page.wait_for_timeout(1500)
        page.evaluate("window.scrollTo(0, 0)")
        shot(page, "03-landing-full") if os.getenv("FULL") else None

        page.goto(f"{BASE}/app/mandates/new")
        wait_text(page, "Write a mandate")
        page.get_by_role("button", name="Coffee restock").click()
        shot(page, "10-compose")
        page.get_by_role("button", name="Compile the rules").click()
        wait_text(page, "What your agent may do")
        shot(page, "11-review")
        page.get_by_role("button", name="Sign mandate").click()
        wait_text(page, "Approve allowance on PayPal")
        page.get_by_role("button", name="Approve allowance on PayPal").click()
        page.wait_for_url("**/fixture/paypal/checkoutnow**", timeout=30000)
        shot(page, "12-paypal-approval")
        page.get_by_role("button", name="Approve (simulated)").click()
        page.wait_for_url("**/app/mandates/**", timeout=30000)
        wait_text(page, "Send the agent shopping")
        page.get_by_role("button", name="Restock my usual 1 kg bag of medium roast whole beans").click()
        wait_text(page, "Open receipt and checks", 120)
        shot(page, "13-mandate-active")
        page.get_by_role("button", name="A 1 kg bag of Ethiopia Guji from Daily Grind").click()
        wait_text(page, "Review and approve", 120)

        page.goto(f"{BASE}/app/approvals")
        wait_text(page, "Approve and sign")
        shot(page, "20-approval")
        page.get_by_role("button", name="Approve and sign").first.click()
        page.wait_for_timeout(2500)

        page.goto(f"{BASE}/app/redteam")
        wait_text(page, "Run all eight")
        page.get_by_role("button", name="Run all eight").click()
        for _ in range(60):
            if page.get_by_role("button", name="Run attack").count() == 0:
                break
            page.wait_for_timeout(1000)
        shot(page, "21-redteam")

        page.goto(f"{BASE}/app")
        page.wait_for_timeout(2500)
        shot(page, "22-overview")

        page.goto(f"{BASE}/app/receipts")
        wait_text(page, "Intent receipts")
        shot(page, "23-receipts")
        page.locator("tbody tr").first.click()
        wait_text(page, "Verify receipt")
        page.get_by_role("button", name="Verify receipt").click()
        page.wait_for_timeout(1500)
        if page.get_by_role("button", name="Simulate a buyer dispute").count():
            page.get_by_role("button", name="Simulate a buyer dispute").click()
            wait_text(page, "Respond with the signed receipt")
            page.get_by_role("button", name="Respond with the signed receipt").click()
            page.wait_for_timeout(2500)
        shot(page, "24-receipt-detail")

        page.goto(f"{BASE}/app/activity")
        wait_text(page, "Verify the chain")
        page.get_by_role("button", name="Verify the chain").click()
        page.wait_for_timeout(1200)
        shot(page, "25-audit-trail")
        page.goto(f"{BASE}/app/integration")
        page.wait_for_timeout(2000)
        shot(page, "26-integration")
        page.goto(f"{BASE}/app")
        page.wait_for_timeout(1500)
        page.get_by_role("button", name="Kill switch").click()
        page.wait_for_timeout(900)
        shot(page, "27-kill-switch")

        phone = browser.new_context(viewport={"width": 375, "height": 812}, device_scale_factor=2, is_mobile=True, has_touch=True)
        m = phone.new_page()
        m.goto(BASE)
        m.wait_for_timeout(2500)
        shot(m, "30-phone-landing")
        m.goto(f"{BASE}/app")
        m.wait_for_timeout(2500)
        shot(m, "31-phone-app")
        m.goto(f"{BASE}/app/approvals")
        m.wait_for_timeout(2000)
        shot(m, "32-phone-approvals")
        tablet = browser.new_context(viewport={"width": 768, "height": 1024})
        t = tablet.new_page()
        t.goto(f"{BASE}/app")
        t.wait_for_timeout(2000)
        shot(t, "33-tablet-app")
        for pg in (page, m, t):
            w = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            if w > 0:
                print("HORIZONTAL OVERFLOW", pg.url, w)
        browser.close()


if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"done in {time.time() - t0:.0f}s")
