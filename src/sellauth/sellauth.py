

from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import requests

import src.logger.logger as log


_UNLIMITED_STOCK_VALUES = {"-1", "infinite", "infinity", "unlimited", "∞"}


def _normalize_stock(value: Any) -> Optional[int]:
    
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if not normalized or normalized in _UNLIMITED_STOCK_VALUES:
            return None
        value = normalized
    try:
        stock = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return None if stock < 0 else stock


class SellAuthError(RuntimeError):
    

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SellAuthAPI:
    PUBLIC_API_BASE = "https://api.sellauth.com/v1"
    INTERNAL_API_BASE = "https://api-internal.sellauth.com/v1"
    CHECKOUT_API_BASE = "https://api-internal-3.sellauth.com/v1"
    DEFAULT_TIMEOUT = 30.0
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/143.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        shop_origin: Optional[str] = None,
        api_key: Optional[str] = None,
        proxy: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.session = session or requests.Session()
        self.shop_origin = ""

        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})
        if shop_origin:
            self._set_origin(shop_origin)

    def close(self) -> None:
        self.session.close()

    def _set_origin(self, origin: str) -> None:
        parsed = urlsplit(origin.rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Invalid shop origin: {origin!r}")
        self.shop_origin = f"{parsed.scheme}://{parsed.netloc}"

    def _headers(self, json_body: bool = False, authenticated: bool = False) -> dict:
        headers = {
            "accept": "application/json, */*",
            "accept-encoding": "gzip, deflate, br",
            "accept-language": "en-US,en;q=0.9",
            "user-agent": self.USER_AGENT,
        }
        if self.shop_origin:
            headers["origin"] = self.shop_origin
            headers["referer"] = f"{self.shop_origin}/"
        if json_body:
            headers["content-type"] = "application/json"
        if authenticated:
            if not self.api_key:
                raise SellAuthError(
                    "A SellAuth API key is required for this operation."
                )
            headers["authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: bool = False,
        authenticated: bool = False,
        suppress_error_log: bool = False,
        **kwargs: Any,
    ) -> Any:
        kwargs.setdefault("timeout", self.timeout)
        headers = self._headers(json_body, authenticated)
        headers.update(kwargs.pop("headers", {}) or {})
        kwargs["headers"] = headers
        try:
            response = self.session.request(method, url, **kwargs)
            response.raise_for_status()
        except requests.RequestException as exc:
            detail = (
                getattr(exc.response, "text", "")[:500]
                if exc.response is not None
                else str(exc)
            )
            status_code = (
                getattr(exc.response, "status_code", None)
                if exc.response is not None
                else None
            )
            safe_endpoint = urlsplit(url).path or "/"
            if not suppress_error_log:
                log.error(
                    "SellAuth request failed",
                    [
                        ("Method", method.upper()),
                        ("Endpoint", safe_endpoint),
                        ("Status", status_code or "network"),
                        ("Exception", type(exc).__name__),
                    ],
                )
            raise SellAuthError(
                f"SellAuth {method} {safe_endpoint} failed: {detail}",
                status_code=status_code,
            ) from exc

        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise SellAuthError(
                f"SellAuth returned invalid JSON for {method} {url}."
            ) from exc

    def _checkout_request(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._request(
            method, f"{self.CHECKOUT_API_BASE}/{path.lstrip('/')}", **kwargs
        )


    def fetch_shops(self) -> list:
        data = self._request("GET", f"{self.PUBLIC_API_BASE}/shops", authenticated=True)
        if not isinstance(data, list):
            raise SellAuthError("SellAuth shops response was not a list.")
        return data

    def fetch_products(self, shop_id: int) -> dict:
        data = self._request(
            "GET",
            f"{self.PUBLIC_API_BASE}/shops/{shop_id}/products",
            authenticated=True,
        )
        products = {}
        for product in data.get("data", []) if isinstance(data, dict) else []:
            name = product.get("name", "Unnamed Product")
            variants = product.get("variants", [])
            products[name] = {
                "product_id": product.get("id"),
                "variant_ids": [variant.get("id") for variant in variants],
            }
        return products


    @staticmethod
    def _solve_altcha(payload: Dict[str, Any]) -> Dict[str, Any]:
        started = time.time()
        algorithm = payload.get("algorithm", "SHA-256")
        if algorithm != "SHA-256":
            raise SellAuthError(f"Unsupported Altcha algorithm: {algorithm}")

        try:
            challenge = payload["challenge"]
            salt = payload["salt"]
            signature = payload["signature"]
            max_number = int(payload.get("maxnumber", 100_000))
        except (KeyError, TypeError, ValueError) as exc:
            raise SellAuthError(
                "SellAuth returned an invalid Altcha challenge."
            ) from exc

        for number in range(max_number + 1):
            digest = hashlib.sha256(f"{salt}{number}".encode()).hexdigest()
            if digest == challenge:
                return {
                    "algorithm": algorithm,
                    "challenge": challenge,
                    "number": number,
                    "salt": salt,
                    "signature": signature,
                    "took": int((time.time() - started) * 1000),
                }
        raise SellAuthError("Altcha solution was not found within maxnumber.")

    def _get_altcha(self) -> str:
        payload = self._checkout_request("GET", "altcha")
        solved = self._solve_altcha(payload)
        return base64.b64encode(
            json.dumps(solved, separators=(",", ":")).encode()
        ).decode()

    def get_supported_currencies(self, checkout_id: str) -> list[str]:
        
        data = self.get_checkout_full(checkout_id)
        methods = data.get("shop", {}).get("payment_methods", [])
        currencies = []
        for method in methods:
            currency = str(method.get("type") or "").strip().upper()
            if currency and currency not in currencies:
                currencies.append(currency)
        return currencies

    def _find_payment_method(self, checkout_id: str, currency: str = "LTC") -> int:
        data = self.get_checkout_full(checkout_id)
        invoice_url = data.get("invoice", {}).get("url", "")
        if invoice_url:
            parsed = urlsplit(invoice_url)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                self._set_origin(f"{parsed.scheme}://{parsed.netloc}")

        methods = data.get("shop", {}).get("payment_methods", [])
        for method in methods:
            if method.get("type", "").upper() == currency.upper():
                log.info(
                    "Payment method found",
                    [("Type", currency), ("ID", str(method["id"]))],
                )
                return method["id"]
        raise SellAuthError(f"No {currency} payment method for this shop.")

    def create_checkout(
        self, product_id: int, variant_id: int, shop_id: int, quantity: int = 1
    ) -> str:
        if quantity < 1:
            raise ValueError("quantity must be at least 1")
        data = self._checkout_request(
            "POST",
            "checkout",
            json={
                "cart": [
                    {
                        "productId": product_id,
                        "variantId": variant_id,
                        "quantity": quantity,
                    }
                ],
                "shopId": shop_id,
                "affiliate": None,
                "metadata": None,
                "altcha": self._get_altcha(),
            },
            json_body=True,
        )
        if not isinstance(data, dict) or not data.get("success") or not data.get("url"):
            raise SellAuthError(f"Checkout creation failed: {data}")
        checkout_id = str(data["url"]).rstrip("/").split("/")[-1]
        log.success("Checkout created", [("ID", checkout_id), ("Qty", str(quantity))])
        return checkout_id

    def get_checkout_full(self, checkout_id: str) -> dict:
        data = self._checkout_request("GET", f"checkout/{checkout_id}/full")
        if not isinstance(data, dict):
            raise SellAuthError("SellAuth checkout response was not an object.")
        return data

    def get_required_custom_fields(self, checkout_id: str) -> list:
        data = self.get_checkout_full(checkout_id)
        required = []
        for item in data.get("invoice", {}).get("items", []):
            for field in item.get("product", {}).get("custom_fields", []):
                if field.get("is_required"):
                    required.append(
                        {
                            "item_id": item.get("id"),
                            "field_id": field["id"],
                            "name": field["name"],
                        }
                    )
        if required:
            log.info(
                "Required custom fields",
                [
                    ("Checkout", checkout_id),
                    ("Fields", ", ".join(field["name"] for field in required)),
                ],
            )
        return required

    def submit_details(
        self,
        checkout_id: str,
        email: str,
        currency: str = "LTC",
        coupon: Optional[str] = None,
    ) -> dict:
        if not email or "@" not in email:
            raise ValueError("A valid email is required for checkout details.")
        data = self._checkout_request(
            "PUT",
            f"checkout/{checkout_id}",
            json={
                "email": email,
                "discord_user_id": "",
                "discord_user_username": "",
                "discord_access_token": "",
                "coupon": str(coupon or "").strip().upper(),
                "payment_method_id": self._find_payment_method(checkout_id, currency),
                "billing_name": "",
                "billing_address_line1": "",
                "billing_address_line2": "",
                "billing_city": "",
                "billing_state": "",
                "billing_postcode": "",
                "billing_country": "",
                "shipping_name": "",
                "shipping_address_line1": "",
                "shipping_address_line2": "",
                "shipping_city": "",
                "shipping_state": "",
                "shipping_postcode": "",
                "shipping_country": "",
                "shipping_phone": "",
                "terms": True,
                "newsletter": False,
                "altcha": self._get_altcha(),
                "shipping_zone_id": None,
            },
            json_body=True,
        )
        if not isinstance(data, dict) or not data.get("success"):
            raise SellAuthError(f"Details update failed: {data}")
        return data

    def submit_custom_fields(
        self, checkout_id: str, field_values: Dict[int, Dict[int, str]]
    ) -> dict:
        custom_fields = {
            str(item_id): {str(field_id): value for field_id, value in fields.items()}
            for item_id, fields in field_values.items()
        }
        data = self._checkout_request(
            "PUT",
            f"checkout/{checkout_id}",
            json={"altcha": self._get_altcha(), "custom_fields": custom_fields},
            json_body=True,
        )
        if not isinstance(data, dict) or not data.get("success"):
            raise SellAuthError(f"Custom fields update failed: {data}")
        log.success("Custom fields accepted", [("Checkout", checkout_id)])
        return data

    def get_payment_details(self, checkout_id: str) -> dict:
        data = self._checkout_request("GET", f"checkout/{checkout_id}/minimal")
        if not isinstance(data, dict):
            raise SellAuthError("SellAuth payment response was not an object.")
        return data

    def get_delivered_items(self, checkout_id: str) -> list:
        data = self.get_checkout_full(checkout_id)
        delivered = []
        for item in data.get("invoice", {}).get("items", []):
            delivered.extend(item.get("delivered", []))
        return delivered

    def get_payment_status(self, checkout_id: str) -> dict:
        return self.get_payment_details(checkout_id)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        body: Any = None,
        form: Optional[dict] = None,
        files: Any = None,
        headers: Optional[dict] = None,
        suppress_error_log: bool = False,
    ) -> Any:
        
        clean_path = path.strip()
        if clean_path.startswith("http://") or clean_path.startswith("https://"):
            raise ValueError("path must be relative to the SellAuth API")
        if clean_path.startswith("/v1/"):
            clean_path = clean_path[3:]
        elif clean_path == "/v1":
            clean_path = ""

        kwargs: Dict[str, Any] = {}
        if params is not None:
            kwargs["params"] = params
        if body is not None:
            kwargs["json"] = body
        if form is not None:
            kwargs["data"] = form
        if files is not None:
            kwargs["files"] = files
        return self._request(
            method.upper(),
            f"{self.PUBLIC_API_BASE}/{clean_path.lstrip('/')}",
            json_body=body is not None and form is None and files is None,
            authenticated=True,
            suppress_error_log=suppress_error_log,
            headers=headers,
            **kwargs,
        )

    def get_user(self) -> dict:
        return self.request("GET", "/user")

    def get_shops(self) -> list:
        return self.fetch_shops()

    def get_shop(self, shop_id: int) -> dict:
        return self.request("GET", f"/shops/{shop_id}")

    def create_shop(self, name: str, subdomain: str, *, logo: Any = None) -> dict:
        files = {"logo": logo} if logo is not None else None
        return self.request(
            "POST",
            "/shops/create",
            form={"name": name, "subdomain": subdomain},
            files=files,
        )

    def update_shop(self, shop_id: int, **fields: Any) -> Any:
        return self.request("PUT", f"/shops/{shop_id}/update", body=fields)

    def delete_shop(self, shop_id: int, *, name: Optional[str] = None) -> Any:
        body = {"name": name} if name is not None else None
        return self.request("DELETE", f"/shops/{shop_id}", body=body)

    def get_shop_stats(self, shop_id: int) -> dict:
        return self.request("GET", f"/shops/{shop_id}/stats")

    def list_products(self, shop_id: int, **filters: Any) -> dict:
        return self.request(
            "GET",
            f"/shops/{shop_id}/products",
            params=filters or None,
        )

    def get_product(self, shop_id: int, product_id: int) -> dict:
        return self.request("GET", f"/shops/{shop_id}/products/{product_id}")

    def get_variant_stock(
        self, shop_id: int, product_id: int, variant_id: int
    ) -> Optional[int]:
        
        product = self.get_product(shop_id, product_id)
        variants = product.get("variants") or []
        for variant in variants:
            try:
                matches = int(variant.get("id")) == int(variant_id)
            except (TypeError, ValueError):
                matches = False
            if not matches:
                continue
            stock = variant.get("stock")
            if stock in (None, ""):
                stock = variant.get("stock_count")
            if stock in (None, ""):
                stock = product.get("stock_count")
            if stock in (None, ""):
                stock = product.get("stock")
            return _normalize_stock(stock)
        return None

    def get_default_variant_id(self, shop_id: int, product_id: int) -> int:
        
        product = self.get_product(shop_id, product_id)
        variants = product.get("variants") or []
        for variant in variants:
            if variant.get("id") is not None and not variant.get("disabled", False):
                return self._positive_id(variant["id"], "variant_id")
        raise SellAuthError(f"Product {product_id} has no usable variants.")

    def create_product(self, shop_id: int, **product: Any) -> Any:
        return self.request("POST", f"/shops/{shop_id}/products", body=product)

    def update_product(self, shop_id: int, product_id: int, **fields: Any) -> Any:
        return self.request(
            "PUT",
            f"/shops/{shop_id}/products/{product_id}/update",
            body=fields,
        )

    def delete_product(self, shop_id: int, product_id: int) -> Any:
        return self.request("DELETE", f"/shops/{shop_id}/products/{product_id}")

    def list_invoices(
        self,
        shop_id: int,
        *,
        suppress_error_log: bool = False,
        **filters: Any,
    ) -> dict:
        return self.request(
            "GET",
            f"/shops/{shop_id}/invoices",
            params=filters or None,
            suppress_error_log=suppress_error_log,
        )

    def get_invoice(
        self,
        shop_id: int,
        invoice_id: str | int,
        *,
        suppress_error_log: bool = False,
    ) -> dict:
        return self.request(
            "GET",
            f"/shops/{shop_id}/invoices/{invoice_id}",
            suppress_error_log=suppress_error_log,
        )

    def list_feedbacks(
        self,
        shop_id: int,
        *,
        suppress_error_log: bool = False,
        **filters: Any,
    ) -> Any:
        return self.request(
            "GET",
            f"/shops/{shop_id}/feedbacks",
            params=filters or None,
            suppress_error_log=suppress_error_log,
        )

    def get_latest_notifications(
        self,
        shop_id: int,
        *,
        suppress_error_log: bool = False,
    ) -> Any:
        shop_id = self._positive_id(shop_id, "shop_id")
        return self.request(
            "GET",
            f"/shops/{shop_id}/notifications/latest",
            suppress_error_log=suppress_error_log,
        )

    def create_checkout_session(self, shop_id: int, cart: list, **options: Any) -> dict:
        
        payload = {"cart": cart, **options}
        return self.request("POST", f"/shops/{shop_id}/checkout", body=payload)


    def create_coupon(self, shop_id: int, **coupon: Any) -> dict:
        return self.request("POST", f"/shops/{shop_id}/coupons", body=coupon)

    def list_coupons(self, shop_id: int, **filters: Any) -> dict:
        return self.request(
            "GET",
            f"/shops/{shop_id}/coupons",
            body=filters or None,
        )

    def get_coupon(self, shop_id: int, coupon_id: int | str) -> dict:
        
        return self.request("GET", f"/shops/{shop_id}/coupons/{coupon_id}")

    def delete_coupon(self, shop_id: int, coupon_id: int | str) -> Any:
        
        return self.request("DELETE", f"/shops/{shop_id}/coupons/{coupon_id}")

    def delete_used_coupons(self, shop_id: int) -> Any:
        
        return self.request("DELETE", f"/shops/{shop_id}/coupons/used")

    def update_coupon(self, shop_id: int, coupon_id: int | str, **coupon: Any) -> Any:
        
        return self.request(
            "PUT",
            f"/shops/{shop_id}/coupons/{coupon_id}/update",
            body=coupon,
        )

    @staticmethod
    def _positive_id(value: int | str, name: str) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be an integer.") from exc
        if parsed < 1:
            raise ValueError(f"{name} must be greater than zero.")
        return parsed

    def get_balance(self, shop_id: int) -> dict:
        
        shop_id = self._positive_id(shop_id, "shop_id")
        data = self.request("GET", f"/shops/{shop_id}/payouts/balances")
        if not isinstance(data, dict):
            raise SellAuthError("SellAuth balance response was not an object.")
        return data

    def list_blacklist(self, shop_id: int, **filters: Any) -> Any:
        shop_id = self._positive_id(shop_id, "shop_id")
        return self.request("GET", f"/shops/{shop_id}/blacklist", body=filters or None)

    def create_blacklist(
        self,
        shop_id: int,
        *,
        blacklist_type: str,
        value: str,
        reason: str,
        match_type: str = "exact",
    ) -> Any:
        shop_id = self._positive_id(shop_id, "shop_id")
        allowed_types = {"user_agent", "email", "ip"}
        if blacklist_type not in allowed_types:
            raise ValueError(
                f"blacklist_type must be one of: {', '.join(sorted(allowed_types))}."
            )
        value = str(value).strip()
        reason = str(reason).strip()
        if not value or len(value) > 320:
            raise ValueError("Blacklist value must contain 1-320 characters.")
        if not reason or len(reason) > 500:
            raise ValueError("Blacklist reason must contain 1-500 characters.")
        if match_type not in {"exact", "contains", "starts_with", "ends_with"}:
            raise ValueError("Unsupported blacklist match type.")
        return self.request(
            "POST",
            f"/shops/{shop_id}/blacklist",
            body={
                "match_type": match_type,
                "reason": reason,
                "type": blacklist_type,
                "value": value,
            },
        )

    def delete_blacklist(self, shop_id: int, blacklist_id: int | str) -> Any:
        shop_id = self._positive_id(shop_id, "shop_id")
        blacklist_id = self._positive_id(blacklist_id, "blacklist_id")
        return self.request("DELETE", f"/shops/{shop_id}/blacklist/{blacklist_id}")

    def reply_feedback(self, shop_id: int, feedback_id: int | str, reply: str) -> Any:
        shop_id = self._positive_id(shop_id, "shop_id")
        feedback_id = self._positive_id(feedback_id, "feedback_id")
        reply = str(reply).strip()
        if not reply or len(reply) > 2000:
            raise ValueError("Feedback reply must contain 1-2000 characters.")
        return self.request(
            "POST",
            f"/shops/{shop_id}/feedbacks/{feedback_id}/reply",
            body={"reply": reply},
        )

    def process_invoice(self, shop_id: int, invoice_id: int | str) -> Any:
        
        shop_id = self._positive_id(shop_id, "shop_id")
        invoice_id = self._positive_id(invoice_id, "invoice_id")
        return self.request("GET", f"/shops/{shop_id}/invoices/{invoice_id}/process")

    def get_blockchain_transaction(self, transaction_id: str) -> dict:
        
        transaction_id = str(transaction_id).strip()
        if (
            not transaction_id
            or len(transaction_id) > 128
            or not all(character.isalnum() for character in transaction_id)
        ):
            raise ValueError(
                "transaction_id must be a valid hexadecimal transaction ID."
            )
        data = self._request(
            "GET",
            f"https://api.blockcypher.com/v1/ltc/main/txs/{transaction_id}",
            authenticated=False,
        )
        if not isinstance(data, dict):
            raise SellAuthError("Blockchain response was not an object.")
        return data

    def get_deliverables(self, shop_id: int, product_id: int, variant_id: int) -> list:
        shop_id = self._positive_id(shop_id, "shop_id")
        product_id = self._positive_id(product_id, "product_id")
        variant_id = self._positive_id(variant_id, "variant_id")
        data = self.request(
            "GET", f"/shops/{shop_id}/products/{product_id}/deliverables/{variant_id}"
        )
        if isinstance(data, dict):
            data = data.get("deliverables", data.get("data", []))
        if not isinstance(data, list):
            raise SellAuthError("SellAuth deliverables response was not a list.")
        return data

    def append_deliverables(
        self, shop_id: int, product_id: int, variant_id: int, deliverables: list[str]
    ) -> Any:
        shop_id = self._positive_id(shop_id, "shop_id")
        product_id = self._positive_id(product_id, "product_id")
        variant_id = self._positive_id(variant_id, "variant_id")
        if not deliverables or len(deliverables) > 10000:
            raise ValueError("Provide between 1 and 10,000 deliverables.")
        return self.request(
            "PUT",
            f"/shops/{shop_id}/products/{product_id}/deliverables/append/{variant_id}",
            body={"deliverables": deliverables},
        )

    def overwrite_deliverables(
        self, shop_id: int, product_id: int, variant_id: int, deliverables: list[str]
    ) -> Any:
        shop_id = self._positive_id(shop_id, "shop_id")
        product_id = self._positive_id(product_id, "product_id")
        variant_id = self._positive_id(variant_id, "variant_id")
        if len(deliverables) > 10000:
            raise ValueError("A maximum of 10,000 deliverables is supported.")
        return self.request(
            "PUT",
            f"/shops/{shop_id}/products/{product_id}/deliverables/overwrite/{variant_id}",
            body={"deliverables": deliverables},
        )


class SellAuthResellerAPI(SellAuthAPI):
    

    RESELLER_API_BASE = "https://api.sellauth.com/v1/reseller"

    def __init__(self, reseller_api_key: str, **kwargs: Any) -> None:
        super().__init__(api_key=reseller_api_key, **kwargs)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        body: Any = None,
        headers: Optional[dict] = None,
    ) -> Any:
        clean_path = path.strip()
        if clean_path.startswith("http://") or clean_path.startswith("https://"):
            raise ValueError("path must be relative to the SellAuth reseller API")
        if clean_path.startswith("/v1/reseller/"):
            clean_path = clean_path[len("/v1/reseller/") :]
        elif clean_path.startswith("/reseller/"):
            clean_path = clean_path[len("/reseller/") :]

        kwargs: Dict[str, Any] = {}
        if params is not None:
            kwargs["params"] = params
        if body is not None:
            kwargs["json"] = body
        return self._request(
            method.upper(),
            f"{self.RESELLER_API_BASE}/{clean_path.lstrip('/')}",
            json_body=body is not None,
            authenticated=True,
            headers=headers,
            **kwargs,
        )

    def list_products(self, *, page: int = 1, per_page: int = 20) -> dict:
        return self.request(
            "GET",
            "/products",
            params={"page": page, "perPage": per_page},
        )

    def get_balance(self) -> dict:
        return self.request("GET", "/balance")

    def list_invoices(self, *, page: int = 1, per_page: int = 20) -> dict:
        return self.request(
            "GET",
            "/invoices",
            params={"page": page, "perPage": per_page},
        )

    def get_invoice(self, invoice_id: str | int) -> dict:
        return self.request("GET", f"/invoices/{invoice_id}")

    def create_invoice(
        self,
        items: list,
        *,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        request_headers = {}
        if idempotency_key:
            request_headers["Idempotency-Key"] = idempotency_key
        return self.request(
            "POST",
            "/invoices",
            body={"items": items},
            headers=request_headers,
        )
