"""bKash charge calculator via headless browser (same UI as the public site)."""

from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from playwright.async_api import (
    Browser,
    Error as PlaywrightError,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
)

CALCULATOR_URL = "https://www.bkash.com/en/help/charge-calculator"

DEFAULT_SERVICE_NAME = "Cash Out from Agent (App)"

_CHARGE_HEADING = re.compile(
    r"Charge\s+for\s+.+?:\s*([\d.]+)\s*BDT",
    re.IGNORECASE | re.DOTALL,
)


class BkashError(Exception):
    """Base error for bKash automation."""


class BkashBlockedError(BkashError):
    """Likely Cloudflare / WAF or regional block."""


class BkashParseError(BkashError):
    """Could not read the charge from the page."""


class BkashTimeoutError(BkashError):
    """Page or UI step took too long."""


class BkashConfigError(BkashError):
    """Missing or invalid deployment configuration (e.g. remote browser URL)."""


def validate_amount(amount: str) -> str:
    try:
        d = Decimal(amount.strip())
    except (InvalidOperation, AttributeError) as e:
        raise ValueError("amount must be a positive decimal number.") from e
    if d <= 0:
        raise ValueError("amount must be greater than zero.")
    return format(d, "f")


def _blocked_html(html: str) -> bool:
    h = html[:12000].lower()
    return (
        "cf-error-details" in h
        or "attention required!" in h
        or "sorry, you have been blocked" in h
        or "just a moment" in h
    )


def _parse_charge_heading(text: str) -> str:
    text = " ".join(text.split())
    m = _CHARGE_HEADING.search(text)
    if not m:
        raise BkashParseError(f"Charge line not found or unrecognized: {text[:200]!r}")
    return m.group(1).strip()


def _chromium_launch_args() -> list[str]:
    raw = os.environ.get("PLAYWRIGHT_CHROMIUM_ARGS", "")
    return [a for a in raw.split() if a]


def _launch_args() -> list[str] | None:
    """Extra Chromium flags for Linux / CI (local ``chromium.launch`` only)."""
    extra = _chromium_launch_args()
    if os.environ.get("PLAYWRIGHT_LINUX_LAUNCH_ARGS", "").lower() in ("1", "true", "yes"):
        base = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-software-rasterizer",
        ]
        return base + extra
    return extra if extra else None


def _vercel_production_or_preview() -> bool:
    env = (os.environ.get("VERCEL_ENV") or "").lower()
    return bool(os.environ.get("VERCEL")) and env in ("production", "preview")


def _headless() -> bool:
    return os.environ.get("BKASH_HEADLESS", "true").lower() in ("1", "true", "yes")


async def fetch_cashout_charge(
    browser: Browser,
    amount: str,
    *,
    service_name: str = DEFAULT_SERVICE_NAME,
    timeout_ms: int = 60_000,
) -> tuple[str, str, str]:
    """
    Drive the official calculator page and return (amount, service_name, charge).

    Requires a shared ``Browser`` from app lifespan (see ``main``).
    """
    amt = validate_amount(amount)
    context = await browser.new_context(
        locale="en-US",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
    )
    page = await context.new_page()
    try:
        await page.goto(
            CALCULATOR_URL,
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        html = await page.content()
        if _blocked_html(html):
            raise BkashBlockedError(
                "The calculator page looks blocked (e.g. Cloudflare). "
                "Try another network, or run with BKASH_HEADLESS=0 to debug."
            )

        combo = page.get_by_role("combobox", name="Select Service")
        await combo.click(timeout=timeout_ms)
        await page.get_by_role("option", name=service_name, exact=True).click(
            timeout=timeout_ms
        )

        amount_input = page.get_by_role("spinbutton", name="Amount (BDT)")
        await amount_input.click(timeout=timeout_ms)
        await amount_input.fill(amt)

        await page.get_by_role("button", name="Calculate").click(timeout=timeout_ms)

        charge_locator = page.locator("h5").filter(has_text=re.compile(r"Charge\s+for", re.I))
        await charge_locator.first.wait_for(state="visible", timeout=timeout_ms)
        heading_text = await charge_locator.first.inner_text()
        charge = _parse_charge_heading(heading_text)
        return amt, service_name, charge
    except PlaywrightTimeoutError as e:
        raise BkashTimeoutError(str(e) or "Timed out waiting for the calculator UI.") from e
    except PlaywrightError as e:
        raise BkashError(str(e)) from e
    finally:
        await context.close()


async def create_browser() -> tuple[Playwright, Browser]:
    """
    Connect to a remote Chromium (``PLAYWRIGHT_WS_ENDPOINT``) or launch a local binary.

    Vercel production/preview bundles cannot include Chromium (~245MB Lambda cap). Use a
    hosted Playwright endpoint (Browserless, Browserbase, etc.) and set
    ``PLAYWRIGHT_WS_ENDPOINT`` to its ``wss://...`` URL.
    """
    from playwright.async_api import async_playwright

    ws = (os.environ.get("PLAYWRIGHT_WS_ENDPOINT") or "").strip()
    if _vercel_production_or_preview() and not ws:
        raise BkashConfigError(
            "Vercel serverless has a ~245MB bundle limit; Playwright plus Chromium "
            "does not fit. Set the environment variable PLAYWRIGHT_WS_ENDPOINT to a "
            "hosted Chromium WebSocket URL (for example from Browserless or Browserbase). "
            "Locally, run `playwright install chromium` and omit PLAYWRIGHT_WS_ENDPOINT, "
            "or use `vercel dev` (VERCEL_ENV=development) without a remote browser."
        )

    pw = await async_playwright().start()
    if ws:
        browser = await pw.chromium.connect(ws, timeout=120_000)
        return pw, browser

    bundle = Path(__file__).resolve().parent.parent / "playwright-browsers"
    if bundle.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundle)

    browser = await pw.chromium.launch(
        headless=_headless(),
        args=_launch_args(),
    )
    return pw, browser


async def dispose_browser(pw: Playwright | None, browser: Browser | None) -> None:
    if browser is not None:
        await browser.close()
    if pw is not None:
        await pw.stop()
