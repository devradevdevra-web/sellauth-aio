
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

import discord

from src.config import API_KEY, SHOP_ID, SUPPORT_ROLE_ID, STAFF_ROLE_ID, OWNER_IDS
from src.sellauth.sellauth import SellAuthAPI, SellAuthError
from src.sellauth.commands.ticket.service import C_BLUE, C_GREEN, C_ORANGE, C_RED, C_PURPLE
from src.sellauth.commands.embed_style import styled_embed
MAX_TEXT = 2000
MAX_DELIVERABLES = 10000


def get_api(bot: discord.Bot) -> SellAuthAPI:
    api = getattr(bot, "sellauth_api", None)
    if api is None:
        api = SellAuthAPI(api_key=API_KEY)
        bot.sellauth_api = api
    return api


def _admin_autocomplete_allowed(ctx: discord.AutocompleteContext) -> bool:
    return owner_or_admin(getattr(ctx.interaction, "user", None))


async def shop_autocomplete(ctx: discord.AutocompleteContext) -> list[discord.OptionChoice]:
    
    if not _admin_autocomplete_allowed(ctx):
        return []

    api = getattr(ctx.bot, "sellauth_api", None)
    if api is None:
        return []

    shops = getattr(ctx.bot, "_shop_autocomplete_cache", None)
    if shops is None:
        try:
            shops = await asyncio.to_thread(api.fetch_shops)
        except Exception:
            return []
        ctx.bot._shop_autocomplete_cache = shops

    if SHOP_ID is not None:
        shops = [
            shop
            for shop in shops or []
            if isinstance(shop, dict) and str(shop.get("id")) == str(SHOP_ID)
        ]

    query = str(ctx.value or "").strip().casefold()
    choices = []
    for shop in shops or []:
        shop_id = shop.get("id") if isinstance(shop, dict) else None
        if shop_id is None:
            continue
        try:
            shop_id = int(shop_id)
        except (TypeError, ValueError):
            continue
        name = str(shop.get("name") or f"Shop {shop_id}")
        if query and query not in name.casefold() and query not in str(shop_id):
            continue
        choices.append(discord.OptionChoice(name[:100], shop_id))
    return choices[:25]


async def _load_products_for_autocomplete(ctx: discord.AutocompleteContext, shop_id) -> dict:
    api = getattr(ctx.bot, "sellauth_api", None)
    if api is None:
        return {}
    cache = getattr(ctx.bot, "_product_autocomplete_cache", None)
    if cache is None:
        cache = {}
        ctx.bot._product_autocomplete_cache = cache
    try:
        shop_id = int(shop_id)
    except (TypeError, ValueError):
        return {}
    if shop_id not in cache:
        try:
            cache[shop_id] = await asyncio.to_thread(api.fetch_products, shop_id)
        except Exception:
            return {}
    return cache.get(shop_id, {})


async def product_autocomplete(ctx: discord.AutocompleteContext) -> list[discord.OptionChoice]:
    if not _admin_autocomplete_allowed(ctx):
        return []
    products = await _load_products_for_autocomplete(ctx, (ctx.options or {}).get("shop_id"))
    query = str(ctx.value or "").strip().casefold()
    choices = []
    for name, product in products.items():
        product_id = product.get("product_id") if isinstance(product, dict) else None
        if product_id is None:
            continue
        label = str(name)
        if query and query not in label.casefold():
            continue
        choices.append(discord.OptionChoice(label[:100], int(product_id)))
    return choices[:25]


async def variant_autocomplete(ctx: discord.AutocompleteContext) -> list[discord.OptionChoice]:
    if not _admin_autocomplete_allowed(ctx):
        return []
    products = await _load_products_for_autocomplete(ctx, (ctx.options or {}).get("shop_id"))
    try:
        product_id = int((ctx.options or {}).get("product_id"))
    except (TypeError, ValueError):
        return []
    query = str(ctx.value or "").strip().casefold()
    choices = []
    for name, product in products.items():
        if not isinstance(product, dict) or int(product.get("product_id", 0)) != product_id:
            continue
        for index, variant_id in enumerate(product.get("variant_ids", []), start=1):
            label = f"{name} • Variant {index}"
            if not query or query in label.casefold():
                choices.append(discord.OptionChoice(label[:100], int(variant_id)))
    return choices[:25]


async def coupon_autocomplete(ctx: discord.AutocompleteContext) -> list[discord.OptionChoice]:
    
    if not _admin_autocomplete_allowed(ctx):
        return []

    api = getattr(ctx.bot, "sellauth_api", None)
    if api is None:
        return []
    try:
        shop_id = int((ctx.options or {}).get("shop_id"))
    except (TypeError, ValueError):
        return []

    cache = getattr(ctx.bot, "_coupon_autocomplete_cache", None)
    if cache is None:
        cache = {}
        ctx.bot._coupon_autocomplete_cache = cache
    if shop_id not in cache:
        try:
            data = await asyncio.to_thread(
                api.list_coupons,
                shop_id,
                page=1,
                perPage=100,
            )
        except Exception:
            return []
        cache[shop_id] = extract_rows(data)

    query = str(ctx.value or "").strip().casefold()
    choices = []
    for coupon in cache.get(shop_id, []):
        if not isinstance(coupon, dict):
            continue
        coupon_id = coupon.get("id")
        code = coupon.get("code") or coupon.get("name")
        if coupon_id is None or not code:
            continue
        label = str(code).strip()
        if query and query not in label.casefold():
            continue
        try:
            value = int(coupon_id)
        except (TypeError, ValueError):
            continue
        choices.append(discord.OptionChoice(label[:100], value))
    return choices[:25]


def embed(title: str, description: str = "", color: int = C_PURPLE) -> discord.Embed:
    return styled_embed(title, description, color)


def bounded_text(value: str, name: str, limit: int = MAX_TEXT) -> str:
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{name} cannot be empty.")
    if len(value) > limit:
        raise ValueError(f"{name} must be {limit} characters or fewer.")
    return value


def bounded_positive(value: int | str, name: str, maximum: int = 2_147_483_647) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a whole number.") from exc
    if result < 1 or result > maximum:
        raise ValueError(f"{name} must be between 1 and {maximum:,}.")
    return result


def extract_rows(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        rows = data.get("data", data.get("items", data.get("results", [])))
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def owner_or_admin(member: discord.abc.User, *, guild_required: bool = True) -> bool:
    if getattr(member, "id", None) in OWNER_IDS:
        return True
    if guild_required and getattr(member, "guild", None) is None:
        return False
    if STAFF_ROLE_ID and any(
        role.id == STAFF_ROLE_ID for role in getattr(member, "roles", [])
    ):
        return True
    permissions = getattr(member, "guild_permissions", None)
    return bool(permissions and (permissions.administrator or permissions.manage_guild))


def support_or_admin(member: discord.abc.User) -> bool:
    if owner_or_admin(member, guild_required=False):
        return True
    permissions = getattr(member, "guild_permissions", None)
    if permissions and (permissions.administrator or permissions.manage_channels):
        return True
    return bool(SUPPORT_ROLE_ID and any(role.id == SUPPORT_ROLE_ID for role in getattr(member, "roles", [])))


async def require(ctx: discord.ApplicationContext, predicate: Callable[[Any], bool], message: str) -> bool:
    if predicate(ctx.author):
        return True
    await ctx.respond(embed=embed("❌ Permission denied", message, C_RED), ephemeral=True)
    return False


async def resolve_shop_id(api: SellAuthAPI, requested: Optional[int] = None) -> int:
    if SHOP_ID is not None:
        if requested is not None and int(requested) != SHOP_ID:
            raise ValueError(f"This bot is configured for SellAuth shop {SHOP_ID} only.")
        return SHOP_ID
    if requested is not None:
        return bounded_positive(requested, "shop_id")
    if SHOP_ID:
        return bounded_positive(SHOP_ID, "shop_id")
    shops = await asyncio.to_thread(api.fetch_shops)
    if not shops or shops[0].get("id") is None:
        raise ValueError("No SellAuth shop is configured. Pass shop_id or set SHOP_ID.")
    return bounded_positive(shops[0]["id"], "shop_id")


class ConfirmationView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.confirmed: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("Only the command author can confirm this action.", ephemeral=True)
        return False

    @discord.ui.button(label="Confirm", emoji="✅", style=discord.ButtonStyle.danger)
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction):
        self.confirmed = True
        self.stop()
        await interaction.response.edit_message(content="Confirmed.", embed=None, view=None)

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction):
        self.confirmed = False
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", embed=None, view=None)


async def confirm(ctx: discord.ApplicationContext, message: str) -> bool:
    view = ConfirmationView(ctx.author.id)
    await ctx.respond(embed=embed("⚠️ Confirmation required", message, C_ORANGE), view=view, ephemeral=True)
    await view.wait()
    if view.confirmed is None:
        try:
            await ctx.edit_original_response(content="Timed out; no changes were made.", embed=None, view=None)
        except discord.HTTPException:
            pass
    return view.confirmed is True


async def run_api(ctx: discord.ApplicationContext, operation: Callable[[], Any], *, success: discord.Embed | None = None) -> Any:
    try:
        result = await asyncio.to_thread(operation)
    except (SellAuthError, ValueError) as exc:
        await ctx.respond(embed=embed("❌ SellAuth request failed", str(exc), C_RED), ephemeral=True)
        return None
    except Exception as exc:
        await ctx.respond(embed=embed("❌ Unexpected error", str(exc)[:1000], C_RED), ephemeral=True)
        return None
    if success is not None:
        await ctx.respond(embed=success)
    return result
