from __future__ import annotations

import asyncio
import random
import time
from typing import Dict, Optional

import src.logger.logger as log
from src.config import CHECKOUT_CONFIG
from src.sellauth.sellauth import SellAuthAPI, SellAuthError


_RETRYABLE_STATUSES = {408, 409, 422, 425, 429, 500, 502, 503, 504}


class InsufficientStockError(SellAuthError):
    

    def __init__(self, requested: int, available: int) -> None:
        self.requested = requested
        self.available = available
        super().__init__(
            f"Only {available:,} item(s) remain in stock, but {requested:,} were requested.",
            status_code=422,
        )


class CheckoutAutomation:
    _FIRST = ["james", "oliver", "harry", "jack", "george", "noah", "charlie"]
    _LAST = ["smith", "jones", "williams", "taylor", "brown", "davies", "evans"]
    _DOMAINS = ["gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com"]

    def __init__(self, api: SellAuthAPI) -> None:
        self.api = api

    @classmethod
    def _random_email(cls) -> str:
        name = random.choice(cls._FIRST) + random.choice(cls._LAST)
        return f"{name}{random.randint(1, 9999)}@{random.choice(cls._DOMAINS)}"

    def create_checkout(
        self, product_id: int, variant_id: int, shop_id: int, quantity: int = 1
    ) -> str:
        return self.api.create_checkout(product_id, variant_id, shop_id, quantity)

    def create_checkout_with_retry(
        self,
        product_id: int,
        variant_id: int,
        shop_id: int,
        quantity: int = 1,
        *,
        max_attempts: int = CHECKOUT_CONFIG["retry_attempts"],
        retry_delay: float = CHECKOUT_CONFIG["retry_delay_seconds"],
    ) -> str:
        
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if retry_delay < 0:
            raise ValueError("retry_delay cannot be negative")

        retryable_statuses = _RETRYABLE_STATUSES
        for attempt in range(1, max_attempts + 1):
            try:
                return self.create_checkout(product_id, variant_id, shop_id, quantity)
            except SellAuthError as exc:
                status_code = getattr(exc, "status_code", None)
                if status_code == 422:
                    try:
                        available = self.api.get_variant_stock(shop_id, product_id, variant_id)
                    except Exception:
                        available = None
                    if available is not None and quantity > available:
                        raise InsufficientStockError(quantity, available) from exc
                if status_code is not None and status_code not in retryable_statuses:
                    raise
                if attempt >= max_attempts:
                    raise

                delay = min(
                    retry_delay * (2 ** (attempt - 1)),
                    CHECKOUT_CONFIG["retry_delay_cap_seconds"],
                )
                delay += random.uniform(0.0, 0.5)
                log.warn(
                    "Checkout creation failed; retrying",
                    [
                        ("Attempt", f"{attempt}/{max_attempts}"),
                        ("Status", str(status_code or "unknown")),
                        ("RetryIn", f"{delay:.1f}s"),
                    ],
                )
                time.sleep(delay)

        raise RuntimeError("Checkout retry loop ended unexpectedly")

    def get_required_custom_fields(self, checkout_id: str) -> list:
        return self.api.get_required_custom_fields(checkout_id)

    def get_supported_currencies(self, checkout_id: str) -> list[str]:
        return self.api.get_supported_currencies(checkout_id)

    def submit_details(
        self,
        checkout_id: str,
        currency: str = CHECKOUT_CONFIG["currency"],
        email: Optional[str] = None,
        coupon: Optional[str] = None,
    ) -> dict:
        return self.api.submit_details(
            checkout_id,
            email or self._random_email(),
            currency,
            coupon=coupon,
        )

    def submit_custom_fields(
        self, checkout_id: str, field_values: Dict[int, Dict[int, str]]
    ) -> dict:
        return self.api.submit_custom_fields(checkout_id, field_values)

    def get_payment_details(self, checkout_id: str) -> dict:
        return self.api.get_payment_details(checkout_id)

    async def wait_for_payment_async(
        self,
        checkout_id: str,
        poll_interval: float = CHECKOUT_CONFIG["payment_poll_interval_seconds"],
        timeout: float = CHECKOUT_CONFIG["payment_timeout_seconds"],
        request_semaphore: Optional[asyncio.Semaphore] = None,
    ) -> list:
        
        deadline = asyncio.get_running_loop().time() + timeout
        seen_mempool = False
        attempt = 0

        async def call_api(function, *args):
            if request_semaphore is None:
                return await asyncio.to_thread(function, *args)
            async with request_semaphore:
                return await asyncio.to_thread(function, *args)

        while asyncio.get_running_loop().time() < deadline:
            attempt += 1
            minimal = await call_api(self.api.get_payment_status, checkout_id)
            status = str(minimal.get("status", ""))
            paid_usd = float(minimal.get("paid_usd") or 0)
            price_usd = float(minimal.get("price_usd") or 0)

            if not seen_mempool and paid_usd > 0 and status != "completed":
                seen_mempool = True
                log.warn(
                    "Payment detected in mempool",
                    [
                        ("Checkout", checkout_id),
                        ("Paid", f"{paid_usd} USD"),
                    ],
                )

            if status == "completed":
                delivered = await call_api(self.api.get_delivered_items, checkout_id)
                log.success(
                    "Payment completed",
                    [
                        ("Checkout", checkout_id),
                        ("Items", str(len(delivered))),
                    ],
                )
                return delivered

            delay = (
                CHECKOUT_CONFIG["payment_mempool_interval_seconds"]
                if seen_mempool
                else poll_interval
            )
            await asyncio.sleep(
                min(
                    delay + random.uniform(0, 1.0),
                    max(0, deadline - asyncio.get_running_loop().time()),
                )
            )

        raise TimeoutError(f"Payment was not received within {int(timeout)} seconds.")

    def wait_for_payment(
        self,
        checkout_id: str,
        poll_interval: float = CHECKOUT_CONFIG["payment_poll_interval_seconds"],
        timeout: float = CHECKOUT_CONFIG["payment_timeout_seconds"],
    ) -> list:
        deadline = time.monotonic() + timeout
        attempt = 0
        seen_mempool = False

        while time.monotonic() < deadline:
            attempt += 1
            minimal = self.api.get_payment_status(checkout_id)
            status = str(minimal.get("status", ""))
            paid_usd = float(minimal.get("paid_usd") or 0)
            price_usd = float(minimal.get("price_usd") or 0)

            if not seen_mempool and paid_usd > 0 and status != "completed":
                seen_mempool = True
                log.warn(
                    "Payment detected in mempool",
                    [
                        ("Checkout", checkout_id),
                        ("Paid", f"{paid_usd} USD"),
                    ],
                )

            log.progress(
                "Payment poll",
                [("Attempt", attempt), ("Status", status)],
            )

            if status == "completed":
                log.progress("Payment poll complete", [("Attempts", attempt)], done=True)
                delivered = self.api.get_delivered_items(checkout_id)
                log.success(
                    "Payment completed",
                    [
                        ("Checkout", checkout_id),
                        ("Items", str(len(delivered))),
                    ],
                )
                return delivered

            time.sleep(
                CHECKOUT_CONFIG["payment_mempool_interval_seconds"]
                if seen_mempool
                else poll_interval
            )

        raise TimeoutError(f"Payment was not received within {int(timeout)} seconds.")

    def buy(
        self,
        product_id: int,
        variant_id: int,
        shop_id: int,
        quantity: int = 1,
        currency: str = CHECKOUT_CONFIG["currency"],
        email: Optional[str] = None,
        coupon: Optional[str] = None,
        custom_fields: Optional[Dict[int, Dict[int, str]]] = None,
        wait: bool = True,
        poll_interval: float = CHECKOUT_CONFIG["payment_poll_interval_seconds"],
        timeout: float = CHECKOUT_CONFIG["payment_timeout_seconds"],
    ) -> dict:
        checkout_id = self.create_checkout_with_retry(product_id, variant_id, shop_id, quantity)
        required_fields = self.get_required_custom_fields(checkout_id)
        if required_fields and not custom_fields:
            raise ValueError(
                "This checkout requires custom fields; collect them before calling buy()."
            )
        self.submit_details(checkout_id, currency, email, coupon=coupon)
        if custom_fields:
            self.submit_custom_fields(checkout_id, custom_fields)
        details = self.get_payment_details(checkout_id)
        if wait:
            self.wait_for_payment(checkout_id, poll_interval, timeout)
        return details
