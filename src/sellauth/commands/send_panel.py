import discord
from discord.ext import commands

from src.sellauth.commands.ticket.service import AutoBuyPanelView, C_PURPLE
from src.sellauth.commands.embed_style import styled_embed
from src.sellauth.commands.common import owner_or_admin


class SendPanelCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @discord.slash_command(name="send-panel", description="Send the auto-buy ticket panel")
    async def sendpanel(self, ctx: discord.ApplicationContext):
        if not ctx.guild or not owner_or_admin(ctx.author):
            await ctx.respond(
                "Only server administrators can send the auto-buy panel.",
                ephemeral=True,
            )
            return

        ticket_cog = self.bot.get_cog("BuyCog")
        if ticket_cog is None:
            await ctx.respond("The ticket service is unavailable.", ephemeral=True)
            return

        embed = styled_embed(
            title="🛒  INSTANT DIGITAL STORE",
            description=(
                "Pick your shop, choose a product, and check out in a private ticket.\n\n"
                "✨ Instant digital delivery\n"
                "🔒 Private checkout from start to finish\n"
                "⚡ Automatic payment confirmation\n"
                "📩 Your completed order is sent by DM\n\n"
                "Click **Open Purchase Ticket** to get started."
            ),
            color=C_PURPLE,
        )
        await ctx.channel.send(embed=embed, view=AutoBuyPanelView(ticket_cog))
        await ctx.respond("✅ Auto-buy panel sent.", ephemeral=True)


def setup(bot: discord.Bot):
    bot.add_cog(SendPanelCog(bot))
