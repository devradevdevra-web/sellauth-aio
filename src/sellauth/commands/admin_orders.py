
import re
import discord
from discord.ext import commands
from .common import C_GREEN, C_ORANGE, embed, get_api, require, owner_or_admin, run_api, bounded_positive, confirm, shop_autocomplete

_INVOICE_ID = re.compile(r"^[A-Za-z0-9]{1,64}(?:-[A-Za-z0-9]{1,64})?$")

def invoice_number(value: str) -> int:
    value = str(value or "").strip()
    if not _INVOICE_ID.fullmatch(value): raise ValueError("Invalid invoice ID format.")
    part = value.split("-")[-1].lstrip("0") or "0"
    return bounded_positive(part, "invoice_id")

class AdminOrdersCog(commands.Cog):
    def __init__(self, bot: discord.Bot): self.api = get_api(bot)

    @discord.slash_command(name="process-order", description="Process a SellAuth invoice")
    async def process_order(self, ctx: discord.ApplicationContext, shop_id: discord.Option(int, "Shop", min_value=1, autocomplete=shop_autocomplete), invoice_id: str):
        if not await require(ctx, owner_or_admin, "Only shop administrators can process invoices."): return
        try: number = invoice_number(invoice_id)
        except ValueError as exc: await ctx.respond(embed=embed("⚠️ Invalid invoice ID", str(exc), C_ORANGE), ephemeral=True); return
        if not await confirm(ctx, f"Process invoice `{invoice_id}` in shop `{shop_id}`?"): return
        result = await run_api(ctx, lambda: self.api.process_invoice(shop_id, number))
        if result is not None: await ctx.respond(embed=embed("✅ Invoice processed", f"Invoice ID: `{number}`", C_GREEN), ephemeral=True)

def setup(bot: discord.Bot): bot.add_cog(AdminOrdersCog(bot))
