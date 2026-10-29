
import discord
from discord.ext import commands
from .common import C_GREEN, embed, get_api, require, owner_or_admin, run_api, bounded_positive, bounded_text, shop_autocomplete

class AdminFeedbackCog(commands.Cog):
    def __init__(self, bot: discord.Bot): self.api = get_api(bot)

    @discord.slash_command(name="reply-feedback", description="Reply to customer feedback")
    async def reply_feedback(self, ctx: discord.ApplicationContext, shop_id: discord.Option(int, "Shop", min_value=1, autocomplete=shop_autocomplete), feedback_id: discord.Option(int, "Feedback ID", min_value=1), message: str):
        if not await require(ctx, owner_or_admin, "Only shop administrators can reply to feedback."): return
        message = bounded_text(message, "message", 2000)
        result = await run_api(ctx, lambda: self.api.reply_feedback(shop_id, bounded_positive(feedback_id, "feedback_id"), message))
        if result is not None: await ctx.respond(embed=embed("✅ Feedback reply sent", f"Feedback ID: `{feedback_id}`\nReply: {message[:1000]}", C_GREEN), ephemeral=True)

def setup(bot: discord.Bot): bot.add_cog(AdminFeedbackCog(bot))
