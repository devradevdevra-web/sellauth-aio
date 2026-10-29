

import io
import asyncio
import discord
from discord.ext import commands
from .common import (
    C_GREEN,
    C_RED,
    embed,
    get_api,
    require,
    owner_or_admin,
    run_api,
    bounded_positive,
    bounded_text,
    confirm,
    shop_autocomplete,
    product_autocomplete,
)


_PRODUCT_UPDATE_LIST_FIELDS = (
    "product_addons",
    "product_upsells",
    "image_ids",
    "custom_fields",
    "product_tabs",
    "product_badges",
)


def _product_update_payload(product: dict, price: float) -> dict:
    
    product_type = str(product.get("type") or "variant").strip().lower()
    if product_type not in {"variant", "addon"}:
        product_type = "variant"

    visibility = str(product.get("visibility") or "public").strip().lower()
    if visibility not in {"public", "unlisted", "private", "on_hold"}:
        visibility = "public"

    currency = str(product.get("currency") or "USD").strip().upper()[:3]
    if not currency:
        currency = "USD"

    deliverables_type = str(product.get("deliverables_type") or "serials").strip().lower()
    if deliverables_type not in {"serials", "service", "files", "dynamic", "physical"}:
        deliverables_type = "serials"

    meta_twitter_card = product.get("meta_twitter_card")
    if meta_twitter_card not in {"summary", "summary_large_image"}:
        meta_twitter_card = "summary"

    payload = {
        "type": product_type,
        "name": str(product.get("name") or "Unnamed Product")[:255],
        "currency": currency,
        "visibility": visibility,
        "product_addons": [],
        "product_upsells": [],
        "path": product.get("path"),
        "category_id": product.get("category_id"),
        "description": str(product.get("description") or ""),
        "meta_title": str(product.get("meta_title") or "")[:255],
        "meta_description": str(product.get("meta_description") or ""),
        "meta_image_id": product.get("meta_image_id"),
        "meta_twitter_card": meta_twitter_card,
        "instructions": str(product.get("instructions") or ""),
        "out_of_stock_message": str(product.get("out_of_stock_message") or ""),
        "thank_you_message": str(product.get("thank_you_message") or ""),
        "tax_inclusive": bool(product.get("tax_inclusive", False)),
        "deliverables_type": deliverables_type,
        "requires_fulfillment": bool(product.get("requires_fulfillment", False)),
        "group_id": product.get("group_id"),
        "image_ids": [],
        "discord_required": bool(product.get("discord_required", False)),
        "hide_stock_count": bool(product.get("hide_stock_count", False)),
        "cashback_percentage": float(product.get("cashback_percentage") or 0),
        "reseller_enabled": bool(product.get("reseller_enabled", False)),
        "reseller_disable_volume_discount": bool(
            product.get("reseller_disable_volume_discount", False)
        ),
        "status_color": str(product.get("status_color") or "")[:24],
        "status_text": str(product.get("status_text") or "")[:255],
        "show_views_count": bool(product.get("show_views_count", False)),
        "show_sales_count": bool(product.get("show_sales_count", True)),
        "show_sales_notifications": bool(
            product.get("show_sales_notifications", True)
        ),
        "sales_count_hours": product.get("sales_count_hours", 24),
        "feedback_coupon_config": product.get("feedback_coupon_config"),
        "deliverables_label": str(product.get("deliverables_label") or "")[:255],
        "metadata": str(product.get("metadata", product.get("metadatas", "")) or ""),
        "is_mandatory": bool(product.get("is_mandatory", False)),
    }

    for field in _PRODUCT_UPDATE_LIST_FIELDS:
        value = product.get(field)
        if isinstance(value, list):
            payload[field] = list(value)
        elif value in (None, ""):
            payload[field] = []
        else:
            payload[field] = [value]

    raw_variants = product.get("variants") or []
    variants = [dict(variant) for variant in raw_variants if isinstance(variant, dict)]
    if variants:
        variants[0]["price"] = price
    elif product_type == "variant":
        variants = [{"id": None, "type": "single", "price": price}]
    payload["variants"] = variants
    return payload


class AdminProductsCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.api = get_api(bot)

    @discord.slash_command(
        name="delete-product", description="Delete a product after confirmation"
    )
    async def delete_product(
        self,
        ctx: discord.ApplicationContext,
        shop_id: discord.Option(
            int, "Shop", min_value=1, autocomplete=shop_autocomplete
        ),
        product_id: discord.Option(
            int, "Product", min_value=1, autocomplete=product_autocomplete
        ),
    ):
        if not await require(
            ctx, owner_or_admin, "Only shop administrators can delete products."
        ):
            return
        if not await confirm(
            ctx,
            f"Delete product `{product_id}` from shop `{shop_id}`? This cannot be undone.",
        ):
            return
        result = await run_api(
            ctx,
            lambda: self.api.delete_product(
                shop_id, bounded_positive(product_id, "product_id")
            ),
        )
        if result is not None:
            await ctx.respond(
                embed=embed(
                    "`✅` Product deleted", f"Product ID: `{product_id}`", C_GREEN
                ),
                ephemeral=True,
            )

    @discord.slash_command(
        name="update-product-price", description="Update a product price"
    )
    async def update_product_price(
        self,
        ctx: discord.ApplicationContext,
        shop_id: discord.Option(
            int, "Shop", min_value=1, autocomplete=shop_autocomplete
        ),
        product_id: discord.Option(
            int, "Product", min_value=1, autocomplete=product_autocomplete
        ),
        new_price: str,
    ):
        if not await require(
            ctx, owner_or_admin, "Only shop administrators can update product prices."
        ):
            return
        try:
            price = float(new_price)
            if price < 0 or price > 1_000_000:
                raise ValueError
        except ValueError:
            await ctx.respond(
                embed=embed("❌ Invalid price", "Price must be from 0 to 1,000,000."),
                ephemeral=True,
            )
            return
        product = await run_api(ctx, lambda: self.api.get_product(shop_id, product_id))
        if product is None:
            return

        payload = _product_update_payload(product, price)
        if not payload["variants"]:
            payload["price"] = price

        result = await run_api(
            ctx,
            lambda: self.api.update_product(shop_id, product_id, **payload),
        )
        if result is not None:
            await ctx.respond(
                embed=embed(
                    "✅ Product price updated",
                    f"Product: `{str(product.get('name', product_id))[:100]}`\nNew price: `{price:g}`",
                    C_GREEN,
                ),
                ephemeral=True,
            )

    @discord.slash_command(
        name="restock-product", description="Append deliverables from a text file"
    )
    async def restock_product(
        self,
        ctx: discord.ApplicationContext,
        shop_id: discord.Option(
            int, "Shop", min_value=1, autocomplete=shop_autocomplete
        ),
        product_id: discord.Option(
            int, "Product", min_value=1, autocomplete=product_autocomplete
        ),
        restock_file: discord.Attachment,
    ):
        if not await require(
            ctx, owner_or_admin, "Only shop administrators can restock products."
        ):
            return
        if restock_file.size and restock_file.size > 5_000_000:
            await ctx.respond(
                embed=embed("❌ File too large", "Restock files are limited to 5 MB."),
                ephemeral=True,
            )
            return
        raw = await restock_file.read()
        if len(raw) > 5_000_000:
            await ctx.respond(
                embed=embed("❌ File too large", "Restock files are limited to 5 MB."),
                ephemeral=True,
            )
            return
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            await ctx.respond(
                embed=embed(
                    "❌ Invalid restock file",
                    "The file must be valid UTF-8 text.",
                ),
                ephemeral=True,
            )
            return
        lines = [line.strip() for line in decoded.splitlines() if line.strip()]
        if not lines or len(lines) > 10000:
            await ctx.respond(
                embed=embed(
                    "❌ Invalid restock file", "Provide 1-10,000 non-empty lines."
                ),
                ephemeral=True,
            )
            return
        variant_id = await run_api(
            ctx, lambda: self.api.get_default_variant_id(shop_id, product_id)
        )
        if variant_id is None:
            return
        if not await confirm(
            ctx,
            f"Append `{len(lines):,}` deliverables to product `{product_id}` using its default variant?",
        ):
            return
        result = await run_api(
            ctx,
            lambda: self.api.append_deliverables(
                shop_id, product_id, variant_id, lines
            ),
        )
        if result is not None:
            await ctx.respond(
                embed=embed(
                    "✅ Product restocked",
                    f"Added `{len(lines):,}` deliverables.",
                    C_GREEN,
                ),
                ephemeral=True,
            )

    @discord.slash_command(
        name="send-varient-serials", description="Send and remove product deliverables"
    )
    async def send_variant_serials(
        self,
        ctx: discord.ApplicationContext,
        shop_id: discord.Option(
            int, "Shop", min_value=1, autocomplete=shop_autocomplete
        ),
        product_id: discord.Option(
            int, "Product", min_value=1, autocomplete=product_autocomplete
        ),
        amount: discord.Option(int, "Number to send", min_value=1, max_value=1000),
        format_choice: discord.Option(
            str, "Delivery format", choices=["txt", "message"]
        ),
        recipient: discord.Member,
    ):
        if not await require(
            ctx,
            owner_or_admin,
            "Only shop administrators can distribute product deliverables.",
        ):
            return
        if not await confirm(
            ctx,
            f"Send and remove `{amount}` deliverables from product `{product_id}`? The default variant will be selected automatically.",
        ):
            return
        variant_id = await run_api(
            ctx, lambda: self.api.get_default_variant_id(shop_id, product_id)
        )
        if variant_id is None:
            return
        deliverables = await run_api(
            ctx, lambda: self.api.get_deliverables(shop_id, product_id, variant_id)
        )
        if deliverables is None:
            return
        amount = min(amount, len(deliverables))
        if amount < 1:
            await ctx.respond(
                embed=embed("❌ No stock", "No deliverables are available."),
                ephemeral=True,
            )
            return
        selected = [str(item) for item in deliverables[:amount]]
        remaining = [str(item) for item in deliverables[amount:]]
        result = await run_api(
            ctx,
            lambda: self.api.overwrite_deliverables(
                shop_id, product_id, variant_id, remaining
            ),
        )
        if result is None:
            return
        try:
            if format_choice == "txt":
                await recipient.send(
                    "Requested deliverables:",
                    file=discord.File(
                        io.BytesIO("\n".join(selected).encode()),
                        filename="deliverables.txt",
                    ),
                )
            else:
                message_content = "\n".join(selected)[:1900]
                await recipient.send(
                    f"Requested deliverables:\n```\n{message_content}\n```"
                )
        except discord.HTTPException:
            await run_api(
                ctx,
                lambda: self.api.append_deliverables(
                    shop_id, product_id, variant_id, selected
                ),
            )
            await ctx.respond(
                embed=embed(
                    "❌ Delivery failed",
                    "Inventory was restored because the recipient could not receive a message.",
                ),
                ephemeral=True,
            )
            return
        await ctx.respond(
            embed=embed(
                "✅ Serials sent",
                f"Sent `{amount}` serial(s) to {recipient.mention}.",
                C_GREEN,
            ),
            ephemeral=True,
        )


def setup(bot: discord.Bot):
    bot.add_cog(AdminProductsCog(bot))
