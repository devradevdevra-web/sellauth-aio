from __future__ import annotations

import asyncio
from typing import Any

import discord
from discord.ext import commands

import src.logger.logger as log
from src.config import MONITORING_CONFIG
from src.sellauth.commands.common import get_api
from src.sellauth.commands.embed_style import styled_embed
from .common import (
    load_ids,
    monitor_enabled,
    monitoring_contexts,
    prepare_monitoring,
    response_rows,
    save_ids,
    create_monitoring_forum_thread,
    send_monitoring_message,
)


def _feedback_id(feedback: dict[str, Any]) -> str | None:
    value = feedback.get("id", feedback.get("feedback_id"))
    return str(value) if value not in (None, "") else None


def _feedback_value(feedback: dict[str, Any], *keys: str, default: str = "N/A") -> str:
    for key in keys:
        value = feedback.get(key)
        if value not in (None, ""):
            return str(value)
    return default


class FeedbackMonitorCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.api = get_api(bot)
        self._tasks: dict[int, asyncio.Task] = {}
        settings = MONITORING_CONFIG["feedback"]
        self.processed_path = settings["processed_path"]

    @commands.Cog.listener()
    async def on_ready(self):
        if not monitor_enabled("feedback"):
            log.info("Feedback monitor disabled")
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
            if shop_id not in self._tasks:
                self._tasks[shop_id] = asyncio.create_task(self._poll(context))
                log.info("Feedback monitor started", [("Shop", context["shop_name"]), ("ShopID", shop_id)])

    def cog_unload(self):
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        self._tasks.clear()

    async def _send_notifications(
        self,
        context: dict[str, Any],
        feedback: dict[str, Any],
        invoice: dict[str, Any] | None,
    ) -> bool:
        feedback_id = _feedback_id(feedback) or "unknown"
        items = invoice.get("items", []) if isinstance(invoice, dict) else []
        product_name = "Unknown Product"
        if isinstance(items, list) and items and isinstance(items[0], dict):
            product = items[0].get("product")
            product_name = str(
                product.get("name") if isinstance(product, dict) else items[0].get("name")
                or product_name
            )
        try:
            rating = max(0, min(5, int(feedback.get("rating", 0))))
        except (TypeError, ValueError):
            rating = 0
        stars = "⭐" * rating if rating else "No rating"
        message = _feedback_value(feedback, "message", "comment", default="No message")
        reply = _feedback_value(feedback, "reply", default="No reply yet")
        shop_name = context["shop_name"]

        customer_embed = styled_embed(
            "📝  NEW FEEDBACK RECEIVED",
            "A new customer review was received for the shop.",
            0x22C55E,
        )
        customer_embed.add_field(name="🆔 Feedback ID", value=f"`{feedback_id}`")
        customer_embed.add_field(name="🛒 Product", value=f"**{product_name}**")
        customer_embed.add_field(name="⭐ Rating", value=stars)
        customer_embed.add_field(name="💬 Message", value=message[:1024], inline=False)
        customer_embed.add_field(name="🔄 Reply", value=reply[:1024], inline=False)
        customer_embed.add_field(name="🏬 Shop", value=f"**{shop_name}**", inline=False)
        if invoice:
            customer_embed.add_field(name="💰 Price", value=f"`{invoice.get('price', 'N/A')}`")
            customer_embed.add_field(name="📧 Email", value=f"`{invoice.get('email', 'N/A')}`")

        staff_embed = styled_embed(
            "📝  NEW FEEDBACK RECEIVED",
            "A new customer review needs attention.",
            0x22C55E,
        )
        staff_embed.add_field(name="🆔 Feedback ID", value=f"`{feedback_id}`")
        staff_embed.add_field(name="🛒 Product", value=f"**{product_name}**")
        staff_embed.add_field(name="⭐ Rating", value=stars)
        staff_embed.add_field(name="💬 Message", value=message[:1024], inline=False)
        staff_embed.add_field(name="🔄 Reply", value=reply[:1024], inline=False)
        staff_embed.add_field(name="🏬 Shop", value=f"**{shop_name}**", inline=False)

        staff_sent = await send_monitoring_message(
            self.bot,
            context,
            "feedback_channel_id",
            embed=staff_embed,
        )
        forum_sent = await create_monitoring_forum_thread(
            self.bot,
            context,
            f"{shop_name} • Feedback #{feedback_id}",
            embed=customer_embed,
        )
        return staff_sent and forum_sent

    async def _poll(self, context: dict[str, Any]):
        shop_id = int(context["shop_id"])
        settings = MONITORING_CONFIG["feedback"]
        processed_feedback = load_ids(self.processed_path, shop_id)
        while not self.bot.is_closed():
            delay = settings["poll_interval_seconds"]
            try:
                data = await asyncio.to_thread(self.api.list_feedbacks, shop_id, page=1, perPage=100)
                for feedback in response_rows(data):
                    feedback_id = _feedback_id(feedback)
                    if feedback_id is None or feedback_id in processed_feedback:
                        continue
                    invoice_id = feedback.get("invoice_id", feedback.get("invoiceId"))
                    invoice = None
                    if invoice_id not in (None, ""):
                        try:
                            invoice = await asyncio.to_thread(self.api.get_invoice, shop_id, invoice_id)
                        except Exception as exc:
                            log.warn("Feedback invoice lookup failed", [("Shop", shop_id), ("Feedback", feedback_id), ("Error", str(exc))])
                    if await self._send_notifications(context, feedback, invoice):
                        processed_feedback.add(feedback_id)
                save_ids(self.processed_path, shop_id, processed_feedback)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                delay = settings["error_retry_interval_seconds"]
                log.exception("Feedback monitor failed", exc, [("Shop", shop_id)])
            await asyncio.sleep(delay)


def setup(bot: discord.Bot):
    bot.add_cog(FeedbackMonitorCog(bot))
