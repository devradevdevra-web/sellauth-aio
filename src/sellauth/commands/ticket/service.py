import asyncio
import io
import secrets
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

import discord
from discord import option
from discord.ext import commands

import src.logger.logger as log
from src.config import (
    API_KEY,
    AUTOMATED_CHECKOUT_CONFIG,
    CHECKOUT_CONFIG,
    ORDER_DB_PATH,
    ORDER_ENCRYPTION_KEY,
    SHOP_ID,
    STAFF_ROLE_ID,
    SUPPORT_ROLE_ID,
    TICKET_CATEGORY_ID,
)
from src.orders.database import OrderStore
from src.sellauth.autobuy.checkout import CheckoutAutomation, InsufficientStockError
from src.sellauth.commands.embed_style import styled_embed
from src.sellauth.sellauth import SellAuthAPI


C_PURPLE = 0x8B5CF6
C_PURPLE_DARK = 0x4C1D95
C_GREEN = 0x22C55E
C_RED = 0xEF4444
C_ORANGE = 0xF59E0B
C_BLUE = 0x38BDF8

_BRAND_FOOTER = "SellAuth AIO  •  Secure • Automatic Delivery"
_MAX_CUSTOM_QUANTITY = 1000
_DELIVERY_EMBED_ITEM_LIMIT = 3
_DELIVERY_EMBED_CHAR_LIMIT = 1200
_PAYMENT_VIEW_TIMEOUT = CHECKOUT_CONFIG["payment_timeout_seconds"]
_DETAILS_VIEW_TIMEOUT = 600
_DETAILS_TIMEOUT_MINUTES = max(1, round(_DETAILS_VIEW_TIMEOUT / 60))
_PAYMENT_POLL_INTERVAL = CHECKOUT_CONFIG["payment_poll_interval_seconds"]
_PAYMENT_TIMEOUT = CHECKOUT_CONFIG["payment_timeout_seconds"]
_PAYMENT_TIMEOUT_MINUTES = max(1, round(_PAYMENT_TIMEOUT / 60))
_PAYMENT_POLL_CONCURRENCY = CHECKOUT_CONFIG["payment_poll_concurrency"]
_CHECKOUT_CURRENCY = CHECKOUT_CONFIG["currency"]
_CURRENCY_SELECTION_ENABLED = CHECKOUT_CONFIG["currency_selection_enabled"]
_ALLOWED_CURRENCIES = tuple(CHECKOUT_CONFIG["allowed_currencies"])


def _selectable_currencies(store_currencies) -> list[str]:
    
    available = []
    for item in store_currencies or []:
        currency = str(item).strip().upper()
        if currency and currency not in available:
            available.append(currency)

    if _ALLOWED_CURRENCIES:
        available = [currency for currency in available if currency in _ALLOWED_CURRENCIES]

    if not available:
        raise ValueError("No configured payment currencies are supported by this SellAuth store.")

    if not _CURRENCY_SELECTION_ENABLED:
        return [_CHECKOUT_CURRENCY if _CHECKOUT_CURRENCY in available else available[0]]

    if _CHECKOUT_CURRENCY in available:
        available.remove(_CHECKOUT_CURRENCY)
        available.insert(0, _CHECKOUT_CURRENCY)
    return available[:25]


def _configured_shops(shops) -> list[dict]:
    available = [shop for shop in shops or [] if isinstance(shop, dict)]
    if SHOP_ID is None:
        return available
    return [shop for shop in available if str(shop.get("id")) == str(SHOP_ID)]




def _styled_embed(title: str, description: str = "", color: int = C_PURPLE):
    return styled_embed(title, description, color)


def embed_ticket_panel():

    embed = _styled_embed(
        "`🛍️`  AUTOMATIC STORE",
        "***Choose a product and complete your order in a private ticket.***",
        C_PURPLE_DARK,
    )

    embed.add_field(
        name="✦  How it works",
        value=(
            "`01`  Open a private ticket\n"
            "`02`  Choose your shop, product, and quantity\n"
            "`03`  Pay using the instructions in your ticket\n"
            "`04`  Receive your delivery by DM"
        ),
        inline=False,
    )

    embed.add_field(
        name="`⚡`  Fast & automated",
        value="***Payments are checked automatically. Keep your DMs open for delivery.***",
        inline=False,
    )

    return embed


def embed_order(
    shop_name,
    product_name,
    quantity,
    price,
    address,
    amount,
    currency=None,
    coupon_code=None,
):

    currency = currency or _CHECKOUT_CURRENCY

    embed = _styled_embed(
        "💳  PAYMENT REQUIRED",
        "Your order is ready. Send the **exact amount** to the address below.",
        C_PURPLE,
    )

    embed.add_field(
        name="`🧾` Order summary",
        value=(
            f"**{product_name}** × **{quantity}**\n"
            f"`Shop:` **{shop_name}**  •  `Total:` **${price} USD**"
        ),
        inline=False,
    )
    embed.add_field(
        name=f"`📋` {currency} address",
        value=f"```{address}```",
        inline=False,
    )

    embed.add_field(
        name="`🪙` Exact amount",
        value=f"```{amount} {currency}```",
        inline=False,
    )

    if coupon_code:
        embed.add_field(
            name="`🎟️` Coupon applied",
            value=f"`{str(coupon_code)[:64]}`  •  discount included",
            inline=False,
        )

    embed.add_field(
        name="`🔐` What happens next?",
        value="Payment is detected automatically. Delivery is sent by DM after confirmation.",
        inline=False,
    )

    return embed


def embed_details_required(
    product_name, quantity, field_names, currency=None
):
    required_items = [
        item.strip()
        for item in str(field_names).split(",")
        if item.strip()
    ]
    required_text = "\n".join(f"• `{item}`" for item in required_items)
    description = (
        f"Payment method **{currency}** selected. Complete the information below "
        "to generate your payment instructions."
        if currency
        else "Complete the information below before payment instructions can be generated."
    )

    embed = _styled_embed("📝  COMPLETE ORDER DETAILS", description, C_BLUE)

    embed.add_field(
        name="`📦` Product",
        value=f"**{product_name}**",
        inline=True,
    )
    embed.add_field(
        name="`🔢` Quantity",
        value=f"**{quantity:,}**",
        inline=True,
    )
    if currency:
        embed.add_field(
            name="`💳` Payment method",
            value=f"**{currency}**",
            inline=True,
        )

    embed.add_field(
        name="`📋` Information required",
        value=required_text[:1024] or "`Additional order details required`",
        inline=False,
    )
    embed.add_field(
        name="`➡️` Next step",
        value=(
            "Click **Fill Details** below and submit the requested information. "
            f"This checkout remains active for **{_DETAILS_TIMEOUT_MINUTES} minutes**."
        ),
        inline=False,
    )

    return embed


def embed_currency_selection(currencies):
    methods = "\n".join(
        f"• `{currency}` — Pay using {currency}" for currency in currencies[:25]
    )
    embed = _styled_embed(
        "💳  CHOOSE PAYMENT METHOD",
        "Select a payment method to continue with your order.",
        C_BLUE,
    )
    embed.add_field(
        name="`💰` Available methods",
        value=methods or "`No payment methods available`",
        inline=False,
    )
    embed.add_field(
        name="`➡️` Next step",
        value="Choose one method from the menu below. The required order-details form will appear next.",
        inline=False,
    )
    return embed


def _clean_delivery_item(item) -> str:
    
    return str(item).replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\r").strip()


def _clean_deliveries(delivered) -> list[str]:

    return [cleaned for item in (delivered or []) if (cleaned := _clean_delivery_item(item))]


def _format_deliveries(delivered) -> str:

    return "\n".join(_clean_deliveries(delivered)) or "N/A"


def embed_delivered(product_name, delivered):

    cleaned_deliveries = _clean_deliveries(delivered)

    delivery_text = "\n".join(cleaned_deliveries)

    is_large = (
        len(cleaned_deliveries) > _DELIVERY_EMBED_ITEM_LIMIT
        or len(delivery_text) > _DELIVERY_EMBED_CHAR_LIMIT
    )

    embed = _styled_embed(
        "✅  ORDER DELIVERED",
        "Your payment was confirmed and your order has been delivered.",
        C_GREEN,
    )

    embed.add_field(name="📦 Product", value=f"**{product_name}**", inline=False)
    if is_large:

        delivery_value = (
            f"**{len(cleaned_deliveries)} deliverables are ready.**\n"
            "Use the **Save as TXT** button below to download them."
        )

    else:

        delivery_value = f"```{delivery_text or 'N/A'}```"

    embed.add_field(
        name="🔑 Your delivery",
        value=delivery_value,
        inline=False,
    )

    embed.add_field(
        name="⚠️ Keep it safe",
        value="Use the buttons below to copy the delivery or save it as a text file.",
        inline=False,
    )

    return embed


def embed_purchase_coupon(coupon: dict):

    discount = float(coupon.get("discount", 0))

    expiry = str(coupon.get("expiration_date", "Never")).replace("T", " ")[:19]

    embed = _styled_embed(
        "🎁  THANK YOU — HERE'S A REWARD",
        "Enjoy this discount on your next order. Your code is ready to use whenever you are.",
        C_GREEN,
    )

    embed.add_field(
        name="`🎟️` Your coupon code",
        value=f"```{coupon.get('code', 'N/A')}```",
        inline=False,
    )

    embed.add_field(
        name="`💸` Coupon details",
        value=(
            f"**{discount:g}% off**  •  **{coupon.get('max_uses', 1)} use(s)**\n"
            f"Expires: `{expiry}`"
        ),
        inline=False,
    )

    return embed


def embed_coupon_prompt():

    return _styled_embed(
        "🎟️  USE A COUPON?",
        "Have a coupon? Apply it before payment, or choose **Skip** to continue.",
        C_BLUE,
    )


def embed_no_delivery(product_name):

    embed = _styled_embed(
        "✅  PAYMENT CONFIRMED",
        "Your payment was received successfully.",
        C_GREEN,
    )

    embed.add_field(name="📦 Product", value=f"**{product_name}**", inline=False)
    embed.add_field(
        name="Delivery status",
        value="No deliverable was returned yet. Please contact support if this continues.",
        inline=False,
    )

    return embed


def embed_timeout(checkout_id):

    embed = _styled_embed(
        "⏰  CHECKOUT EXPIRED",
        f"This checkout was not paid within the {_PAYMENT_TIMEOUT_MINUTES}-minute payment window.",
        C_ORANGE,
    )

    embed.add_field(name="🔖 Checkout ID", value=f"`{checkout_id}`", inline=False)
    embed.add_field(
        name="Start again",
        value="Click **Open Purchase Ticket** in the store panel to create a new checkout.",
        inline=False,
    )

    return embed


def embed_error(message):

    embed = _styled_embed(
        "❌  SOMETHING WENT WRONG",
        "The order could not be completed.",
        C_RED,
    )

    embed.add_field(name="Error details", value=f"```{str(message)[:3900]}```", inline=False)

    embed.add_field(
        name="Need help?",
        value="Try again or contact a support member with the checkout ID.",
        inline=False,
    )

    return embed


def embed_stock_unavailable(product_name: str, requested: int, available: int):

    if available > 0:
        description = (
            f"Only `{available:,}` item(s) are currently available. "
            "Please choose a smaller quantity and try again."
        )

    else:

        description = "This product is currently out of stock. Please choose another product."

    embed = _styled_embed(
        "`⚠️` **NOT ENOUGH STOCK**",
        description,
        C_ORANGE,
    )

    embed.add_field(name="`📦` Product", value=f"`{product_name}`", inline=False)
    embed.add_field(name="`🛒` Requested", value=f"`{requested:,}`", inline=True)
    embed.add_field(name="`📈` Available", value=f"`{available:,}`", inline=True)
    if available > 0:

        embed.add_field(
            name="`✅` Next step",
            value=f"Choose a quantity from `1` to `{available:,}` in this ticket.",
            inline=False,
        )

    return embed


def embed_ticket_closed(reason: str):

    embed = _styled_embed(
        "🔒  TICKET CLOSED",
        "This ticket is locked and can no longer receive messages from the customer.",
        C_ORANGE,
    )

    embed.add_field(name="Reason", value=reason, inline=False)

    embed.add_field(
        name="Support controls",
        value="A support member can reopen the ticket or delete it permanently.",
        inline=False,
    )

    return embed




async def _dm(user: discord.User, **kwargs) -> bool:

    try:

        await user.send(**kwargs)

        return True

    except (discord.Forbidden, discord.HTTPException):

        return False



class PaymentView(discord.ui.View):

    def __init__(self, address: str, amount: str):

        super().__init__(timeout=_PAYMENT_VIEW_TIMEOUT)
        self.address = address
        self.amount = amount

    @discord.ui.button(label="Copy Address", emoji="📋", style=discord.ButtonStyle.secondary)
    async def copy_address(self, button, interaction: discord.Interaction):

        await interaction.response.send_message(self.address, ephemeral=True)

    @discord.ui.button(label="Copy Amount", emoji="🪙", style=discord.ButtonStyle.secondary)
    async def copy_amount(self, button, interaction: discord.Interaction):

        await interaction.response.send_message(self.amount, ephemeral=True)


class CouponCopyView(discord.ui.View):

    def __init__(self, code: str, user_id: int):

        super().__init__(timeout=900)
        self.code = code
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:

        if interaction.user.id == self.user_id:
            return True

        await interaction.response.send_message(
            "This coupon belongs to another customer.", ephemeral=True
        )
        return False

    @discord.ui.button(label="Copy Code", emoji="📋", style=discord.ButtonStyle.primary)
    async def copy_code(self, button, interaction: discord.Interaction):

        await interaction.response.send_message(self.code, ephemeral=True)


CouponSubmitter = Callable[[discord.Interaction, Optional[str]], Awaitable[None]]


class CouponChoiceView(discord.ui.View):

    def __init__(self, user_id: int, submitter: CouponSubmitter):

        super().__init__(timeout=_DETAILS_VIEW_TIMEOUT)
        self.user_id = user_id
        self.submitter = submitter

    async def interaction_check(self, interaction: discord.Interaction) -> bool:

        if interaction.user.id == self.user_id:
            return True

        await interaction.response.send_message(
            "Only the customer who opened this order can choose a coupon.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="Use Coupon", emoji="🎟️", style=discord.ButtonStyle.success)
    async def use_coupon(self, button, interaction: discord.Interaction):

        await interaction.response.send_modal(CouponCodeModal(self))

    @discord.ui.button(label="Skip", emoji="➡️", style=discord.ButtonStyle.secondary)
    async def skip_coupon(self, button, interaction: discord.Interaction):

        await interaction.response.defer(ephemeral=True)
        self.stop()
        try:
            await self.submitter(interaction, None)
        except Exception as exc:
            log.capchaerror(
                "Checkout without coupon failed",
                [("Error", str(exc))],
            )
            await interaction.followup.send(embed=embed_error(str(exc)), ephemeral=True)


class CouponCodeModal(discord.ui.Modal):

    def __init__(self, choice_view: CouponChoiceView):

        super().__init__(title="Enter Coupon Code")
        self.choice_view = choice_view
        self.code_input = discord.ui.InputText(
            label="Coupon code",
            placeholder="Enter your SellAuth coupon code",
            required=True,
        )
        self.add_item(self.code_input)

    async def callback(self, interaction: discord.Interaction):

        code = str(self.code_input.value or "").strip().upper()
        if not code:
            await interaction.response.send_message(
                "Enter a coupon code or choose Skip.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await self.choice_view.submitter(interaction, code)
            self.choice_view.stop()
        except Exception as exc:
            log.capchaerror(
                "Coupon application failed",
                [("Error", str(exc))],
            )
            await interaction.followup.send(
                embed=embed_error(
                    f"That coupon could not be applied. Check the code and try again.\n\n{str(exc)[:800]}"
                ),
                ephemeral=True,
            )


class DeliveryView(discord.ui.View):

    def __init__(self, delivered: list, product_name: str):

        super().__init__(timeout=0)

        self.delivered = _clean_deliveries(delivered)
        self.product_name = product_name

        self.is_large = (
            len(self.delivered) > _DELIVERY_EMBED_ITEM_LIMIT
            or len("\n".join(self.delivered)) > _DELIVERY_EMBED_CHAR_LIMIT
        )

        if self.is_large:

            copy_button = next(
                (
                    item
                    for item in self.children
                    if getattr(item, "label", "") == "Copy Deliverables"
                ),
                None,
            )

            if copy_button is not None:

                self.remove_item(copy_button)

    @discord.ui.button(label="Copy Deliverables", emoji="📋", style=discord.ButtonStyle.secondary)
    async def copy_deliverables(self, button, interaction: discord.Interaction):

        await interaction.response.send_message(
            f"```\n{chr(10).join(self.delivered)}\n```", ephemeral=True
        )

    @discord.ui.button(label="Save as TXT", emoji="💾", style=discord.ButtonStyle.secondary)
    async def save_txt(self, button, interaction: discord.Interaction):

        content = "\n".join(self.delivered).encode("utf-8")

        file = discord.File(fp=io.BytesIO(content), filename="delivery.txt")

        await interaction.response.send_message(file=file, ephemeral=True)


async def _offer_coupon(
    interaction,
    *,
    channel,
    user,
    client,
    checkout_id,
    currency,
    product_name,
    quantity,
    shop_name,
    shop_id,
    cog,
    order_id,
    custom_fields=None,
):

    async def submit(submit_interaction, coupon_code):
        await _submit_checkout_details(
            submit_interaction,
            client,
            checkout_id,
            currency,
            user,
            product_name,
            quantity,
            shop_name,
            shop_id,
            channel,
            cog,
            order_id,
            custom_fields=custom_fields,
            coupon_code=coupon_code,
        )

    await channel.send(
        embed=embed_coupon_prompt(),
        view=CouponChoiceView(user.id, submit),
    )
    await interaction.followup.send(
        "Your details are ready. Choose whether you want to use a coupon.",
        ephemeral=True,
    )


async def _submit_checkout_details(
    interaction,
    client,
    checkout_id,
    currency,
    user,
    product_name,
    quantity,
    shop_name,
    shop_id,
    channel,
    cog,
    order_id,
    custom_fields=None,
    coupon_code=None,
):

    coupon_code = str(coupon_code or "").strip().upper() or None
    await asyncio.to_thread(
        client.submit_details,
        checkout_id,
        currency,
        coupon=coupon_code,
    )
    if custom_fields:
        await asyncio.to_thread(client.submit_custom_fields, checkout_id, custom_fields)

    details = await asyncio.to_thread(client.get_payment_details, checkout_id)
    address = details.get("crypto_address", "N/A")
    amount = details.get("crypto_amount", "N/A")
    price = details.get("price_usd", "N/A")

    await _order_update(
        cog,
        order_id,
        status="payment_pending",
        checkout_id=checkout_id,
        payload_updates={
            "custom_fields": custom_fields or {},
            "coupon_code": coupon_code,
            "payment": {
                "currency": currency,
                "crypto_address": address,
                "crypto_amount": amount,
                "price_usd": price,
            },
        },
    )
    if coupon_code:
        await _order_event(cog, order_id, "coupon_applied", reason=coupon_code)
    await _order_event(cog, order_id, "payment_details_created", actor_id=user.id)

    log.success(
        "Checkout ready",
        [
            ("Checkout", checkout_id),
            ("Currency", currency),
            ("Coupon", coupon_code or "None"),
            ("Address", address),
            ("Amount", f"{amount} {currency}"),
        ],
        trim=False,
    )

    await interaction.followup.send(
        "✅ Checkout ready. Payment instructions were posted in this ticket.",
        ephemeral=True,
    )

    if channel:
        await channel.send(
            embed=embed_order(
                shop_name,
                product_name,
                quantity,
                price,
                address,
                amount,
                currency=currency,
                coupon_code=coupon_code,
            ),
            view=PaymentView(address, amount),
        )

    cog.start_delivery(
        order_id,
        _wait_and_deliver(
            user,
            client,
            checkout_id,
            product_name,
            shop_id=shop_id,
            channel=channel,
            cog=cog,
            order_id=order_id,
        ),
    )


class CurrencySelect(discord.ui.Select):

    def __init__(self, parent, currencies):
        self.parent_view = parent
        super().__init__(
            placeholder="What payment method would you like to use?",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=currency,
                    value=currency,
                    description=f"Pay using {currency}",
                )
                for currency in currencies
            ],
            custom_id=f"currency_select:{parent.checkout_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.user.id:
            await interaction.response.send_message(
                "Only the ticket owner can choose a payment currency.", ephemeral=True
            )
            return
        self.parent_view.currency = self.values[0]
        await self.parent_view.currency_selected(interaction)


class CurrencySelectionView(discord.ui.View):

    def __init__(
        self,
        currencies,
        client,
        checkout_id,
        user,
        product_name,
        quantity,
        shop_name,
        shop_id,
        channel,
        cog,
        order_id,
        required_fields=None,
    ):
        super().__init__(timeout=_DETAILS_VIEW_TIMEOUT)
        self.user = user
        self.currency = None
        self.checkout_id = checkout_id
        self.client = client
        self.product_name = product_name
        self.quantity = quantity
        self.shop_name = shop_name
        self.shop_id = shop_id
        self.channel = channel
        self.cog = cog
        self.order_id = order_id
        self.required_fields = list(required_fields or [])
        self.add_item(CurrencySelect(self, currencies))

    async def currency_selected(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        self.stop()
        try:
            if self.required_fields:
                field_names = ", ".join(
                    field["name"] for field in self.required_fields
                )
                await self.channel.send(
                    embed=embed_details_required(
                        self.product_name,
                        self.quantity,
                        field_names,
                        currency=self.currency,
                    ),
                    view=FillDetailsView(
                        required_fields=self.required_fields,
                        client=self.client,
                        checkout_id=self.checkout_id,
                        user=self.user,
                        product_name=self.product_name,
                        quantity=self.quantity,
                        shop_name=self.shop_name,
                        shop_id=self.shop_id,
                        channel=self.channel,
                        cog=self.cog,
                        order_id=self.order_id,
                        currency_options=[self.currency],
                    ),
                )
                await interaction.followup.send(
                    "✅ Payment method selected. Fill in the order details below.",
                    ephemeral=True,
                )
                return

            await _offer_coupon(
                interaction,
                channel=self.channel,
                user=self.user,
                client=self.client,
                checkout_id=self.checkout_id,
                currency=self.currency,
                product_name=self.product_name,
                quantity=self.quantity,
                shop_name=self.shop_name,
                shop_id=self.shop_id,
                cog=self.cog,
                order_id=self.order_id,
            )
        except Exception as exc:
            log.capchaerror(
                "Currency selection failed",
                [("Checkout", self.checkout_id), ("Error", str(exc))],
            )
            await interaction.followup.send(embed=embed_error(str(exc)), ephemeral=True)


class FillDetailsView(discord.ui.View):

    def __init__(
        self,
        required_fields,
        client,
        checkout_id,
        user,
        product_name,
        quantity,
        shop_name,
        shop_id=None,
        channel=None,
        cog=None,
        order_id=None,
        currency_options=None,
    ):

        super().__init__(timeout=_DETAILS_VIEW_TIMEOUT)

        self.required_fields = required_fields
        self.client = client

        self.checkout_id = checkout_id
        self.user = user
        self.product_name = product_name

        self.quantity = quantity

        self.shop_name = shop_name

        self.shop_id = shop_id

        self.channel = channel

        self.cog = cog
        self.order_id = order_id
        self.currency_options = list(currency_options or [_CHECKOUT_CURRENCY])
        self.currency = self.currency_options[0] if len(self.currency_options) == 1 else None

        if len(self.currency_options) > 1:
            self.add_item(CurrencySelect(self, self.currency_options))
            self.fill_details.disabled = True

    async def currency_selected(self, interaction: discord.Interaction):
        self.fill_details.disabled = False
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Fill Details", emoji="📝", style=discord.ButtonStyle.primary)
    async def fill_details(self, button, interaction: discord.Interaction):
        if not self.currency:
            await interaction.response.send_message(
                "Choose a payment method first.", ephemeral=True
            )
            return

        modal = CustomFieldsModal(
            required_fields=self.required_fields,
            client=self.client,
            checkout_id=self.checkout_id,
            user=self.user,
            product_name=self.product_name,
            quantity=self.quantity,
            shop_name=self.shop_name,
            shop_id=self.shop_id,
            channel=self.channel,
            cog=self.cog,
            order_id=self.order_id,
            currency=self.currency,
        )
        await interaction.response.send_modal(modal)
        self.stop()




class CustomFieldsModal(discord.ui.Modal):

    def __init__(
        self,
        required_fields,
        client,
        checkout_id,
        user,
        product_name,
        quantity,
        shop_name,
        shop_id=None,
        channel=None,
        cog=None,
        order_id=None,
        currency=None,
    ):

        super().__init__(title="Order Details")

        self.required_fields = required_fields
        self.client = client

        self.checkout_id = checkout_id
        self.user = user
        self.product_name = product_name

        self.quantity = quantity

        self.shop_name = shop_name

        self.shop_id = shop_id

        self.channel = channel

        self.cog = cog
        self.order_id = order_id
        self.currency = currency or _CHECKOUT_CURRENCY

        for field in required_fields[:5]:

            self.add_item(
                discord.ui.InputText(
                    label=field["name"],
                    placeholder=f"Enter {field['name']}",
                    required=True,
                    custom_id=f"{field['item_id']}:{field['field_id']}",
                )
            )

    async def callback(self, interaction: discord.Interaction):

        await interaction.response.defer(ephemeral=True)

        try:

            field_values: dict = {}

            for child in self.children:

                item_id_str, field_id_str = child.custom_id.split(":")

                field_values.setdefault(int(item_id_str), {})[int(field_id_str)] = child.value

            await _offer_coupon(
                interaction,
                channel=self.channel,
                user=self.user,
                client=self.client,
                checkout_id=self.checkout_id,
                currency=self.currency,
                product_name=self.product_name,
                quantity=self.quantity,
                shop_name=self.shop_name,
                shop_id=self.shop_id,
                cog=self.cog,
                order_id=self.order_id,
                custom_fields=field_values,
            )

        except Exception as e:

            log.capchaerror(
                "Modal callback failed",
                [
                    ("Checkout", self.checkout_id),
                    ("Error", str(e)),
                ],
            )

            await interaction.followup.send(embed=embed_error(str(e)), ephemeral=True)



async def _close_ticket(channel, cog, owner_id: int, reason: str, order_id=None):

    await _order_event(cog, order_id, "ticket_closed", actor_id=owner_id, reason=reason)

    if channel is None or cog is None:
        return

    try:

        cog._tickets.pop(owner_id, None)

        await _set_ticket_locked(channel, owner_id, locked=True)

        control_message = await channel.send(
            embed=embed_ticket_closed(reason),
            view=ClosedTicketView(cog, owner_id, order_id),
        )

        await _order_update(
            cog,
            order_id,
            payload_updates={"control_message_id": str(control_message.id)},
        )

    except discord.NotFound:
        pass

    except discord.Forbidden:

        log.warn("Could not lock completed ticket", [("Channel", str(channel.id))])


def _coupon_user_slug(user) -> str:
    

    username = getattr(user, "name", None) or getattr(user, "display_name", None)

    if not username:

        username = f"user-{getattr(user, 'id', 'customer')}"

    slug = "".join(
        character if character.isascii() and character.isalnum() else "-"
        for character in str(username)
    ).strip("-")

    return slug[:24] or "customer"


async def _create_purchase_coupon(
    cog,
    shop_id: int,
    order_id: Optional[str],
    user=None,
) -> dict:
    
    if not AUTOMATED_CHECKOUT_CONFIG.get("enabled"):

        return None

    if not cog or not getattr(cog, "_api", None):

        raise RuntimeError("SellAuth API is unavailable for purchase coupon creation.")

    if shop_id is None:

        raise ValueError("The completed order has no shop ID for coupon creation.")

    random_suffix = secrets.token_hex(4).upper()

    username_part = (
        f"-{_coupon_user_slug(user)}"
        if AUTOMATED_CHECKOUT_CONFIG.get("include_discord_username")
        else ""
    )

    code = f"{AUTOMATED_CHECKOUT_CONFIG['code_prefix']}{username_part}-{random_suffix}"

    expiration = datetime.now(timezone.utc) + timedelta(
        days=AUTOMATED_CHECKOUT_CONFIG["valid_for_days"]
    )

    payload = {
        "code": code,
        "global": AUTOMATED_CHECKOUT_CONFIG["apply_globally"],
        "discount": AUTOMATED_CHECKOUT_CONFIG["discount_percent"],
        "type": "percentage",
        "max_uses": AUTOMATED_CHECKOUT_CONFIG["max_uses"],
        "expiration_date": expiration.isoformat(),
        "disable_if_volume_discount": False,
    }

    await asyncio.to_thread(cog._api.create_coupon, int(shop_id), **payload)

    return {
        "code": code,
        "discount": AUTOMATED_CHECKOUT_CONFIG["discount_percent"],
        "max_uses": AUTOMATED_CHECKOUT_CONFIG["max_uses"],
        "expiration_date": expiration.isoformat(),
    }


async def _wait_and_deliver(
    user,
    client,
    checkout_id,
    product_name,
    *,
    shop_id=None,
    channel=None,
    cog=None,
    order_id=None,
):

    try:

        delivered = await client.wait_for_payment_async(
            checkout_id,
            _PAYMENT_POLL_INTERVAL,
            _PAYMENT_TIMEOUT,
            request_semaphore=(cog.get_payment_poll_semaphore() if cog else None),
        )

        await _order_update(
            cog,
            order_id,
            status="payment_completed",
            payload_updates={"payment_completed": True},
            checkout_id=checkout_id,
        )

        await _order_event(cog, order_id, "payment_completed")

        purchase_coupon = None
        if AUTOMATED_CHECKOUT_CONFIG.get("enabled"):

            try:

                purchase_coupon = await _create_purchase_coupon(cog, shop_id, order_id, user)

                await _order_update(
                    cog,
                    order_id,
                    payload_updates={"purchase_coupon": {**purchase_coupon, "created": True}},
                )

                await _order_event(cog, order_id, "purchase_coupon_created")

            except Exception as exc:

                log.capchaerror(
                    "Purchase coupon creation failed",
                    [("Checkout", checkout_id), ("Error", str(exc))],
                )

                await _order_update(
                    cog,
                    order_id,
                    payload_updates={
                        "purchase_coupon": {"created": False, "error": str(exc)[:500]}
                    },
                )

                await _order_event(
                    cog,
                    order_id,
                    "purchase_coupon_failed",
                    reason=str(exc)[:500],
                )

        if not delivered:

            log.warn("Payment confirmed but no deliverables", [("Checkout", checkout_id)])

            await _order_update(
                cog,
                order_id,
                status="paid_no_delivery",
                payload_updates={"delivery": [], "delivery_fetched": True},
            )

            await _dm(user, embed=embed_no_delivery(product_name))

            if purchase_coupon:

                coupon_dm_sent = await _dm(
                    user,
                    embed=embed_purchase_coupon(purchase_coupon),
                    view=CouponCopyView(
                        str(purchase_coupon.get("code", "")),
                        user.id,
                    ),
                )

                await _order_update(
                    cog,
                    order_id,
                    payload_updates={
                        "purchase_coupon": {
                            **purchase_coupon,
                            "created": True,
                            "dm_sent": coupon_dm_sent,
                        }
                    },
                )

                await _order_event(
                    cog,
                    order_id,
                    "purchase_coupon_dm_sent" if coupon_dm_sent else "purchase_coupon_dm_failed",
                )

            await _close_ticket(
                channel,
                cog,
                user.id,
                "Payment confirmed; no deliverables were returned.",
                order_id,
            )
            return

        log.success(
            "Order delivered",
            [
                ("Checkout", checkout_id),
                ("Items", str(len(delivered))),
                ("User", str(user)),
            ],
        )

        delivery_embed = embed_delivered(product_name, delivered)

        dm_sent = await _dm(
            user,
            embed=delivery_embed,
            view=DeliveryView(delivered=delivered, product_name=product_name),
        )

        if dm_sent:

            await _order_update(
                cog,
                order_id,
                status="delivered",
                payload_updates={"delivery": delivered, "delivery_fetched": True, "dm_sent": True},
            )

            await _order_event(cog, order_id, "delivery_dm_sent")

            close_reason = "Order completed and delivery was sent by DM."

        else:

            await _order_update(
                cog,
                order_id,
                status="delivery_available",
                payload_updates={"delivery": delivered, "delivery_fetched": True, "dm_sent": False},
            )

            await _order_event(
                cog, order_id, "delivery_dm_failed", reason="Discord DM was unavailable"
            )

            close_reason = (
                "Payment confirmed, but delivery could not be sent by DM. Contact support."
            )

        if purchase_coupon:

            coupon_dm_sent = await _dm(
                user,
                embed=embed_purchase_coupon(purchase_coupon),
                view=CouponCopyView(
                    str(purchase_coupon.get("code", "")),
                    user.id,
                ),
            )

            await _order_update(
                cog,
                order_id,
                payload_updates={
                    "purchase_coupon": {
                        **purchase_coupon,
                        "created": True,
                        "dm_sent": coupon_dm_sent,
                    }
                },
            )

            await _order_event(
                cog,
                order_id,
                "purchase_coupon_dm_sent" if coupon_dm_sent else "purchase_coupon_dm_failed",
            )

        await _close_ticket(channel, cog, user.id, close_reason, order_id)

    except TimeoutError:

        log.capchaerror("Checkout timed out", [("Checkout", checkout_id)])

        await _order_update(
            cog, order_id, status="expired", payload_updates={"payment_completed": False}
        )

        await _order_event(cog, order_id, "checkout_expired")

        await _dm(user, embed=embed_timeout(checkout_id))

        await _close_ticket(channel, cog, user.id, "Checkout expired without payment.", order_id)

    except Exception as e:

        log.capchaerror("Delivery poller failed", [("Checkout", checkout_id), ("Error", str(e))])

        await _order_update(
            cog, order_id, status="checkout_error", payload_updates={"error": str(e)}
        )

        await _order_event(cog, order_id, "checkout_error", reason=str(e)[:500])

        await _dm(user, embed=embed_error(str(e)))



class TicketShopSelect(discord.ui.Select):

    def __init__(self, view):

        self.ticket_view = view

        options = [
            discord.SelectOption(
                label=str(shop.get("name", "Unnamed Shop"))[:100],
                value=str(shop.get("id")),
            )
            for shop in view.shops[:25]
            if shop.get("id") is not None
        ]
        super().__init__(
            placeholder="Choose a shop",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="ticket_shop_select",
        )

    async def callback(self, interaction: discord.Interaction):

        view = self.ticket_view

        if not await view.owner_only(interaction):
            return

        await interaction.response.defer()

        shop_id = int(self.values[0])

        shop = next(shop for shop in view.shops if str(shop.get("id")) == str(shop_id))

        try:

            products = await asyncio.to_thread(view.cog._api.fetch_products, shop_id)

            product_options = []

            for name, product in products.items():

                variants = product.get("variant_ids", [])

                if variants:
                    product_options.append(
                        discord.SelectOption(
                            label=str(name)[:100],
                            value=f"{product['product_id']}:{variants[0]}",
                        )
                    )

            view.selected_shop = shop

            view.selected_product = None

            view.products = products

            view.quantity_select.disabled = True

            view.product_select.options = product_options[:25] or [
                discord.SelectOption(label="No products available", value="none")
            ]

            view.product_select.disabled = not bool(product_options)

            await interaction.edit_original_response(view=view)

            await interaction.followup.send(
                f"Shop selected: **{shop.get('name', 'Unnamed Shop')}**. " "Now choose a product.",
                ephemeral=True,
            )

        except Exception as exc:

            await interaction.followup.send(embed=embed_error(str(exc)), ephemeral=True)


class TicketProductSelect(discord.ui.Select):

    def __init__(self, view):

        self.ticket_view = view
        super().__init__(
            placeholder="Choose a product after selecting a shop",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label="Choose a shop first", value="none")],
            disabled=True,
            custom_id="ticket_product_select",
        )

    async def callback(self, interaction: discord.Interaction):

        view = self.ticket_view

        if not await view.owner_only(interaction):
            return

        if self.values[0] == "none":

            await interaction.response.send_message("Choose a valid product.", ephemeral=True)
            return

        product_id, variant_id = (int(value) for value in self.values[0].split(":"))

        view.selected_product = (product_id, variant_id)

        view.quantity_select.disabled = False

        await interaction.response.edit_message(view=view)

        await interaction.followup.send(
            "Product selected. Now choose a quantity to create the order.",
            ephemeral=True,
        )


class CustomQuantityModal(discord.ui.Modal):

    def __init__(self, ticket_view):

        super().__init__(title="Custom Order Amount")

        self.ticket_view = ticket_view

        self.amount = discord.ui.InputText(
            label="Quantity",
            placeholder=f"Enter a number from 1 to {_MAX_CUSTOM_QUANTITY}",
            required=True,
            max_length=5,
        )
        self.add_item(self.amount)

    async def callback(self, interaction: discord.Interaction):

        raw_amount = self.amount.value.strip().replace(",", "")

        if not raw_amount.isdigit():

            await interaction.response.send_message(
                "Please enter a whole number, such as `25`.",
                ephemeral=True,
            )
            return

        quantity = int(raw_amount)

        if quantity < 1 or quantity > _MAX_CUSTOM_QUANTITY:

            await interaction.response.send_message(
                f"Quantity must be between **1** and **{_MAX_CUSTOM_QUANTITY:,}**.",
                ephemeral=True,
            )
            return

        view = self.ticket_view

        if view.selected_product is None:

            await interaction.response.send_message(
                "Choose a product before entering a quantity.",
                ephemeral=True,
            )
            return

        view.quantity = quantity

        product_id, variant_id = view.selected_product

        await interaction.response.defer(ephemeral=True)

        await view.begin_checkout(interaction, product_id, variant_id)


class TicketQuantitySelect(discord.ui.Select):

    def __init__(self, view):

        self.ticket_view = view
        super().__init__(
            placeholder="Quantity: 1",
            min_values=1,
            max_values=1,
            options=(
                [
                    discord.SelectOption(label=str(quantity), value=str(quantity))
                    for quantity in range(1, 11)
                ]
                + [
                    discord.SelectOption(
                        label="Custom amount",
                        value="custom",
                        emoji="✏️",
                    )
                ]
            ),
            custom_id="ticket_quantity_select",
        )

    async def callback(self, interaction: discord.Interaction):

        view = self.ticket_view

        if not await view.owner_only(interaction):
            return

        if view.selected_product is None:

            await interaction.response.send_message(
                "Choose a product before selecting a quantity.", ephemeral=True
            )
            return

        if self.values[0] == "custom":

            await interaction.response.send_modal(CustomQuantityModal(view))
            return

        view.quantity = int(self.values[0])

        product_id, variant_id = view.selected_product

        await interaction.response.defer()

        await view.begin_checkout(interaction, product_id, variant_id)


async def _set_ticket_locked(channel, owner_id: int, locked: bool):

    owner = channel.guild.get_member(owner_id)

    if owner:

        await channel.set_permissions(
            owner,
            view_channel=True,
            read_message_history=True,
            send_messages=not locked,
            add_reactions=not locked,
        )


def _is_support(interaction: discord.Interaction) -> bool:

    if interaction.user.guild_permissions.manage_channels:

        return True

    return bool(
        SUPPORT_ROLE_ID
        and any(role.id == SUPPORT_ROLE_ID for role in getattr(interaction.user, "roles", []))
    )


class OrderIssueModal(discord.ui.Modal):

    def __init__(self, cog, owner_id: int, order_id=None):

        super().__init__(title="Report an Order Issue")

        self.cog = cog

        self.owner_id = owner_id
        self.order_id = order_id

        self.reason = discord.ui.InputText(
            label="What went wrong?",
            placeholder="Explain the issue so support can investigate it.",
            style=discord.InputTextStyle.paragraph,
            required=True,
            max_length=1000,
        )
        self.add_item(self.reason)

    async def callback(self, interaction: discord.Interaction):

        await interaction.response.defer(ephemeral=True)

        issue = self.reason.value.strip()

        await _order_update(
            self.cog,
            self.order_id,
            status="support_reopened",
            payload_updates={"support_issue": issue},
        )

        await _order_event(
            self.cog,
            self.order_id,
            "customer_reported_issue",
            actor_id=interaction.user.id,
            reason=issue,
        )

        await _set_ticket_locked(interaction.channel, self.owner_id, locked=False)

        self.cog._tickets[self.owner_id] = interaction.channel.id

        support_role = (
            interaction.guild.get_role(SUPPORT_ROLE_ID)
            if SUPPORT_ROLE_ID and interaction.guild
            else None
        )

        content = f"{support_role.mention} " if support_role else ""

        content += f"{interaction.user.mention} reported an order issue."

        control_message = await interaction.channel.send(
            content=content,
            embed=_styled_embed(
                "🆘  SUPPORT REQUIRED",
                "The ticket was reopened for support. Please investigate the issue below.",
                C_RED,
            ).add_field(name="Customer report", value=issue[:1024], inline=False),
            view=ReopenedTicketView(self.cog, self.owner_id, self.order_id),
        )

        await _order_update(
            self.cog,
            self.order_id,
            payload_updates={"control_message_id": str(control_message.id)},
        )

        await interaction.followup.send(
            "Support has been notified and this ticket was reopened.", ephemeral=True
        )


class ClosedTicketView(discord.ui.View):

    def __init__(self, cog, owner_id: int, order_id=None):

        super().__init__(timeout=None)

        self.cog = cog

        self.owner_id = owner_id
        self.order_id = order_id

        if order_id:

            for child in self.children:

                child.custom_id = f"{child.custom_id}:{order_id}"

    @discord.ui.button(
        label="Reopen Ticket",
        emoji="🔓",
        style=discord.ButtonStyle.primary,
        custom_id="ticket_reopen",
    )
    async def reopen_ticket(self, button, interaction: discord.Interaction):

        if not _is_support(interaction):

            await interaction.response.send_message(
                "Only support staff can reopen a ticket.", ephemeral=True
            )
            return

        await _set_ticket_locked(interaction.channel, self.owner_id, locked=False)

        self.cog._tickets[self.owner_id] = interaction.channel.id

        await _order_update(self.cog, self.order_id, status="support_reopened")

        await _order_event(
            self.cog, self.order_id, "support_reopened", actor_id=interaction.user.id
        )

        support_role = (
            interaction.guild.get_role(SUPPORT_ROLE_ID)
            if SUPPORT_ROLE_ID and interaction.guild
            else None
        )

        if support_role:

            await interaction.channel.send(
                content=f"{support_role.mention} Support reopened order `{self.order_id or 'unknown'}` for review."
            )

        await interaction.response.edit_message(
            embed=_styled_embed(
                "🔓  TICKET REOPENED",
                "Support reopened this ticket. The customer can send messages again.",
                C_BLUE,
            ),
            view=ReopenedTicketView(self.cog, self.owner_id, self.order_id),
        )

    @discord.ui.button(
        label="Report Order Issue",
        emoji="🆘",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket_report_issue",
    )
    async def report_issue(self, button, interaction: discord.Interaction):

        if interaction.user.id != self.owner_id:

            await interaction.response.send_message(
                "Only the ticket owner can report this order issue.", ephemeral=True
            )
            return

        await interaction.response.send_modal(
            OrderIssueModal(self.cog, self.owner_id, self.order_id)
        )

    @discord.ui.button(
        label="Delete Ticket",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_delete",
    )
    async def delete_ticket(self, button, interaction: discord.Interaction):

        if not _is_support(interaction):

            await interaction.response.send_message(
                "Only support staff can delete a ticket.", ephemeral=True
            )
            return

        if self.order_id:

            try:

                await asyncio.to_thread(
                    self.cog.orders.soft_delete_order,
                    self.order_id,
                    actor_id=interaction.user.id,
                    reason="Ticket deleted by support",
                )

            except Exception as exc:

                log.capchaerror(
                    "Order tombstone failed", [("Order", str(self.order_id)), ("Error", str(exc))]
                )

        await interaction.response.send_message("Deleting ticket...", ephemeral=True)

        self.cog._tickets.pop(self.owner_id, None)

        await interaction.channel.delete(reason="Ticket deleted by support")


class ReopenedTicketView(discord.ui.View):

    def __init__(self, cog, owner_id: int, order_id=None):

        super().__init__(timeout=None)

        self.cog = cog

        self.owner_id = owner_id
        self.order_id = order_id

        if order_id:

            for child in self.children:

                child.custom_id = f"{child.custom_id}:{order_id}"

    @discord.ui.button(
        label="Close Ticket",
        emoji="🔒",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket_close_reopened",
    )
    async def close_ticket(self, button, interaction: discord.Interaction):

        if not _is_support(interaction):

            await interaction.response.send_message(
                "Only support staff can close this ticket.", ephemeral=True
            )
            return

        await _set_ticket_locked(interaction.channel, self.owner_id, locked=True)

        await _order_update(self.cog, self.order_id, status="ticket_closed")

        await _order_event(
            self.cog, self.order_id, "support_closed_ticket", actor_id=interaction.user.id
        )

        await interaction.response.edit_message(
            embed=embed_ticket_closed("Closed by support."),
            view=ClosedTicketView(self.cog, self.owner_id, self.order_id),
        )

    @discord.ui.button(
        label="Delete Ticket",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_delete_reopened",
    )
    async def delete_ticket(self, button, interaction: discord.Interaction):

        if not _is_support(interaction):

            await interaction.response.send_message(
                "Only support staff can delete a ticket.", ephemeral=True
            )
            return

        if self.order_id:

            try:

                await asyncio.to_thread(
                    self.cog.orders.soft_delete_order,
                    self.order_id,
                    actor_id=interaction.user.id,
                    reason="Ticket deleted by support",
                )

            except Exception as exc:

                log.capchaerror(
                    "Order tombstone failed", [("Order", str(self.order_id)), ("Error", str(exc))]
                )

        await interaction.response.send_message("Deleting ticket...", ephemeral=True)

        self.cog._tickets.pop(self.owner_id, None)

        await interaction.channel.delete(reason="Ticket deleted by support")


class TicketView(discord.ui.View):

    def __init__(
        self, cog, owner: discord.Member, shops: list, order_id=None, panel_message_id=None
    ):

        super().__init__(timeout=None)

        self.cog = cog

        self.owner = owner

        self.shops = shops
        self.order_id = order_id

        self.panel_message_id = panel_message_id

        self.selected_shop = None

        self.selected_product = None

        self.products = {}

        self.quantity = 1

        self.shop_select = TicketShopSelect(self)

        self.product_select = TicketProductSelect(self)

        self.quantity_select = TicketQuantitySelect(self)

        self.quantity_select.disabled = True

        self.add_item(self.shop_select)
        self.add_item(self.product_select)

        self.add_item(self.quantity_select)

        if order_id:

            for child in self.children:

                child.custom_id = f"{child.custom_id}:{order_id}"

    async def owner_only(self, interaction: discord.Interaction) -> bool:

        if interaction.user.id == self.owner.id:

            return True

        await interaction.response.send_message(
            "Only the ticket owner can use this purchase panel.", ephemeral=True
        )

        return False

    def enable_selection_controls(self) -> None:

        self.shop_select.disabled = False

        self.product_select.disabled = False

        self.quantity_select.disabled = self.selected_product is None

    async def refresh_panel(self, interaction: discord.Interaction | None = None) -> None:
        

        channel = getattr(interaction, "channel", None)

        if self.panel_message_id and channel is not None:

            try:

                panel = await channel.fetch_message(int(self.panel_message_id))

                await panel.edit(view=self)
                return

            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        if interaction is not None:

            try:

                await interaction.edit_original_response(view=self)

            except discord.HTTPException:
                pass

    @discord.ui.button(
        label="Close Ticket", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="ticket_close"
    )
    async def close_ticket(self, button, interaction: discord.Interaction):

        if interaction.user.id != self.owner.id and not _is_support(interaction):

            await interaction.response.send_message("You cannot close this ticket.", ephemeral=True)
            return

        await _set_ticket_locked(interaction.channel, self.owner.id, locked=True)

        await _order_update(self.cog, self.order_id, status="ticket_closed")

        await _order_event(
            self.cog, self.order_id, "ticket_closed_by_user", actor_id=interaction.user.id
        )

        await interaction.response.send_message("Ticket locked.", ephemeral=True)

        control_message = await interaction.channel.send(
            embed=embed_ticket_closed("Closed by the ticket owner or support."),
            view=ClosedTicketView(self.cog, self.owner.id, self.order_id),
        )

        await _order_update(
            self.cog,
            self.order_id,
            payload_updates={"control_message_id": str(control_message.id)},
        )

    async def begin_checkout(self, interaction, product_id: int, variant_id: int):

        shop_id = int(self.selected_shop["id"])

        shop_name = self.selected_shop.get("name", "Unknown Shop")

        product_name = next(
            (
                name
                for name, product in self.products.items()
                if product.get("product_id") == product_id
            ),
            "Unknown Product",
        )

        
        client = CheckoutAutomation(self.cog._api)

        try:

            available = await asyncio.to_thread(
                self.cog._api.get_variant_stock,
                shop_id,
                product_id,
                variant_id,
            )

        except Exception as exc:

            available = None

            log.warn(
                "Live stock check unavailable",
                [
                    ("Product", str(product_id)),
                    ("Variant", str(variant_id)),
                    ("Error", str(exc)[:200]),
                ],
            )

        if available is not None and self.quantity > available:

            await _order_update(
                self.cog,
                self.order_id,
                status="ticket_open",
                payload_updates={
                    "stock_error": {
                        "product_id": product_id,
                        "variant_id": variant_id,
                        "requested": self.quantity,
                        "available": available,
                    }
                },
            )

            await _order_event(
                self.cog,
                self.order_id,
                "insufficient_stock",
                reason=f"Requested {self.quantity}; available {available}",
            )

            await interaction.followup.send(
                embed=embed_stock_unavailable(product_name, self.quantity, available),
                ephemeral=True,
            )
            return

        self.product_select.disabled = True

        self.shop_select.disabled = True

        self.quantity_select.disabled = True

        await self.refresh_panel(interaction)

        try:

            checkout_id = await asyncio.to_thread(
                client.create_checkout_with_retry,
                product_id,
                variant_id,
                shop_id,
                self.quantity,
            )

            await _order_update(
                self.cog,
                self.order_id,
                status="checkout_created",
                checkout_id=checkout_id,
                payload_updates={
                    "shop_id": shop_id,
                    "shop_name": shop_name,
                    "product_id": product_id,
                    "product_name": product_name,
                    "variant_id": variant_id,
                    "quantity": self.quantity,
                    "checkout_id": checkout_id,
                },
            )

            await _order_event(self.cog, self.order_id, "checkout_created", actor_id=self.owner.id)

            supported_currencies = await asyncio.to_thread(
                client.get_supported_currencies,
                checkout_id,
            )
            currencies = _selectable_currencies(supported_currencies)

            required_fields = await asyncio.to_thread(
                client.get_required_custom_fields,
                checkout_id,
            )

            if required_fields:

                await _order_update(
                    self.cog,
                    self.order_id,
                    status="details_required",
                    payload_updates={
                        "required_fields": [field["name"] for field in required_fields]
                    },
                )

                await _order_event(self.cog, self.order_id, "custom_fields_requested")

                if _CURRENCY_SELECTION_ENABLED and len(currencies) > 1:
                    await interaction.channel.send(
                        embed=embed_currency_selection(currencies),
                        view=CurrencySelectionView(
                            currencies,
                            client,
                            checkout_id,
                            self.owner,
                            product_name,
                            self.quantity,
                            shop_name,
                            shop_id,
                            interaction.channel,
                            self.cog,
                            self.order_id,
                            required_fields=required_fields,
                        ),
                    )
                    await interaction.followup.send(
                        "What payment method would you like to use? Choose one in this ticket.",
                        ephemeral=True,
                    )
                    return

                field_names = ", ".join(field["name"] for field in required_fields)

                await interaction.channel.send(
                    embed=embed_details_required(
                        product_name,
                        self.quantity,
                        field_names,
                        currency=currencies[0] if len(currencies) == 1 else None,
                    ),
                    view=FillDetailsView(
                        required_fields=required_fields,
                        client=client,
                        checkout_id=checkout_id,
                        user=self.owner,
                        product_name=product_name,
                        quantity=self.quantity,
                        shop_name=shop_name,
                        shop_id=shop_id,
                        channel=interaction.channel,
                        cog=self.cog,
                        order_id=self.order_id,
                        currency_options=currencies,
                    ),
                )
                return

            if _CURRENCY_SELECTION_ENABLED and len(currencies) > 1:
                await interaction.channel.send(
                    embed=embed_currency_selection(currencies),
                    view=CurrencySelectionView(
                        currencies,
                        client,
                        checkout_id,
                        self.owner,
                        product_name,
                        self.quantity,
                        shop_name,
                        shop_id,
                        interaction.channel,
                        self.cog,
                        self.order_id,
                    ),
                )
                await interaction.followup.send(
                    "What payment method would you like to use? Choose one in this ticket.",
                    ephemeral=True,
                )
                return

            await _offer_coupon(
                interaction,
                channel=interaction.channel,
                user=self.owner,
                client=client,
                checkout_id=checkout_id,
                currency=currencies[0],
                product_name=product_name,
                quantity=self.quantity,
                shop_name=shop_name,
                shop_id=shop_id,
                cog=self.cog,
                order_id=self.order_id,
            )

        except InsufficientStockError as exc:

            self.enable_selection_controls()

            await self.refresh_panel(interaction)

            await _order_update(
                self.cog,
                self.order_id,
                status="ticket_open",
                payload_updates={
                    "stock_error": {
                        "product_id": product_id,
                        "variant_id": variant_id,
                        "requested": exc.requested,
                        "available": exc.available,
                    }
                },
            )

            await _order_event(
                self.cog,
                self.order_id,
                "insufficient_stock",
                reason=str(exc),
            )

            await interaction.followup.send(
                embed=embed_stock_unavailable(product_name, exc.requested, exc.available),
                ephemeral=True,
            )

        except Exception as exc:

            await _order_update(
                self.cog,
                self.order_id,
                status="checkout_error",
                payload_updates={"error": str(exc)},
            )

            await _order_event(self.cog, self.order_id, "checkout_error", reason=str(exc)[:500])

            log.capchaerror(
                "Ticket checkout failed",
                [
                    ("User", str(self.owner)),
                    ("Error", str(exc)),
                ],
            )

            await interaction.followup.send(embed=embed_error(str(exc)), ephemeral=True)


class _TicketInteractionContext:
    

    def __init__(self, interaction: discord.Interaction):
        self._interaction = interaction

        self.author = interaction.user

        self.guild = interaction.guild

    async def defer(self, **kwargs):

        await self._interaction.response.defer(**kwargs)

    async def respond(self, *args, **kwargs):

        if not self._interaction.response.is_done():

            await self._interaction.response.send_message(*args, **kwargs)

        else:

            await self._interaction.followup.send(*args, **kwargs)


class AutoBuyPanelView(discord.ui.View):
    

    def __init__(self, cog):

        super().__init__(timeout=None)

        self.cog = cog

    @discord.ui.button(
        label="Open Purchase Ticket",
        emoji="🛒",
        style=discord.ButtonStyle.success,
        custom_id="autobuy_open_ticket",
    )
    async def open_ticket(self, button, interaction: discord.Interaction):

        await self.cog._open_ticket(_TicketInteractionContext(interaction))




class BuyCog(commands.Cog):

    def __init__(self, bot: discord.Bot):

        self.bot = bot

        self._api = getattr(bot, "sellauth_api", None) or SellAuthAPI(api_key=API_KEY)

        bot.sellauth_api = self._api

        self.orders = OrderStore(ORDER_DB_PATH, ORDER_ENCRYPTION_KEY)

        self._shop_cache: list = []

        self._product_cache: dict = {}

        self._tickets: dict[int, int] = {}

        self._ticket_locks: dict[tuple[int, int], asyncio.Lock] = {}

        self._delivery_tasks: dict[str, asyncio.Task] = {}

        self._payment_poll_semaphore: Optional[asyncio.Semaphore] = None

    async def _autocomplete_shops(self, ctx: discord.AutocompleteContext):

        try:

            if not self._shop_cache:

                self._shop_cache = _configured_shops(
                    await asyncio.to_thread(self._api.fetch_shops)
                )

            return [
                discord.OptionChoice(name=s.get("name", "Unnamed"), value=str(s.get("id")))
                for s in self._shop_cache
            ]

        except Exception:

            return []

    async def _autocomplete_products(self, ctx: discord.AutocompleteContext):

        shop_id = ctx.options.get("shop")

        if not shop_id:

            return []

        try:

            if shop_id not in self._product_cache:

                self._product_cache[shop_id] = await asyncio.to_thread(
                    self._api.fetch_products, int(shop_id)
                )

            products = self._product_cache[shop_id]

            return [
                discord.OptionChoice(
                    name=name, value=f"{info['product_id']}:{info['variant_ids'][0]}"
                )
                for name, info in products.items()
                if info["variant_ids"]
            ]

        except Exception:

            return []

    def get_payment_poll_semaphore(self) -> asyncio.Semaphore:

        if self._payment_poll_semaphore is None:

            self._payment_poll_semaphore = asyncio.Semaphore(_PAYMENT_POLL_CONCURRENCY)

        return self._payment_poll_semaphore

    def start_delivery(self, order_id, delivery_coroutine):
        

        if order_id:

            existing = self._delivery_tasks.get(order_id)

            if existing and not existing.done():

                delivery_coroutine.close()

                return existing

        task = asyncio.create_task(delivery_coroutine)

        if order_id:

            self._delivery_tasks[order_id] = task

            task.add_done_callback(
                lambda completed, key=order_id: self._delivery_tasks.pop(key, None)
            )

        return task

    async def _open_ticket(self, ctx: discord.ApplicationContext):

        guild_id = getattr(ctx.guild, "id", 0)

        lock_key = (guild_id, ctx.author.id)

        lock = self._ticket_locks.setdefault(lock_key, asyncio.Lock())

        async with lock:

            await self._open_ticket_unlocked(ctx)

    async def _open_ticket_unlocked(self, ctx: discord.ApplicationContext):

        if ctx.guild is None:

            await ctx.respond("Tickets can only be opened inside a server.", ephemeral=True)
            return

        existing_id = self._tickets.get(ctx.author.id)

        if not existing_id:

            records = await asyncio.to_thread(
                self.orders.find_orders_by_user,
                ctx.author.id,
                include_deleted=False,
            )

            active = next(
                (
                    record
                    for record in records
                    if record["status"]
                    not in {"delivered", "expired", "ticket_deleted", "checkout_error"}
                    and record.get("channel_id")
                ),
                None,
            )

            if active:

                existing_id = int(active["channel_id"])

                self._tickets[ctx.author.id] = existing_id

        if existing_id:

            existing = self.bot.get_channel(existing_id)

            if existing:

                await ctx.respond(
                    f"You already have an open ticket: {existing.mention}", ephemeral=True
                )
                return

            self._tickets.pop(ctx.author.id, None)

        await ctx.defer(ephemeral=True)

        try:

            if not self._shop_cache:

                self._shop_cache = _configured_shops(
                    await asyncio.to_thread(self._api.fetch_shops)
                )

            if not self._shop_cache:

                raise RuntimeError("No SellAuth shops are available.")

            guild = ctx.guild

            safe_name = (
                "".join(
                    character.lower() if character.isalnum() else "-"
                    for character in str(ctx.author.name)
                ).strip("-")[:24]
                or "user"
            )

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                ctx.author: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True,
                ),
            }

            bot_member = guild.me or guild.get_member(self.bot.user.id)

            if bot_member:

                overwrites[bot_member] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_channels=True,
                    manage_messages=True,
                )

            for role_id in (SUPPORT_ROLE_ID, STAFF_ROLE_ID):

                if not role_id:
                    continue

                staff_role = guild.get_role(role_id)

                if staff_role:

                    overwrites[staff_role] = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                    )

            category = guild.get_channel(TICKET_CATEGORY_ID) if TICKET_CATEGORY_ID else None

            channel = await guild.create_text_channel(
                name=f"ticket-{safe_name}",
                category=category if isinstance(category, discord.CategoryChannel) else None,
                overwrites=overwrites,
                topic=f"ticket_owner:{ctx.author.id}",
                reason=f"Purchase ticket opened by {ctx.author}",
            )

            order_id = await asyncio.to_thread(
                self.orders.create_order,
                ctx.author.id,
                guild_id=guild.id,
                channel_id=channel.id,
                status="ticket_open",
                payload={
                    "guild_id": str(guild.id),
                    "channel_id": str(channel.id),
                    "ticket_owner": str(ctx.author.id),
                },
            )

            await asyncio.to_thread(
                self.orders.append_event,
                order_id,
                "ticket_opened",
                actor_id=ctx.author.id,
            )

            self._tickets[ctx.author.id] = channel.id

            embed = _styled_embed(
                "🎫  PRIVATE PURCHASE TICKET",
                (
                    f"Welcome, {ctx.author.mention}. Your private order session is ready.\n\n"
                    "Use the menus below to select your shop, product, and quantity."
                ),
                C_PURPLE,
            )

            embed.add_field(
                name="🔒 Private checkout",
                value="Only you and the configured support team can see this channel.",
                inline=False,
            )

            embed.add_field(
                name="📬 Delivery",
                value="Payment instructions stay here. Confirmed deliverables are sent by DM.",
                inline=False,
            )

            support_role = guild.get_role(SUPPORT_ROLE_ID) if SUPPORT_ROLE_ID else None

            ticket_ping = f"{support_role.mention} " if support_role else ""

            ticket_view = TicketView(self, ctx.author, self._shop_cache, order_id)

            ticket_message = await channel.send(
                content=f"{ticket_ping}{ctx.author.mention}",
                embed=embed,
                view=ticket_view,
            )

            ticket_view.panel_message_id = ticket_message.id

            await asyncio.to_thread(
                self.orders.update_order,
                order_id,
                payload_updates={"ticket_message_id": str(ticket_message.id)},
            )

            await ctx.respond(f"Your private ticket is ready: {channel.mention}", ephemeral=True)

        except Exception as exc:

            log.capchaerror(
                "Ticket creation failed",
                [
                    ("User", str(ctx.author)),
                    ("Error", str(exc)),
                ],
            )

            await ctx.respond(embed=embed_error(str(exc)), ephemeral=True)

    async def ticket(self, ctx: discord.ApplicationContext):

        await self._open_ticket(ctx)

    async def restore_order_state(self):
        

        records = await asyncio.to_thread(self.orders.find_active_orders)

        for record in records:

            payload = record["payload"]

            owner_id = payload.get("ticket_owner") or payload.get("owner_id")

            channel_id = record.get("channel_id")

            if not owner_id or not channel_id:
                continue

            channel = self.bot.get_channel(int(channel_id))

            if channel is None:
                continue

            status = record["status"]

            if status not in {"delivered", "expired", "checkout_error", "ticket_deleted"}:

                self._tickets[int(owner_id)] = int(channel_id)

            message_id = payload.get(
                "ticket_message_id" if status == "ticket_open" else "control_message_id"
            )

            try:

                if status == "ticket_open":

                    member = channel.guild.get_member(int(owner_id))

                    if member is None:
                        continue

                    if not self._shop_cache:

                        self._shop_cache = _configured_shops(
                            await asyncio.to_thread(self._api.fetch_shops)
                        )

                    view = TicketView(
                        self,
                        member,
                        self._shop_cache,
                        record["order_id"],
                        panel_message_id=message_id,
                    )

                elif status == "support_reopened":

                    view = ReopenedTicketView(self, int(owner_id), record["order_id"])

                elif status not in {"ticket_deleted"} and message_id:

                    view = ClosedTicketView(self, int(owner_id), record["order_id"])

                else:
                    continue

                if message_id:

                    self.bot.add_view(view, message_id=int(message_id))

            except Exception as exc:

                log.warn(
                    "Could not restore ticket view",
                    [("Order", record["order_id"]), ("Error", str(exc))],
                )

    @staticmethod
    def _is_support_member(member) -> bool:

        if getattr(member.guild_permissions, "manage_channels", False):

            return True

        return bool(
            SUPPORT_ROLE_ID
            and any(role.id == SUPPORT_ROLE_ID for role in getattr(member, "roles", []))
        )


def setup(bot: discord.Bot):

    bot.add_cog(BuyCog(bot))


async def _order_update(
    cog, order_id, *, status=None, payload_updates=None, channel_id=None, checkout_id=None
):

    if not cog or not order_id:
        return

    try:

        await asyncio.to_thread(
            cog.orders.update_order,
            order_id,
            status=status,
            payload_updates=payload_updates,
            channel_id=channel_id,
            checkout_id=checkout_id,
        )

    except Exception as exc:

        log.capchaerror(
            "Order database update failed", [("Order", str(order_id)), ("Error", str(exc))]
        )


async def _order_event(cog, order_id, event_type, *, actor_id=None, reason=None, details=None):

    if not cog or not order_id:
        return

    try:

        await asyncio.to_thread(
            cog.orders.append_event,
            order_id,
            event_type,
            actor_id=actor_id,
            reason=reason,
            details=details,
        )

    except Exception as exc:

        log.capchaerror("Order event write failed", [("Order", str(order_id)), ("Error", str(exc))])
