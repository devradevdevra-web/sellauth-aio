
import asyncio
import discord
from discord.ext import commands
from .common import C_GREEN, C_RED, embed, get_api, require, owner_or_admin, resolve_shop_id, run_api, bounded_positive, shop_autocomplete, product_autocomplete

_COUPON_PAGE_SIZE = 5


def _coupon_value(row: dict) -> str:
    code = str(row.get("code", "unknown"))
    discount = row.get("discount", "N/A")
    used = row.get("used_count", row.get("uses", row.get("used", 0)))
    maximum = row.get("max_uses")
    uses = f"{used}/{maximum}" if maximum not in (None, "") else str(used)
    expiry = str(row.get("expiration_date") or "Never").replace("T", " ")[:19]
    return (
        f"`🎟️` **Code:** `{code}`\n"
        f"`💸` **Discount:** `{discount}%`\n"
        f"`🔢` **Uses:** `{uses}`\n"
        f"`⏰` **Expires:** `{expiry}`"
    )


def _coupons_embed(rows: list[dict], page: int) -> discord.Embed:
    total_pages = max(1, (len(rows) + _COUPON_PAGE_SIZE - 1) // _COUPON_PAGE_SIZE)
    start = page * _COUPON_PAGE_SIZE
    current = rows[start : start + _COUPON_PAGE_SIZE]
    embed_result = embed(
        "✅  COUPON LIST",
        (
            f"**Page {page + 1}/{total_pages}**  •  "
            f"**{len(rows)} coupon(s)**  •  "
            f"Showing **{start + 1}–{start + len(current)}**"
        ),
        C_GREEN,
    )
    for index, row in enumerate(current, start=start + 1):
        code = str(row.get("code", "unknown"))
        embed_result.add_field(
            name=f"`🎟️` Coupon #{index:02d}  •  `{code}`",
            value=_coupon_value(row),
            inline=False,
        )
    return embed_result


class CouponPagesView(discord.ui.View):
    def __init__(self, rows: list[dict], requester_id: int):
        super().__init__(timeout=300)
        self.rows = rows
        self.requester_id = requester_id
        self.page = 0
        if self.total_pages <= 1:
            self.remove_item(self.previous_page)
            self.remove_item(self.next_page)
        else:
            self._update_buttons()

    @property
    def total_pages(self) -> int:
        return max(1, (len(self.rows) + _COUPON_PAGE_SIZE - 1) // _COUPON_PAGE_SIZE)

    def _update_buttons(self):
        self.previous_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= self.total_pages - 1

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message(
            "Only the support member who ran this command can change pages.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="Previous", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def previous_page(self, button, interaction: discord.Interaction):
        if not await self._authorized(interaction):
            return
        self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_coupons_embed(self.rows, self.page), view=self
        )

    @discord.ui.button(label="Next", emoji="▶️", style=discord.ButtonStyle.primary)
    async def next_page(self, button, interaction: discord.Interaction):
        if not await self._authorized(interaction):
            return
        self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_coupons_embed(self.rows, self.page), view=self
        )


class AdminShopCog(commands.Cog):
    def __init__(self, bot: discord.Bot): self.api = get_api(bot)

    @discord.slash_command(name="check-balance", description="View crypto payout balances")
    async def check_balance(self, ctx: discord.ApplicationContext, shop_id: discord.Option(int, "Shop", min_value=1, autocomplete=shop_autocomplete)):
        if not await require(ctx, owner_or_admin, "Only shop administrators can view balances."): return
        sid = await resolve_shop_id(self.api, shop_id)
        data = await run_api(ctx, lambda: self.api.get_balance(sid))
        if data is None: return
        e = embed("✅ Crypto balance", color=C_GREEN)
        for currency in ("ltc", "btc"):
            value = data.get(currency, {}) if isinstance(data.get(currency), dict) else {}
            e.add_field(name=currency.upper(), value=f"Balance: `{value.get(currency, 0)}`\nUSD: `{value.get('usd', 0)}`", inline=True)
        await ctx.respond(embed=e, ephemeral=True)

    @discord.slash_command(name="check-shop-stats", description="View SellAuth shop statistics")
    async def check_shop_stats(self, ctx: discord.ApplicationContext, shop_id: discord.Option(int, "Shop", min_value=1, autocomplete=shop_autocomplete)):
        if not await require(ctx, owner_or_admin, "Only shop administrators can view shop statistics."): return
        sid = await resolve_shop_id(self.api, shop_id)
        data = await run_api(ctx, lambda: self.api.get_shop_stats(sid))
        if data is None: return
        e = embed("✅ Shop statistics", color=C_GREEN)
        for key, label in (("products_sold", "Products sold"), ("total_customers", "Customers"), ("total_feedbacks", "Feedbacks"), ("average_rating", "Average rating")):
            e.add_field(name=label, value=f"`{data.get(key, 'N/A')}`", inline=True)
        await ctx.respond(embed=e, ephemeral=True)

    @discord.slash_command(name="check-product-stock", description="View product stock and price")
    async def check_product_stock(self, ctx: discord.ApplicationContext, shop_id: discord.Option(int, "Shop", min_value=1, autocomplete=shop_autocomplete), product_id: discord.Option(int, "Product", min_value=1, autocomplete=product_autocomplete)):
        if not await require(ctx, owner_or_admin, "Only shop administrators can view product stock."): return
        data = await run_api(ctx, lambda: self.api.get_product(shop_id, product_id))
        if data is None: return
        variants = data.get("variants") or []
        price = data.get("price")
        if price in (None, ""):
            price = next(
                (variant.get("price") for variant in variants if variant.get("price") not in (None, "")),
                "N/A",
            )
        product_name = str(data.get("name", "Unnamed product"))[:100]
        stock = data.get("stock_count", "N/A")
        currency = data.get("currency") or "N/A"
        e = embed("✅  PRODUCT STOCK", color=C_GREEN)
        e.add_field(name="`🛒` Product", value=f"`{product_name}`", inline=True)
        e.add_field(name="`📈` Stock", value=f"`{stock}`", inline=True)
        e.add_field(name="`💵` Price", value=f"`{price} {currency}`", inline=True)
        await ctx.respond(embed=e, ephemeral=True)

    @discord.slash_command(name="check-coupons", description="List coupons for a SellAuth shop")
    async def check_coupons(self, ctx: discord.ApplicationContext, shop_id: discord.Option(int, "Shop", min_value=1, autocomplete=shop_autocomplete)):
        if not await require(ctx, owner_or_admin, "Only shop administrators can view coupons."): return
        data = await run_api(ctx, lambda: self.api.list_coupons(shop_id))
        if data is None: return
        rows = data.get("data", []) if isinstance(data, dict) else data
        rows = [row for row in (rows or []) if isinstance(row, dict)]
        if not rows:
            await ctx.respond(
                embed=embed(
                    "✅  COUPONS",
                    "No coupons are currently configured for this shop.",
                    C_GREEN,
                ),
                ephemeral=True,
            )
            return
        view = CouponPagesView(rows, ctx.author.id)
        await ctx.respond(
            embed=_coupons_embed(rows, view.page),
            view=view,
            ephemeral=True,
        )

def setup(bot: discord.Bot): bot.add_cog(AdminShopCog(bot))
