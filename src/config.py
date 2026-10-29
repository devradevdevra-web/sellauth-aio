

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


_RUNTIME_ROOT = os.getenv("SELLAUTH_RUNTIME_DIR")
_PROJECT_ROOT = (
    Path(_RUNTIME_ROOT).expanduser().resolve()
    if _RUNTIME_ROOT
    else Path(__file__).resolve().parent.parent
)
_DATA_DIR = _PROJECT_ROOT / "data"
_CONFIG_PATH = _DATA_DIR / "config.json"
_INTERNAL_MONITORING_STATE_PATH = _DATA_DIR / ".monitoring_state.json"
_ENV_PATH = _PROJECT_ROOT / ".env"
_DEFAULT_CONFIG = {
    "bot": {
        "owner_ids": [],
        "shop_id": None,
        "shop_id_note": "Keep shop_id null to use all shops. Set a SellAuth shop ID to use one shop.",
        "customer_role_name": "Customer",
    },
    "discord": {
        "ticket_category_id": None,
        "support_role_id": None,
        "staff_role_id": None,
    },
    "database": {
        "filename": "orders.sqlite3",
    },
    "automated_checkout": {
        "enabled": False,
        "post_purchase_reward": {
            "enabled": False,
            "discount_percent": 10,
            "max_uses": 1,
            "valid_for_days": 30,
            "code_prefix": "THANKYOU",
            "include_discord_username": True,
            "apply_globally": True,
        },
        "checkout": {
            "currency": "LTC",
            "retry_attempts": 5,
            "retry_delay_seconds": 2.0,
            "retry_delay_cap_seconds": 15.0,
            "payment_poll_interval_seconds": 5.0,
            "payment_mempool_interval_seconds": 3.0,
            "payment_timeout_seconds": 1800.0,
            "payment_poll_concurrency": 20,
            "currency_selection": {
                "enabled": True,
                "allowed": [],
            },
        },
    },
    "logging": {
        "level": "INFO",
        "console": True,
        "color": True,
        "show_fields": True,
    },
    "monitoring": {
        "enabled": False,
        "all_shops": True,
        "auto_create_channels": True,
        "guild_id": None,
        "category_id": None,
        "shop_name": "",
        "orders": {
            "enabled": True,
            "poll_interval_seconds": 60,
            "error_retry_interval_seconds": 10,
            "processed_path": "data/monitoring/processed_orders.json",
            "pending_path": "data/monitoring/pending_orders.json",
        },
        "feedback": {
            "enabled": False,
            "poll_interval_seconds": 60,
            "error_retry_interval_seconds": 10,
            "processed_path": "data/monitoring/processed_feedback.json",
        },
        "out_of_stock": {
            "enabled": False,
            "poll_interval_seconds": 240,
            "error_retry_interval_seconds": 10,
            "processed_path": "data/monitoring/processed_stock_notifications.json",
        },
        "notifications": {
            "sales_channel_id": None,
            "pending_orders_channel_id": None,
            "feedback_channel_id": None,
            "feedback_forum_channel_id": None,
            "stock_warnings_channel_id": None,
        },
    },
}
_ENV_KEYS = {
    "bot_token": "DISCORD_BOT_TOKEN",
    "sellauth_api_key": "SELLAUTH_API_KEY",
    "order_encryption_key": "ORDER_ENCRYPTION_KEY",
}


def _load_dotenv() -> None:
    
    if not _ENV_PATH.exists():
        return
    for raw_line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)


def _ensure_config_file() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not _CONFIG_PATH.exists():
        public_defaults = json.loads(json.dumps(_DEFAULT_CONFIG))
        public_defaults.pop("database", None)
        public_defaults.pop("logging", None)
        monitoring = public_defaults.get("monitoring")
        if isinstance(monitoring, dict):
            for key in (
                "guild_id",
                "category_id",
                "shop_name",
                "shop_id",
                "shops",
                "notifications",
            ):
                monitoring.pop(key, None)
            for section_name in ("orders", "feedback", "out_of_stock"):
                section = monitoring.get(section_name)
                if isinstance(section, dict):
                    section.pop("processed_path", None)
                    section.pop("pending_path", None)
        _CONFIG_PATH.write_text(
            json.dumps(public_defaults, indent=4) + "\n",
            encoding="utf-8",
        )


def load() -> dict:
    
    _ensure_config_file()
    data: dict[str, Any] = {}
    with _CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        loaded = json.load(config_file)
    if not isinstance(loaded, dict):
        raise ValueError("config.json must contain a JSON object")
    data.update(loaded)

    for config_key, environment_key in _ENV_KEYS.items():
        value = os.getenv(environment_key)
        if value:
            data[config_key] = value

    missing = [key for key in _ENV_KEYS if not data.get(key)]
    if missing:
        env_names = ", ".join(_ENV_KEYS[key] for key in missing)
        raise ValueError(
            f"Missing configuration. Set environment variable(s): {env_names}"
        )
    return data


_load_dotenv()
_cfg = load()
BOT_TOKEN = _cfg["bot_token"]
API_KEY = _cfg["sellauth_api_key"]
ORDER_ENCRYPTION_KEY = _cfg["order_encryption_key"]


def _section(name: str) -> dict[str, Any]:
    value = _cfg.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} configuration must be an object")
    return value


def _optional_int(value: Any, name: str = "configuration") -> int | None:
    if value in (None, ""):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if result <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return result


def _bounded_float(value: Any, name: str, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return result


def _bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a whole number") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} must be between {minimum:,} and {maximum:,}")
    return result


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def _load_logging_config(value: Any) -> dict[str, Any]:
    defaults = _DEFAULT_CONFIG["logging"]
    if value is None:
        value = defaults
    if not isinstance(value, dict):
        raise ValueError("logging configuration must be an object")
    settings = {**defaults, **value}
    level = str(settings["level"]).strip().upper()
    if level not in {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARN", "ERROR"}:
        raise ValueError("logging.level must be TRACE, DEBUG, SUCCESS, WARN, or ERROR")

    return {
        "level": level,
        "console": _as_bool(settings["console"]),
        "color": _as_bool(settings["color"]),
        "show_fields": _as_bool(settings["show_fields"]),
    }


LOGGING_CONFIG = _load_logging_config(_cfg.get("logging"))


_bot = _section("bot")
_discord = _section("discord")
_database = _section("database")

TICKET_CATEGORY_ID = _optional_int(
    _discord.get("ticket_category_id", _cfg.get("ticket_category_id")),
    "discord.ticket_category_id",
)
SUPPORT_ROLE_ID = _optional_int(
    _discord.get("support_role_id", _cfg.get("support_role_id")),
    "discord.support_role_id",
)
STAFF_ROLE_ID = _optional_int(
    _discord.get("staff_role_id", _cfg.get("staff_role_id")),
    "discord.staff_role_id",
)
SHOP_ID = _optional_int(_bot.get("shop_id", _cfg.get("shop_id")), "bot.shop_id")


def _resolve_monitor_path(value: Any, default: str) -> str:
    raw = str(value or default).strip()
    path = Path(raw)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return str(path.resolve())


def _monitor_notifications(value: Any, defaults: dict[str, Any], name: str) -> dict[str, int | None]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError(f"{name}.notifications configuration must be an object")
    merged = {**defaults["notifications"], **value}
    return {
        key: _optional_int(merged.get(key), f"{name}.notifications.{key}")
        for key in defaults["notifications"]
    }


def _monitor_shop_bindings(settings: dict[str, Any], defaults: dict[str, Any]) -> dict[str, dict[str, Any]]:
    bindings: dict[str, dict[str, Any]] = {}
    configured = {}
    try:
        internal_state = json.loads(
            _INTERNAL_MONITORING_STATE_PATH.read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        internal_state = {}
    if isinstance(internal_state, dict):
        internal_shops = internal_state.get("shops", internal_state)
        if isinstance(internal_shops, dict):
            configured.update(internal_shops)

    public_shops = settings.get("shops", {})
    if public_shops is None:
        public_shops = {}
    if not isinstance(public_shops, dict):
        raise ValueError("monitoring.shops configuration must be an object")
    configured.update(public_shops)

    for raw_key, raw_value in configured.items():
        if not isinstance(raw_value, dict):
            continue
        try:
            shop_id = _optional_int(raw_value.get("shop_id", raw_key), f"monitoring.shops.{raw_key}.shop_id")
        except ValueError:
            continue
        if shop_id is None:
            continue
        bindings[str(shop_id)] = {
            "shop_id": shop_id,
            "shop_name": str(raw_value.get("shop_name") or "").strip(),
            "guild_id": _optional_int(raw_value.get("guild_id"), f"monitoring.shops.{shop_id}.guild_id"),
            "category_id": _optional_int(raw_value.get("category_id"), f"monitoring.shops.{shop_id}.category_id"),
            "notifications": _monitor_notifications(
                raw_value.get("notifications"), defaults, f"monitoring.shops.{shop_id}"
            ),
        }

    legacy_shop_id = SHOP_ID
    if legacy_shop_id is None:
        legacy_shop_id = _optional_int(settings.get("shop_id"), "monitoring.shop_id")
    if legacy_shop_id is not None and str(legacy_shop_id) not in bindings:
        bindings[str(legacy_shop_id)] = {
            "shop_id": legacy_shop_id,
            "shop_name": str(settings.get("shop_name") or "").strip(),
            "guild_id": _optional_int(settings.get("guild_id"), "monitoring.guild_id"),
            "category_id": _optional_int(settings.get("category_id"), "monitoring.category_id"),
            "notifications": _monitor_notifications(settings.get("notifications"), defaults, "monitoring"),
        }
    return bindings


def _load_monitoring_config(value: Any) -> dict[str, Any]:
    defaults = _DEFAULT_CONFIG["monitoring"]
    if value is None:
        value = defaults
    if not isinstance(value, dict):
        raise ValueError("monitoring configuration must be an object")

    settings = {**defaults, **value}
    shops = _monitor_shop_bindings(settings, defaults)

    monitors: dict[str, dict[str, Any]] = {}
    for name in ("orders", "feedback", "out_of_stock"):
        section_value = settings.get(name, {})
        if not isinstance(section_value, dict):
            raise ValueError(f"monitoring.{name} configuration must be an object")
        section = {**defaults[name], **section_value}
        monitors[name] = {
            "enabled": _as_bool(settings["enabled"]) and _as_bool(section["enabled"]),
            "poll_interval_seconds": _bounded_float(
                section["poll_interval_seconds"],
                f"monitoring.{name}.poll_interval_seconds",
                1,
                86_400,
            ),
            "error_retry_interval_seconds": _bounded_float(
                section["error_retry_interval_seconds"],
                f"monitoring.{name}.error_retry_interval_seconds",
                1,
                3_600,
            ),
            "processed_path": _resolve_monitor_path(
                section.get("processed_path"), defaults[name]["processed_path"]
            ),
        }
        if name == "orders":
            monitors[name]["pending_path"] = _resolve_monitor_path(
                section.get("pending_path"), defaults[name]["pending_path"]
            )

    return {
        "enabled": _as_bool(settings["enabled"]),
        "all_shops": _as_bool(settings.get("all_shops", True)),
        "auto_create_channels": _as_bool(settings.get("auto_create_channels", True)),
        "guild_id": _optional_int(settings.get("guild_id"), "monitoring.guild_id"),
        "category_id": _optional_int(
            settings.get("category_id"), "monitoring.category_id"
        ),
        "shop_name": str(settings.get("shop_name") or "").strip(),
        "shop_id": SHOP_ID if SHOP_ID is not None else _optional_int(settings.get("shop_id"), "monitoring.shop_id"),
        "shops": shops,
        "shop_contexts": {},
        "orders": monitors["orders"],
        "feedback": monitors["feedback"],
        "out_of_stock": monitors["out_of_stock"],
        "notifications": _monitor_notifications(settings.get("notifications"), defaults, "monitoring"),
    }


MONITORING_CONFIG = _load_monitoring_config(_cfg.get("monitoring"))

CUSTOMER_ROLE_NAME = (
    str(
        _bot.get("customer_role_name", _cfg.get("customer_role_name", "Customer"))
    ).strip()
    or "Customer"
)


def _owner_ids() -> frozenset[int]:
    raw = _bot.get("owner_ids", _cfg.get("bot_owner_ids", []))
    if raw is None:
        return frozenset()
    values = set()
    items = (
        raw
        if isinstance(raw, (list, tuple, set))
        else str(raw).replace(",", " ").split()
    )
    for item in items:
        try:
            owner_id = int(item)
        except (TypeError, ValueError):
            continue
        if owner_id > 0:
            values.add(owner_id)
    return frozenset(values)


OWNER_IDS = _owner_ids()

_database_filename = str(_database.get("filename", "orders.sqlite3")).strip()
if _database_filename != "orders.sqlite3":
    raise ValueError(
        "database.filename must remain 'orders.sqlite3' to preserve the existing order database"
    )
DATABASE_CONFIG = {"filename": _database_filename}
ORDER_DB_PATH = str(_DATA_DIR / "orders.sqlite3")


def _load_automated_checkout_config(value: Any) -> dict[str, Any]:
    defaults = _DEFAULT_CONFIG["automated_checkout"]
    if value is None:
        value = defaults
    if not isinstance(value, dict):
        raise ValueError("automated_checkout configuration must be an object")

    settings = {**defaults, **value}
    reward_value = settings.get("post_purchase_reward")
    if not isinstance(reward_value, dict):
        raise ValueError("automated_checkout.post_purchase_reward must be an object")
    reward = {**defaults["post_purchase_reward"], **reward_value}

    discount_percent = _bounded_float(
        reward["discount_percent"],
        "automated_checkout.post_purchase_reward.discount_percent",
        0.01,
        100,
    )
    max_uses = _bounded_int(
        reward["max_uses"],
        "automated_checkout.post_purchase_reward.max_uses",
        1,
        100_000,
    )
    valid_for_days = _bounded_int(
        reward["valid_for_days"],
        "automated_checkout.post_purchase_reward.valid_for_days",
        1,
        3_650,
    )
    code_prefix = str(reward["code_prefix"]).strip().upper()
    if (
        not code_prefix
        or len(code_prefix) > 24
        or not all(
            character.isalnum() or character in "-_" for character in code_prefix
        )
    ):
        raise ValueError(
            "automated_checkout.post_purchase_reward.code_prefix must contain only letters, numbers, '-' or '_'"
        )

    return {
        "enabled": _as_bool(settings["enabled"]) and _as_bool(reward["enabled"]),
        "discount_percent": discount_percent,
        "max_uses": max_uses,
        "valid_for_days": valid_for_days,
        "code_prefix": code_prefix,
        "include_discord_username": _as_bool(reward["include_discord_username"]),
        "apply_globally": _as_bool(reward["apply_globally"]),
    }


AUTOMATED_CHECKOUT_CONFIG = _load_automated_checkout_config(
    _cfg.get("automated_checkout")
)


def _load_checkout_config(value: Any) -> dict[str, Any]:
    defaults = _DEFAULT_CONFIG["automated_checkout"]["checkout"]
    if value is None:
        value = defaults
    if not isinstance(value, dict):
        raise ValueError("checkout configuration must be an object")
    settings = {**defaults, **value}

    currency = str(settings["currency"]).strip().upper()
    if not currency.isalpha() or not 2 <= len(currency) <= 10:
        raise ValueError("checkout.currency must be a 2-10 letter currency code")

    retry_attempts = _bounded_int(
        settings["retry_attempts"], "checkout.retry_attempts", 1, 20
    )
    retry_delay = _bounded_float(
        settings["retry_delay_seconds"], "checkout.retry_delay_seconds", 0, 600
    )
    retry_delay_cap = _bounded_float(
        settings["retry_delay_cap_seconds"],
        "checkout.retry_delay_cap_seconds",
        0,
        3_600,
    )
    if retry_delay_cap < retry_delay:
        raise ValueError(
            "checkout.retry_delay_cap_seconds cannot be less than retry_delay_seconds"
        )

    poll_interval = _bounded_float(
        settings["payment_poll_interval_seconds"],
        "checkout.payment_poll_interval_seconds",
        0.1,
        3_600,
    )
    mempool_interval = _bounded_float(
        settings["payment_mempool_interval_seconds"],
        "checkout.payment_mempool_interval_seconds",
        0.1,
        3_600,
    )
    timeout = _bounded_float(
        settings["payment_timeout_seconds"],
        "checkout.payment_timeout_seconds",
        1,
        86_400,
    )
    concurrency = _bounded_int(
        settings["payment_poll_concurrency"],
        "checkout.payment_poll_concurrency",
        1,
        1_000,
    )

    selection = settings.get("currency_selection", {})
    if not isinstance(selection, dict):
        raise ValueError("checkout.currency_selection configuration must be an object")
    selection_enabled = _as_bool(selection.get("enabled", True))
    raw_allowed = selection.get("allowed", [])
    if raw_allowed is None:
        raw_allowed = []
    if isinstance(raw_allowed, str):
        raw_allowed = [raw_allowed]
    if not isinstance(raw_allowed, (list, tuple, set)):
        raise ValueError(
            "checkout.currency_selection.allowed must be a list of currency codes"
        )

    allowed_currencies = []
    for item in raw_allowed:
        allowed_currency = str(item).strip().upper()
        if not allowed_currency.isalpha() or not 2 <= len(allowed_currency) <= 10:
            raise ValueError(
                "checkout.currency_selection.allowed contains an invalid currency code"
            )
        if allowed_currency not in allowed_currencies:
            allowed_currencies.append(allowed_currency)

    return {
        "currency": currency,
        "retry_attempts": retry_attempts,
        "retry_delay_seconds": retry_delay,
        "retry_delay_cap_seconds": retry_delay_cap,
        "payment_poll_interval_seconds": poll_interval,
        "payment_mempool_interval_seconds": mempool_interval,
        "payment_timeout_seconds": timeout,
        "payment_poll_concurrency": concurrency,
        "currency_selection_enabled": selection_enabled,
        "allowed_currencies": allowed_currencies,
    }


_automated_checkout_settings = _cfg.get("automated_checkout")
if isinstance(_automated_checkout_settings, dict):
    _checkout_settings = _automated_checkout_settings.get("checkout")
else:
    _checkout_settings = None
if _checkout_settings is None:
    _checkout_settings = _cfg.get("checkout")
CHECKOUT_CONFIG = _load_checkout_config(_checkout_settings)


def persist_monitoring_state(
    *,
    shop_id: int | None = None,
    shop_name: str | None = None,
    guild_id: int | None = None,
    category_id: int | None = None,
    channel_ids: dict[str, int] | None = None,
) -> None:
    if shop_id is None:
        return
    key = str(int(shop_id))
    try:
        loaded = json.loads(
            _INTERNAL_MONITORING_STATE_PATH.read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        loaded = {}
    if not isinstance(loaded, dict):
        loaded = {}
    shops = loaded.setdefault("shops", {})
    if not isinstance(shops, dict):
        shops = {}
        loaded["shops"] = shops
    current = shops.get(key)
    if not isinstance(current, dict):
        current = {}
    current["shop_id"] = int(shop_id)
    if shop_name is not None:
        current["shop_name"] = str(shop_name)
    if guild_id is not None:
        current["guild_id"] = int(guild_id)
    if category_id is not None:
        current["category_id"] = int(category_id)
    if channel_ids:
        notifications = current.setdefault("notifications", {})
        if isinstance(notifications, dict):
            notifications.update(channel_ids)
    shops[key] = current

    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=_DATA_DIR,
        prefix=".monitoring-state.", suffix=".tmp", delete=False
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(loaded, handle, indent=4)
            handle.write("\n")
        os.replace(temporary, _INTERNAL_MONITORING_STATE_PATH)
    finally:
        if temporary.exists():
            temporary.unlink()

    record = {
        "shop_id": int(shop_id),
        "shop_name": str(shop_name or current.get("shop_name") or f"Shop {key}"),
        "guild_id": guild_id or current.get("guild_id"),
        "category_id": category_id or current.get("category_id"),
        "notifications": {
            **(current.get("notifications") or {}),
            **(channel_ids or {}),
        },
    }
    MONITORING_CONFIG.setdefault("shops", {})[key] = record
