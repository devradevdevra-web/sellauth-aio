import asyncio
import io

import discord
from discord.ext import commands

from src.sellauth.commands.ticket.service import (
    C_PURPLE,
    _DELIVERY_EMBED_CHAR_LIMIT,
    _DELIVERY_EMBED_ITEM_LIMIT,
    _clean_deliveries,
    _styled_embed,
)


class OrderInfoCog(commands.Cog):
    

    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @discord.slash_command(
        name="order-info",
        description="View an encrypted order record",
    )
    @discord.option("order_id", description="Order ID returned by order search")
    async def order_info(self, ctx: discord.ApplicationContext, order_id: str):
        buy_cog = self.bot.get_cog("BuyCog")
        if buy_cog is None:
            await ctx.respond("The order service is unavailable.", ephemeral=True)
            return
        if not buy_cog._is_support_member(ctx.author):
            await ctx.respond("Only support staff can view order details.", ephemeral=True)
            return

        record = await asyncio.to_thread(buy_cog.orders.get_order, order_id.strip())
        if record is None:
            await ctx.respond("Order not found.", ephemeral=True)
            return

        payload = record["payload"]
        embed = _styled_embed(
            f"📄  ORDER {record['order_id']}",
            "Sensitive fields were decrypted only for this private support response.",
            C_PURPLE,
        )
        embed.add_field(name="Status", value=f"`{record['status']}`", inline=True)
        embed.add_field(name="Created", value=record["created_at"][:19], inline=True)
        embed.add_field(
            name="Checkout",
            value=f"`{payload.get('checkout_id', 'N/A')}`",
            inline=False,
        )
        embed.add_field(
            name="Product",
            value=str(payload.get("product_name", "N/A")),
            inline=True,
        )
        embed.add_field(
            name="Quantity",
            value=str(payload.get("quantity", "N/A")),
            inline=True,
        )

        delivery_file = None
        delivery = payload.get("delivery")
        if delivery:
            cleaned_deliveries = _clean_deliveries(delivery)
            delivery_text = "\n".join(cleaned_deliveries)
            is_large = (
                len(cleaned_deliveries) > _DELIVERY_EMBED_ITEM_LIMIT
                or len(delivery_text) > _DELIVERY_EMBED_CHAR_LIMIT
            )
            if is_large:
                embed.add_field(
                    name="Delivery (private support view)",
                    value=(
                        f"**{len(cleaned_deliveries)} deliverables stored.**\n"
                        "The full delivery is attached as a TXT file instead of filling the embed."
                    ),
                    inline=False,
                )
                delivery_file = discord.File(
                    fp=io.BytesIO(delivery_text.encode("utf-8")),
                    filename=f"{record['order_id']}-delivery.txt",
                )
            else:
                embed.add_field(
                    name="Delivery (private support view)",
                    value=f"```{delivery_text or 'N/A'}```",
                    inline=False,
                )

        if payload.get("support_issue"):
            embed.add_field(
                name="Customer issue",
                value=str(payload["support_issue"])[:1024],
                inline=False,
            )

        if delivery_file is not None:
            await ctx.respond(embed=embed, file=delivery_file, ephemeral=True)
        else:
            await ctx.respond(embed=embed, ephemeral=True)


def setup(bot: discord.Bot):
    bot.add_cog(OrderInfoCog(bot))
