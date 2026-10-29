
import asyncio
import re
import discord
from discord.ext import commands
from src.config import CUSTOMER_ROLE_NAME
from .admin_orders import invoice_number
from .common import C_GREEN, C_ORANGE, C_RED, embed, get_api, run_api, bounded_text, shop_autocomplete

class CustomerCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot; self.api = get_api(bot); self.claimed: set[tuple[int, str]] = set()

    @discord.slash_command(name="claim-customer-role", description="Claim the customer role for a completed order")
    async def claim_customer_role(self, ctx: discord.ApplicationContext, shop_id: discord.Option(int, "Shop", min_value=1, autocomplete=shop_autocomplete), order_id: str):
        if not ctx.guild: await ctx.respond(embed=embed("❌ Server only", "This command must be used in a server.", C_RED), ephemeral=True); return
        try: order_id = bounded_text(order_id, "order_id", 128); invoice_id = invoice_number(order_id)
        except ValueError as exc: await ctx.respond(embed=embed("⚠️ Invalid order ID", str(exc), C_ORANGE), ephemeral=True); return
        key = (ctx.author.id, order_id)
        buy_cog = self.bot.get_cog("BuyCog")
        order_store = getattr(buy_cog, "orders", None)
        if order_store and await asyncio.to_thread(order_store.is_customer_role_claimed, order_id):
            await ctx.respond(embed=embed("❌ Already claimed", "This order has already been used for a customer role.", C_ORANGE), ephemeral=True)
            return
        if key in self.claimed:
            await ctx.respond(embed=embed("❌ Already claimed", "You already claimed this order.", C_ORANGE), ephemeral=True)
            return
        invoice = await run_api(ctx, lambda: self.api.get_invoice(shop_id, invoice_id))
        if invoice is None: return
        status = str(invoice.get("status", invoice.get("payment_status", ""))).lower()
        if status not in {"paid", "completed", "complete", "delivered", "processed"}:
            await ctx.respond(embed=embed("⏳ Order not completed", f"Invoice `{order_id}` is not completed yet.", C_ORANGE), ephemeral=True); return
        role = discord.utils.get(ctx.guild.roles, name=CUSTOMER_ROLE_NAME)
        if role is None: await ctx.respond(embed=embed("❌ Role not found", f"Create the `{CUSTOMER_ROLE_NAME}` role or configure CUSTOMER_ROLE_NAME.", C_RED), ephemeral=True); return
        try: await ctx.author.add_roles(role, reason=f"Completed SellAuth order {order_id}")
        except discord.Forbidden: await ctx.respond(embed=embed("❌ Missing permission", "The bot cannot assign that role."), ephemeral=True); return
        if order_store and not await asyncio.to_thread(
            order_store.claim_customer_role, ctx.author.id, order_id
        ):
            await ctx.respond(
                embed=embed("❌ Already claimed", "This order has already been used for a customer role.", C_ORANGE),
                ephemeral=True,
            )
            return
        self.claimed.add(key)
        await ctx.respond(embed=embed("✅ Customer role assigned", f"Order ID: `{order_id}`\nRole: {role.mention}", C_GREEN), ephemeral=True)

def setup(bot: discord.Bot): bot.add_cog(CustomerCog(bot))
