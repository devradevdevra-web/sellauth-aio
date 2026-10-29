
import discord
from discord.ext import commands
from .common import C_GREEN, embed, get_api, require, owner_or_admin, run_api, bounded_text, bounded_positive, confirm, shop_autocomplete

class AdminBlacklistCog(commands.Cog):
    def __init__(self, bot: discord.Bot): self.api = get_api(bot)

    @discord.slash_command(name="create-blacklist", description="Add an exact blacklist rule")
    async def create_blacklist(self, ctx: discord.ApplicationContext, shop_id: discord.Option(int, "Shop", min_value=1, autocomplete=shop_autocomplete), blacklist_type: discord.Option(str, "Data type", choices=["user_agent", "email", "ip"]), data: str, reason: str):
        if not await require(ctx, owner_or_admin, "Only shop administrators can change the blacklist."): return
        result = await run_api(ctx, lambda: self.api.create_blacklist(shop_id, blacklist_type=blacklist_type, value=bounded_text(data, "data", 320), reason=bounded_text(reason, "reason", 500)))
        if result is not None: await ctx.respond(embed=embed("✅ Data blacklisted", f"Type: `{blacklist_type}`\nValue: `{data[:320]}`\nReason: `{reason[:500]}`", C_GREEN), ephemeral=True)

    @discord.slash_command(name="remove-blacklist", description="Remove a blacklist entry after confirmation")
    async def remove_blacklist(self, ctx: discord.ApplicationContext, shop_id: discord.Option(int, "Shop", min_value=1, autocomplete=shop_autocomplete), blacklist_id: discord.Option(int, "Blacklist ID", min_value=1)):
        if not await require(ctx, owner_or_admin, "Only shop administrators can change the blacklist."): return
        if not await confirm(ctx, f"Remove blacklist entry `{blacklist_id}` from shop `{shop_id}`? This cannot be undone."): return
        result = await run_api(ctx, lambda: self.api.delete_blacklist(shop_id, bounded_positive(blacklist_id, "blacklist_id")))
        if result is not None: await ctx.respond(embed=embed("✅ Blacklist entry removed", f"Blacklist ID: `{blacklist_id}`", C_GREEN), ephemeral=True)

def setup(bot: discord.Bot): bot.add_cog(AdminBlacklistCog(bot))
