
from datetime import datetime, timedelta, timezone
import discord
from discord.ext import commands
from .common import (
    C_GREEN,
    embed,
    get_api,
    require,
    owner_or_admin,
    run_api,
    bounded_positive,
    bounded_text,
    confirm,
    shop_autocomplete,
    coupon_autocomplete,
)

class AdminCouponsCog(commands.Cog):
    def __init__(self, bot: discord.Bot): self.api = get_api(bot)

    @discord.slash_command(name="create-coupon", description="Create a percentage coupon")
    async def create_coupon(self, ctx: discord.ApplicationContext, shop_id: discord.Option(int, "Shop", min_value=1, autocomplete=shop_autocomplete), code: str, discount: str, max_uses: discord.Option(int, "Maximum uses", min_value=1, max_value=100000), days_expiry: discord.Option(int, "Days until expiry", min_value=1, max_value=3650)):
        if not await require(ctx, owner_or_admin, "Only shop administrators can create coupons."): return
        code = bounded_text(code, "code", 64).upper()
        try: discount_value = float(discount)
        except ValueError: await ctx.respond(embed=embed("❌ Invalid discount", "Discount must be a number from 0.01 to 100."), ephemeral=True); return
        if not 0.01 <= discount_value <= 100: await ctx.respond(embed=embed("❌ Invalid discount", "Discount must be from 0.01 to 100."), ephemeral=True); return
        expiry = datetime.now(timezone.utc) + timedelta(days=days_expiry)
        coupon = {"code": code, "global": True, "discount": discount_value, "type": "percentage", "max_uses": max_uses, "expiration_date": expiry.isoformat(), "disable_if_volume_discount": False}
        data = await run_api(ctx, lambda: self.api.create_coupon(shop_id, **coupon))
        if data is not None:
            result = embed("✅  COUPON CREATED", "The coupon is ready to use.", C_GREEN)
            result.add_field(name="`🎟️` Code", value=f"`{code}`", inline=True)
            result.add_field(
                name="`💸` Discount", value=f"`{discount_value:g}%`", inline=True
            )
            result.add_field(
                name="`🔢` Maximum uses", value=f"`{max_uses:,}`", inline=True
            )
            result.add_field(
                name="`⏰` Expires", value=f"`{expiry.isoformat()}`", inline=False
            )
            await ctx.respond(embed=result, ephemeral=True)

    @discord.slash_command(name="delete-coupon", description="Delete a coupon after confirmation")
    async def delete_coupon(
        self,
        ctx: discord.ApplicationContext,
        shop_id: discord.Option(int, "Shop", min_value=1, autocomplete=shop_autocomplete),
        coupon_id: discord.Option(
            int,
            "Coupon",
            min_value=1,
            autocomplete=coupon_autocomplete,
        ),
    ):
        if not await require(ctx, owner_or_admin, "Only shop administrators can delete coupons."): return
        if not await confirm(ctx, f"Delete the selected coupon from shop `{shop_id}`? This cannot be undone."): return
        data = await run_api(ctx, lambda: self.api.delete_coupon(shop_id, bounded_positive(coupon_id, "coupon_id")))
        if data is not None:
            await ctx.respond(
                embed=embed(
                    "✅  COUPON DELETED",
                    "The selected coupon was deleted successfully.",
                    C_GREEN,
                ),
                ephemeral=True,
            )


def setup(bot: discord.Bot): bot.add_cog(AdminCouponsCog(bot))
