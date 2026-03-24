import os
from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from playwright.async_api import Browser

from app import bkash


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Vercel runs one invocation per request (fluid compute can reuse, but no reliable
    # long-lived browser). Launching Chromium at startup often crashes without a bundled
    # browser + breaks cold starts; we open/close per request when VERCEL is set.
    if os.environ.get("VERCEL"):
        yield
        return
    pw, browser = await bkash.create_browser()
    app.state.playwright = pw
    app.state.browser = browser
    try:
        yield
    finally:
        await bkash.dispose_browser(pw, browser)


app = FastAPI(
    title="Cashout Charge API",
    version="0.1.0",
    lifespan=lifespan,
    description=(
        "Uses Playwright (Chromium) against the official bKash calculator. "
        "Locally: `pip install -r requirements.txt` then `playwright install chromium`. "
        "On Vercel production/preview, set **PLAYWRIGHT_WS_ENDPOINT** to a hosted "
        "Chromium WebSocket URL (Browserless, Browserbase, etc.); bundled Chromium "
        "exceeds the platform size limit."
    ),
)


class CashoutChargeRequest(BaseModel):
    amount: str = Field(
        ...,
        description="Amount to cash out (decimal string, e.g. 1503 or 1503.50).",
    )
    service_name: str | None = Field(
        default=None,
        description=(
            "Exact option label in the Service dropdown. "
            f"Default: {bkash.DEFAULT_SERVICE_NAME!r}."
        ),
    )


class CashoutChargeResponse(BaseModel):
    amount: str
    service_name: str
    charge: str
    totalAmount: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/cashout-charge", response_model=CashoutChargeResponse)
async def cashout_charge(request: Request, body: CashoutChargeRequest) -> CashoutChargeResponse:
    name = (body.service_name or bkash.DEFAULT_SERVICE_NAME).strip()

    async def _run(browser: Browser) -> tuple[str, str, str]:
        return await bkash.fetch_cashout_charge(
            browser,
            body.amount,
            service_name=name,
        )

    try:
        if os.environ.get("VERCEL"):
            pw, browser = await bkash.create_browser()
            try:
                amount, service_name, charge = await _run(browser)
            finally:
                await bkash.dispose_browser(pw, browser)
        else:
            browser = getattr(request.app.state, "browser", None)
            if browser is None:
                raise HTTPException(status_code=503, detail="Browser is not ready yet.")
            amount, service_name, charge = await _run(browser)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except bkash.BkashBlockedError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except bkash.BkashParseError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except bkash.BkashTimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e)) from e
    except bkash.BkashConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except bkash.BkashError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    total = str(Decimal(amount) + Decimal(charge))
    return CashoutChargeResponse(
        amount=amount,
        service_name=service_name,
        charge=charge,
        totalAmount=total,
    )
