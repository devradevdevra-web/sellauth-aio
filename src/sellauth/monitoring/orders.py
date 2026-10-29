from __future__ import annotations

import asyncio
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


_PENDING_STATUSES = {"pending", "partially_completed", "processing"}


def _invoice_id(invoice: dict[str, Any]) -> str | None:
    value = invoice.get("unique_id", invoice.get("id"))
    return str(value) if value not in (None, "") else None


def _invoice_details(invoice: dict[str, Any]) -> tuple[str, int, str]:
    items = invoice.get("items") if isinstance(invoice.get("items"), list) else []
    names = []
    quantity = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        product = item.get("product")
        name = product.get("name") if isinstance(product, dict) else item.get("name")
        if name and str(name) not in names:
            names.append(str(name))
        try:
            quantity += max(0, int(item.get("quantity", 0)))
        except (TypeError, ValueError):
            pass
    return ", ".join(names) or "Unknown Product", max(quantity, 1), str(
        invoice.get("gateway") or invoice.get("payment_method") or "Unknown"
    )


class OrderMonitorCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.api = get_api(bot)
        self._tasks: dict[int, asyncio.Task] = {}
        settings = MONITORING_CONFIG["orders"]
        self.processed_path = settings["processed_path"]
        self.pending_path = settings["pending_path"]

    @commands.Cog.listener()
    async def on_ready(self):
        if not monitor_enabled("orders"):
            log.info("Order monitor disabled")
            return
        if not await prepare_monitoring(self.bot):
            return
        contexts = {int(c["shop_id"]): c for c in monitoring_contexts()}
        for shop_id, task in list(self._tasks.items()):
            if shop_id not in contexts or task.done():
                if not task.done():
                    task.cancel()
                self._tasks.pop(shop_id, None)
        started_shops = []
        for shop_id, context in contexts.items():
            if shop_id not in self._tasks:
                self._tasks[shop_id] = asyncio.create_task(self._poll(context))
                started_shops.append(str(context["shop_name"]))
        if started_shops:
            log.info(
                "Order monitoring active",
                [
                    ("Shops", len(started_shops)),
                    ("Names", ", ".join(started_shops)),
                ],
            )

    def cog_unload(self):
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        self._tasks.clear()

    async def _send(self, context: dict[str, Any], channel_key: str, embed: discord.Embed) -> bool:
        sent = await send_monitoring_message(
            self.bot,
            context,
            channel_key,
            embed=embed,
        )
        if not sent:
            log.warn(
                "Order notification channel is unavailable",
                [("Shop", context["shop_id"]), ("Channel", channel_key)],
            )
        return sent

    async def _poll(self, context: dict[str, Any]):
        shop_id = int(context["shop_id"])
        settings = MONITORING_CONFIG["orders"]
        processed_orders = load_ids(self.processed_path, shop_id)
        pending_orders = load_ids(self.pending_path, shop_id)
        while not self.bot.is_closed():
            delay = settings["poll_interval_seconds"]
            try:
                data = await asyncio.to_thread(
                    self.api.list_invoices,
                    shop_id,
                    page=1,
                    perPage=100,
                    suppress_error_log=True,
                )
                for invoice in response_rows(data):
                    invoice_id = _invoice_id(invoice)
                    if invoice_id is None:
                        continue
                    status = str(invoice.get("status", "")).strip().casefold()
                    product_name, quantity, gateway = _invoice_details(invoice)
                    if status in _PENDING_STATUSES and invoice_id not in processed_orders:
                        if invoice_id in pending_orders:
                            continue
                        pending_embed = styled_embed(
                            "🛒  NEW ORDER — PENDING",
                            "A new SellAuth order is awaiting completion.",
                            0xF59E0B,
                        )
                        pending_embed.add_field(name="🛒 Product", value=f"**{product_name}**")
                        pending_embed.add_field(name="🆔 Order ID", value=f"`{invoice_id}`")
                        pending_embed.add_field(name="🔢 Quantity", value=f"`{quantity:,}`")
                        pending_embed.add_field(name="🔃 Status", value=f"`{status}`")
                        pending_embed.add_field(name="🏬 Shop", value=f"**{context['shop_name']}**")
                        if await self._send(context, "pending_orders_channel_id", pending_embed):
                            pending_orders.add(invoice_id)
                    elif status == "completed" and invoice_id not in processed_orders:
                        sale_embed = styled_embed(
                            "💵  NEW SALE",
                            "A SellAuth order has completed successfully.",
                            0x14B8A6,
                        )
                        sale_embed.add_field(name="🛒 Product", value=f"**{product_name}**")
                        sale_embed.add_field(name="🔃 Status", value="`completed`")
                        sale_embed.add_field(name="💰 Price", value=f"`{invoice.get('price', 'N/A')}`")
                        sale_embed.add_field(name="🔢 Quantity", value=f"`{quantity:,}`")
                        sale_embed.add_field(name="💳 Payment method", value=f"`{gateway}`")
                        sale_embed.add_field(name="🏬 Shop", value=f"**{context['shop_name']}**")
                        if await self._send(context, "sales_channel_id", sale_embed):
                            processed_orders.add(invoice_id)
                            pending_orders.discard(invoice_id)
                save_ids(self.processed_path, shop_id, processed_orders)
                save_ids(self.pending_path, shop_id, pending_orders)
            except asyncio.CancelledError:
                raise
            except SellAuthError:
                delay = settings["error_retry_interval_seconds"]
            except Exception as exc:
                delay = settings["error_retry_interval_seconds"]
                log.exception("Order monitor failed", exc, [("Shop", shop_id)])
            await asyncio.sleep(delay)


def setup(bot: discord.Bot):
    bot.add_cog(OrderMonitorCog(bot))
