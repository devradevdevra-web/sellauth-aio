import asyncio

import discord
from discord.ext import commands

from src.sellauth.commands.ticket.service import C_BLUE, _styled_embed


_PAGE_SIZE = 10
_STATUS_ICONS = {
    "ticket_open": "🛒",
    "checkout_created": "🧾",
    "details_required": "📝",
    "payment_pending": "💳",
    "payment_completed": "✅",
    "paid_no_delivery": "⚠️",
    "delivery_available": "📬",
    "delivered": "✅",
    "support_reopened": "🆘",
    "ticket_closed": "🔒",
    "ticket_deleted": "🗑️",
    "expired": "⌛",
    "checkout_error": "❌",
}


def _history_embed(records: list[dict], user: discord.Member, page: int) -> discord.Embed:
    total_pages = max(1, (len(records) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    start = page * _PAGE_SIZE
    page_records = records[start : start + _PAGE_SIZE]
    order_count = len(records)
    order_label = "order" if order_count == 1 else "orders"
    if total_pages > 1:
        navigation = "Use the buttons below to browse the order history."
    else:
        navigation = "Showing the saved order history below."

    embed = _styled_embed(
        "📦  CUSTOMER ORDER HISTORY",
        (
            f"Saved orders for {user.mention}. {navigation}\n\n"
            f"**Page {page + 1}/{total_pages}**  •  **{order_count} {order_label}**  •  "
            f"Showing **{start + 1}–{start + len(page_records)}**"
        ),
        C_BLUE,
    )

    for index, record in enumerate(page_records, start=start + 1):
        payload = record["payload"]
        status = str(record["status"])
        icon = _STATUS_ICONS.get(status, "📦")
        product = str(payload.get("product_name") or "Checkout not started")[:80]
        created = str(record["created_at"])[:16].replace("T", " ")
        order_id = str(record["order_id"])
        embed.add_field(
            name=f"{icon}  {status.replace('_', ' ').title()}  ·  #{index:02d}",
            value=(
                f"`{order_id}`\n"
                f"`📦` **{product}**\n"
                f"`🕒` {created} UTC"
            ),
            inline=False,
        )

    return embed


class OrderHistoryView(discord.ui.View):
    def __init__(self, records: list[dict], user: discord.Member, requester_id: int):
        super().__init__(timeout=300)
        self.records = records
        self.user = user
        self.requester_id = requester_id
        self.page = 0
        if self.total_pages <= 1:
            self.remove_item(self.previous_page)
            self.remove_item(self.next_page)
        else:
            self._update_buttons()

    @property
    def total_pages(self) -> int:
        return max(1, (len(self.records) + _PAGE_SIZE - 1) // _PAGE_SIZE)

    def _update_buttons(self):
        self.previous_page.disabled = self.page <= 0
        self.next_page.disabled = self.page >= self.total_pages - 1

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message(
            "Only the support member who ran this search can change pages.",
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
            embed=_history_embed(self.records, self.user, self.page),
            view=self,
        )

    @discord.ui.button(label="Next", emoji="▶️", style=discord.ButtonStyle.primary)
    async def next_page(self, button, interaction: discord.Interaction):
        if not await self._authorized(interaction):
            return
        self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(
            embed=_history_embed(self.records, self.user, self.page),
            view=self,
        )


class OrderSearchUserCog(commands.Cog):
    

    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @discord.slash_command(
        name="order-search-user",
        description="Search saved orders for a customer",
    )
    @discord.option("user", description="Customer whose orders should be searched")
    async def order_search_user(
        self,
        ctx: discord.ApplicationContext,
        user: discord.Member,
    ):
        buy_cog = self.bot.get_cog("BuyCog")
        if buy_cog is None:
            await ctx.respond("The order service is unavailable.", ephemeral=True)
            return
        if not buy_cog._is_support_member(ctx.author):
            await ctx.respond("Only support staff can search orders.", ephemeral=True)
            return

        records = await asyncio.to_thread(buy_cog.orders.find_orders_by_user, user.id)
        if not records:
            await ctx.respond(f"No saved orders found for {user.mention}.", ephemeral=True)
            return

        view = OrderHistoryView(records, user, ctx.author.id)
        await ctx.respond(
            embed=_history_embed(records, user, view.page),
            view=view,
            ephemeral=True,
        )


def setup(bot: discord.Bot):
    bot.add_cog(OrderSearchUserCog(bot))
