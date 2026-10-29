from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import discord

import src.logger.logger as log
from src.config import (
    MONITORING_CONFIG,
    STAFF_ROLE_ID,
    SUPPORT_ROLE_ID,
    persist_monitoring_state,
)


def _monitoring_overwrites(guild: discord.Guild, bot: discord.Bot) -> dict:
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }

    bot_member = guild.me
    if bot_member is None and getattr(bot, "user", None) is not None:
        bot_member = guild.get_member(bot.user.id)
    if bot_member is not None:
        overwrites[bot_member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            manage_messages=True,
        )

    for role_id in (SUPPORT_ROLE_ID, STAFF_ROLE_ID):
        if not role_id:
            continue
        role = guild.get_role(role_id)
        if role is not None:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )
    return overwrites


def response_rows(data: Any, preferred_key: str = "data") -> list[dict]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in (preferred_key, "data", "items", "results", "notifications"):
        rows = data.get(key)
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, dict)]
    return []


def _read_state(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def load_ids(path_value: str, shop_id: int | str) -> set[str]:
    loaded = _read_state(Path(path_value))
    key = str(shop_id)
    if isinstance(loaded, list):
        legacy_key = str(MONITORING_CONFIG.get("shop_id") or shop_id)
        return {str(item) for item in loaded if str(shop_id) == legacy_key and item not in (None, "")}
    if isinstance(loaded, dict):
        if isinstance(loaded.get("ids"), list):
            legacy_key = str(MONITORING_CONFIG.get("shop_id") or shop_id)
            return {str(item) for item in loaded["ids"] if str(shop_id) == legacy_key and item not in (None, "")}
        values = loaded.get(key, [])
        if isinstance(values, list):
            return {str(item) for item in values if item not in (None, "")}
    return set()


def save_ids(path_value: str, shop_id: int | str, values: set[str]) -> None:
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    loaded = _read_state(path)
    if isinstance(loaded, list):
        legacy_key = str(MONITORING_CONFIG.get("shop_id") or shop_id)
        state: dict[str, list[str]] = {legacy_key: [str(item) for item in loaded]}
    elif isinstance(loaded, dict) and isinstance(loaded.get("ids"), list):
        legacy_key = str(MONITORING_CONFIG.get("shop_id") or shop_id)
        state = {legacy_key: [str(item) for item in loaded["ids"]]}
    elif isinstance(loaded, dict):
        state = {
            str(key): [str(item) for item in value]
            for key, value in loaded.items()
            if isinstance(value, list)
        }
    else:
        state = {}
    state[str(shop_id)] = sorted({str(item) for item in values if item not in (None, "")})

    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp", delete=False
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


async def resolve_channel(bot: discord.Bot, channel_id: int | None):
    if not channel_id:
        return None
    channel = bot.get_channel(channel_id)
    if channel is not None:
        return channel
    try:
        return await bot.fetch_channel(channel_id)
    except (discord.DiscordException, ValueError, TypeError):
        return None


def monitor_enabled(name: str) -> bool:
    return bool(MONITORING_CONFIG["enabled"] and MONITORING_CONFIG[name]["enabled"])


def monitoring_contexts() -> list[dict[str, Any]]:
    contexts = MONITORING_CONFIG.get("shop_contexts", {})
    return list(contexts.values()) if isinstance(contexts, dict) else []


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return result[:40] or "shop"


def _channel_names(shop_id: int, shop_name: str) -> dict[str, str]:
    prefix = f"sellauth-{_slug(shop_name)}-{shop_id}"
    return {
        "sales_channel_id": f"{prefix}-sales"[:100],
        "pending_orders_channel_id": f"{prefix}-pending"[:100],
        "feedback_channel_id": f"{prefix}-feedback"[:100],
        "feedback_forum_channel_id": f"{prefix}-feedback-discussion"[:100],
        "stock_warnings_channel_id": f"{prefix}-out-of-stock"[:100],
    }


async def _resolve_shop_channels(
    bot: discord.Bot,
    shop_id: int,
    shop_name: str,
    configured: dict[str, Any],
    legacy: bool,
    force_new_keys: set[str] | None = None,
) -> tuple[int | None, int | None, dict[str, int]]:
    guild_id = configured.get("guild_id")
    category_id = configured.get("category_id")
    notifications = dict(configured.get("notifications") or {})
    if legacy:
        guild_id = guild_id or MONITORING_CONFIG.get("guild_id")
        category_id = category_id or MONITORING_CONFIG.get("category_id")
        for key, value in MONITORING_CONFIG.get("notifications", {}).items():
            notifications.setdefault(key, value)

    guild = bot.get_guild(guild_id) if guild_id else None
    if guild is None and bot.guilds:
        guild = bot.guilds[0]
        guild_id = guild.id
    if guild is None:
        log.warn("Monitoring channels cannot be created; bot is not in a guild", [("Shop", shop_id)])
        return guild_id, category_id, {}

    overwrites = _monitoring_overwrites(guild, bot)
    category = await resolve_channel(bot, category_id) if category_id else None
    if not isinstance(category, discord.CategoryChannel):
        category_name = f"SellAuth Monitoring - {shop_name} ({shop_id})"
        category = next(
            (item for item in guild.categories if item.name.casefold() == category_name.casefold()),
            None,
        )
    if category is None and legacy:
        category = next(
            (item for item in guild.categories if item.name.casefold() == "sellauth monitoring"),
            None,
        )
    if category is None and MONITORING_CONFIG.get("auto_create_channels", True):
        try:
            category = await guild.create_category(
                f"SellAuth Monitoring - {shop_name} ({shop_id})",
                overwrites=overwrites,
            )
        except discord.DiscordException as exc:
            log.exception("Could not create monitoring category", exc, [("Shop", shop_id)])
            return guild_id, category_id, {}
    if category is None:
        log.warn("Monitoring category is not configured", [("Shop", shop_id)])
        return guild_id, category_id, {}
    category_id = category.id
    try:
        await category.edit(
            overwrites=overwrites,
            reason="Keep SellAuth monitoring private to staff and administrators",
        )
    except discord.DiscordException:
        pass

    channel_ids: dict[str, int] = {}
    force_new_keys = force_new_keys or set()
    names = _channel_names(shop_id, shop_name)
    active_keys = {"sales_channel_id", "pending_orders_channel_id"}
    if monitor_enabled("feedback"):
        active_keys.update({"feedback_channel_id", "feedback_forum_channel_id"})
    if monitor_enabled("out_of_stock"):
        active_keys.add("stock_warnings_channel_id")
    for key, channel_name in names.items():
        if key not in active_keys:
            continue
        channel = None
        if key not in force_new_keys:
            channel = await resolve_channel(bot, notifications.get(key)) if notifications.get(key) else None
        if channel is not None and getattr(getattr(channel, "guild", None), "id", guild.id) != guild.id:
            channel = None
        is_forum = key == "feedback_forum_channel_id"
        if is_forum and channel is not None and not isinstance(channel, discord.ForumChannel):
            channel = None
        if channel is None and key not in force_new_keys:
            channel = next(
                (
                    item for item in category.channels
                    if item.name.casefold() == channel_name.casefold()
                    and (not is_forum or isinstance(item, discord.ForumChannel))
                ),
                None,
            )
        if channel is None and MONITORING_CONFIG.get("auto_create_channels", True):
            try:
                if is_forum:
                    create_forum = getattr(guild, "create_forum_channel", None)
                    if create_forum is None:
                        create_forum = getattr(guild, "create_forum", None)
                    if create_forum is None:
                        log.warn(
                            "Discord forum creation is unavailable",
                            [("Shop", shop_id)],
                        )
                        continue
                    channel = await create_forum(
                        channel_name,
                        category=category,
                        overwrites=overwrites,
                    )
                else:
                    channel = await guild.create_text_channel(
                        channel_name,
                        category=category,
                        overwrites=overwrites,
                    )
            except discord.DiscordException as exc:
                log.exception(
                    "Could not create monitoring channel",
                    exc,
                    [("Shop", shop_id), ("Channel", channel_name)],
                )
                continue
        if channel is None:
            log.warn("Monitoring channel is not configured", [("Shop", shop_id), ("Channel", key)])
            continue
        if is_forum and not isinstance(channel, discord.ForumChannel):
            log.warn("Feedback discussion channel is not a forum", [("Shop", shop_id)])
            continue
        try:
            await channel.edit(
                overwrites=overwrites,
                reason="Keep SellAuth monitoring private to staff and administrators",
            )
        except discord.DiscordException:
            pass
        channel_ids[key] = int(channel.id)
    return guild_id, category_id, channel_ids


async def prepare_monitoring(bot: discord.Bot) -> bool:
    if not any(monitor_enabled(name) for name in ("orders", "feedback", "out_of_stock")):
        return False

    lock = getattr(bot, "_monitoring_setup_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        bot._monitoring_setup_lock = lock
    async with lock:
        if getattr(bot, "_monitoring_initialized", False):
            return bool(monitoring_contexts())

        api = getattr(bot, "sellauth_api", None)
        if api is None:
            from src.sellauth.commands.common import get_api
            api = get_api(bot)
        try:
            discovered = await asyncio.to_thread(api.fetch_shops)
        except Exception as exc:
            log.exception("Could not discover SellAuth shops", exc)
            return False

        valid: dict[int, dict[str, Any]] = {}
        for shop in discovered or []:
            if not isinstance(shop, dict):
                continue
            try:
                shop_id = int(shop.get("id"))
            except (TypeError, ValueError):
                continue
            if shop_id > 0:
                valid[shop_id] = shop
        configured = MONITORING_CONFIG.get("shops", {})
        configured = configured if isinstance(configured, dict) else {}
        configured_shop_id = MONITORING_CONFIG.get("shop_id")
        if configured_shop_id is not None:
            selected_ids = (
                [int(configured_shop_id)]
                if int(configured_shop_id) in valid
                else []
            )
        elif MONITORING_CONFIG.get("all_shops", True):
            selected_ids = sorted(valid)
        else:
            selected_ids = []
        if not selected_ids:
            log.warn("No valid SellAuth shops were returned for monitoring")
            return False

        contexts: dict[str, dict[str, Any]] = {}
        legacy_id = MONITORING_CONFIG.get("shop_id")
        for shop_id in selected_ids:
            api_shop = valid[shop_id]
            existing = configured.get(str(shop_id), {})
            if not isinstance(existing, dict):
                existing = {}
            shop_name = str(
                existing.get("shop_name") or api_shop.get("name") or f"Shop {shop_id}"
            ).strip()
            legacy = legacy_id is not None and int(legacy_id) == shop_id
            guild_id, category_id, channel_ids = await _resolve_shop_channels(
                bot, shop_id, shop_name, existing, legacy
            )
            notifications = {
                **(existing.get("notifications") or {}),
                **channel_ids,
            }
            context = {
                "shop_id": shop_id,
                "shop_name": shop_name,
                "guild_id": guild_id,
                "category_id": category_id,
                "notifications": notifications,
            }
            contexts[str(shop_id)] = context
            persist_monitoring_state(
                shop_id=shop_id,
                shop_name=shop_name,
                guild_id=guild_id,
                category_id=category_id,
                channel_ids=channel_ids,
            )

        MONITORING_CONFIG["shop_contexts"] = contexts
        MONITORING_CONFIG["shop_ids"] = selected_ids
        primary_id = legacy_id if legacy_id in selected_ids else selected_ids[0]
        MONITORING_CONFIG["shop_id"] = primary_id
        bot._monitoring_initialized = True
        return bool(contexts)


async def repair_monitoring_channels(
    bot: discord.Bot,
    context: dict[str, Any],
    failed_channel_key: str | None = None,
) -> bool:
    shop_id = int(context["shop_id"])
    guild_id, category_id, channel_ids = await _resolve_shop_channels(
        bot,
        shop_id,
        str(context["shop_name"]),
        context,
        False,
        {failed_channel_key} if failed_channel_key else None,
    )
    context["guild_id"] = guild_id
    context["category_id"] = category_id
    context["notifications"].update(channel_ids)
    if channel_ids or category_id:
        persist_monitoring_state(
            shop_id=shop_id,
            shop_name=context["shop_name"],
            guild_id=guild_id,
            category_id=category_id,
            channel_ids=channel_ids,
        )
    return bool(channel_ids)


async def send_monitoring_message(
    bot: discord.Bot,
    context: dict[str, Any],
    channel_key: str,
    **kwargs: Any,
) -> bool:
    for attempt in range(2):
        channel = await resolve_channel(
            bot, context["notifications"].get(channel_key)
        )
        if channel is None:
            if attempt == 0 and await repair_monitoring_channels(
                bot, context, channel_key
            ):
                continue
            return False
        try:
            await channel.send(**kwargs)
            return True
        except discord.DiscordException as exc:
            log.warn(
                "Monitoring channel send failed; repairing channel",
                [("Shop", context["shop_id"]), ("Channel", channel_key), ("Error", str(exc))],
            )
            if attempt == 0:
                await repair_monitoring_channels(bot, context, channel_key)
                continue
            return False
    return False


async def create_monitoring_forum_thread(
    bot: discord.Bot,
    context: dict[str, Any],
    name: str,
    **kwargs: Any,
) -> bool:
    channel_key = "feedback_forum_channel_id"
    for attempt in range(2):
        channel = await resolve_channel(
            bot, context["notifications"].get(channel_key)
        )
        if not isinstance(channel, discord.ForumChannel):
            if attempt == 0 and await repair_monitoring_channels(
                bot, context, channel_key
            ):
                continue
            return False
        try:
            await channel.create_thread(name=name[:100], **kwargs)
            return True
        except discord.DiscordException as exc:
            log.warn(
                "Feedback forum operation failed; repairing forum",
                [("Shop", context["shop_id"]), ("Error", str(exc))],
            )
            if attempt == 0:
                await repair_monitoring_channels(bot, context, channel_key)
                continue
            return False
    return False
