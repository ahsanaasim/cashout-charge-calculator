from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from playwright.async_api import Browser

from app import bkash


@asynccontextmanager
async def lifespan(app: FastAPI):
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
        "After `pip install -r requirements.txt`, run: `playwright install chromium`."
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
    browser: Browser | None = getattr(request.app.state, "browser", None)
    if browser is None:
        raise HTTPException(status_code=503, detail="Browser is not ready yet.")

    name = (body.service_name or bkash.DEFAULT_SERVICE_NAME).strip()
    try:
        amount, service_name, charge = await bkash.fetch_cashout_charge(
            browser,
            body.amount,
            service_name=name,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except bkash.BkashBlockedError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except bkash.BkashParseError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except bkash.BkashTimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e)) from e
    except bkash.BkashError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    total = str(Decimal(amount) + Decimal(charge))
    return CashoutChargeResponse(
        amount=amount,
        service_name=service_name,
        charge=charge,
        totalAmount=total,
    )
