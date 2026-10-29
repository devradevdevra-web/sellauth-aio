from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import discord
from discord.ext import commands

import src.logger.logger as log
from src.config import MONITORING_CONFIG
from src.sellauth.commands.common import get_api
from src.sellauth.commands.embed_style import styled_embed
from src.sellauth.sellauth import SellAuthError
from .common import (
    load_ids,
    monitor_enabled,
    monitoring_contexts,
    prepare_monitoring,
    response_rows,
    save_ids,
    send_monitoring_message,
)


def _notification_id(notification: dict[str, Any]) -> str:
    value = notification.get("id", notification.get("notification_id"))
    if value not in (None, ""):
        return str(value)
    stable = {
        "title": notification.get("title"),
        "description": notification.get("description"),
        "link": notification.get("link"),
    }
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class OutOfStockMonitorCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.api = get_api(bot)
        self._tasks: dict[int, asyncio.Task] = {}
        self._unsupported: set[int] = set()
        self.processed_path = MONITORING_CONFIG["out_of_stock"]["processed_path"]

    @commands.Cog.listener()
    async def on_ready(self):
        if not monitor_enabled("out_of_stock"):
            log.info("Out-of-stock monitor disabled")
            return
        if not await prepare_monitoring(self.bot):
            return
        contexts = {int(c["shop_id"]): c for c in monitoring_contexts()}
        for shop_id, task in list(self._tasks.items()):
            if shop_id not in contexts or task.done():
                if not task.done():
                    task.cancel()
                self._tasks.pop(shop_id, None)
        for shop_id, context in contexts.items():
            if shop_id not in self._tasks and shop_id not in self._unsupported:
                self._tasks[shop_id] = asyncio.create_task(self._poll(context))
                log.info("Out-of-stock monitor started", [("Shop", context["shop_name"]), ("ShopID", shop_id)])

    def cog_unload(self):
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        self._tasks.clear()

    async def _send_alert(self, context: dict[str, Any], notification: dict[str, Any]) -> bool:
        shop_id = int(context["shop_id"])
        product_name = str(notification.get("description") or "Unknown Product")
        title = str(notification.get("title") or "Product Out of Stock")
        link = str(notification.get("link") or "")
        embed = styled_embed(
            "⚠️  PRODUCT OUT OF STOCK",
            "A SellAuth product has reached zero available stock.",
            0xF59E0B,
        )
        embed.add_field(name="📦 Product", value=f"**{product_name[:1024]}**", inline=False)
        embed.add_field(name="🛒 Stock status", value="`OUT OF STOCK`", inline=True)
        embed.add_field(name="🏬 Shop", value=f"**{context['shop_name']}**", inline=True)
        embed.add_field(name="🔔 Alert", value=f"`{title[:100]}`", inline=False)
        if link.startswith(("http://", "https://")):
            embed.add_field(name="🔗 Product link", value=link[:1024], inline=False)
        sent = await send_monitoring_message(
            self.bot,
            context,
            "stock_warnings_channel_id",
            embed=embed,
        )
        if not sent:
            log.warn("Stock warning channel is unavailable", [("Shop", shop_id)])
        return sent

    async def _poll(self, context: dict[str, Any]):
        shop_id = int(context["shop_id"])
        settings = MONITORING_CONFIG["out_of_stock"]
        processed_notifications = load_ids(self.processed_path, shop_id)
        while not self.bot.is_closed() and shop_id not in self._unsupported:
            delay = settings["poll_interval_seconds"]
            try:
                data = await asyncio.to_thread(self.api.get_latest_notifications, shop_id)
                for notification in response_rows(data, preferred_key="notifications"):
                    title = str(notification.get("title") or "").casefold()
                    if "out of stock" not in title:
                        continue
                    notification_id = _notification_id(notification)
                    if notification_id in processed_notifications:
                        continue
                    if await self._send_alert(context, notification):
                        processed_notifications.add(notification_id)
                save_ids(self.processed_path, shop_id, processed_notifications)
            except asyncio.CancelledError:
                raise
            except SellAuthError as exc:
                if exc.status_code == 404:
                    self._unsupported.add(shop_id)
                    log.warn(
                        "SellAuth latest notifications endpoint is unavailable; out-of-stock monitor stopped",
                        [("Shop", shop_id)],
                    )
                    return
                delay = settings["error_retry_interval_seconds"]
                log.exception("Out-of-stock monitor failed", exc, [("Shop", shop_id)])
            except Exception as exc:
                delay = settings["error_retry_interval_seconds"]
                log.exception("Out-of-stock monitor failed", exc, [("Shop", shop_id)])
            await asyncio.sleep(delay)


def setup(bot: discord.Bot):
    bot.add_cog(OutOfStockMonitorCog(bot))
