from __future__ import annotations
import asyncio
import discord
import src.logger.logger as log
from src.config import BOT_TOKEN, LOGGING_CONFIG, SHOP_ID

log.configure(LOGGING_CONFIG)
log.banner("SELLAUTH AIO", "All-in-one automated checkout console")

EXTENSIONS = (
    "src.sellauth.commands.ticket.service",
    "src.sellauth.commands.send_panel",
    "src.sellauth.commands.order_info",
    "src.sellauth.commands.order_search_user",
    "src.sellauth.commands.admin_shop",
    "src.sellauth.commands.admin_products",
    "src.sellauth.commands.admin_coupons",
    "src.sellauth.commands.admin_blacklist",
    "src.sellauth.commands.admin_orders",
    "src.sellauth.commands.admin_feedback",
    "src.sellauth.commands.customer",
    "src.sellauth.commands.blockchain",
    "src.sellauth.monitoring.orders",
)


intents = discord.Intents.default()
bot = discord.Bot(intents=intents, auto_sync_commands=False)
for extension in EXTENSIONS:
    try:
        bot.load_extension(extension)
        log.debug("Extension loaded", [("Extension", extension)])
    except Exception as exc:
        log.exception("Extension failed to load", exc, [("Extension", extension)])
        raise


def _selected_shops(shops) -> list[dict]:
    available = [shop for shop in shops or [] if isinstance(shop, dict)]
    if SHOP_ID is None:
        return available
    return [shop for shop in available if str(shop.get("id")) == str(SHOP_ID)]


def _apply_shop_choices(shops: list[dict]) -> None:
    
    choices = []
    for shop in shops:
        if not isinstance(shop, dict) or shop.get("id") is None:
            continue
        try:
            shop_id = int(shop["id"])
        except (TypeError, ValueError):
            continue
        name = str(shop.get("name") or f"Shop {shop_id}")
        choices.append(discord.OptionChoice(name[:100], shop_id))

    for command in bot.pending_application_commands:
        for option in getattr(command, "options", []):
            if option.name != "shop_id":
                continue
            option.description = "Choose a SellAuth shop"
            if len(choices) <= 25:
                option.choices = choices
                option._autocomplete = None
                option._autocomplete_is_instance_method = False
            else:

                option.choices = []


def _apply_catalog_choices(catalog: dict[int, dict]) -> None:
    
    product_choices = [
        discord.OptionChoice(item["name"][:100], product_id) for product_id, item in catalog.items()
    ]
    variant_choices = []
    seen_variants = set()
    for item in catalog.values():
        for index, variant_id in enumerate(item.get("variant_ids", []), start=1):
            if variant_id in seen_variants:
                continue
            seen_variants.add(variant_id)
            variant_choices.append(
                discord.OptionChoice(f"{item['name']} • Variant {index}"[:100], variant_id)
            )

    for command in bot.pending_application_commands:
        for option in getattr(command, "options", []):
            if option.name == "product_id":
                option.description = "Choose a product"
                if len(product_choices) <= 25:
                    option.choices = product_choices
                    option._autocomplete = None
                    option._autocomplete_is_instance_method = False
            elif option.name == "variant_id":
                option.description = "Choose a product variant"
                if len(variant_choices) <= 25:
                    option.choices = variant_choices
                    option._autocomplete = None
                    option._autocomplete_is_instance_method = False


async def _prepare_shop_options() -> None:
    api = getattr(bot, "sellauth_api", None)
    if api is None:
        return
    try:
        shops = await asyncio.to_thread(api.fetch_shops)
    except Exception as exc:
        log.exception("Could not load shop choices", exc)
        return

    shops = _selected_shops(shops)

    bot._shop_autocomplete_cache = shops
    _apply_shop_choices(shops)

    catalog = {}
    for shop in shops or []:
        try:
            shop_id = int(shop["id"])
            products = await asyncio.to_thread(api.fetch_products, shop_id)
        except Exception as exc:
            log.warn(
                "Catalog shop skipped",
                [("Shop", shop.get("id", "unknown")), ("Error", type(exc).__name__)],
            )
            continue
        for name, product in (products or {}).items():
            if not isinstance(product, dict) or product.get("product_id") is None:
                continue
            try:
                product_id = int(product["product_id"])
            except (TypeError, ValueError):
                continue
            item = catalog.setdefault(
                product_id,
                {"name": str(name), "variant_ids": []},
            )
            for variant_id in product.get("variant_ids", []):
                try:
                    variant_id = int(variant_id)
                except (TypeError, ValueError):
                    continue
                if variant_id not in item["variant_ids"]:
                    item["variant_ids"].append(variant_id)

    _apply_catalog_choices(catalog)


@bot.event
async def on_ready():
    if not getattr(bot, "_commands_synced", False):
        try:
            await _prepare_shop_options()
            await bot.sync_commands(method="bulk", force=True)
            bot._commands_synced = True
        except Exception as exc:
            log.exception("Command synchronization failed", exc)
            return

    buy_cog = bot.get_cog("BuyCog")
    if buy_cog and not getattr(bot, "_orders_restored", False):
        try:
            bot._orders_restored = True
            await buy_cog.restore_order_state()
        except Exception as exc:
            bot._orders_restored = False
            log.exception("Order state restoration failed", exc)
            return

    if getattr(bot, "_ready_logged", False):
        return

    shops = getattr(bot, "_shop_autocomplete_cache", [])
    shop_names = []
    for shop in shops:
        if not isinstance(shop, dict):
            continue
        shop_id = shop.get("id", "unknown")
        shop_name = shop.get("name") or f"Shop {shop_id}"
        shop_names.append(str(shop_name))
    shop_summary = ", ".join(shop_names) or "None"
    bot._ready_logged = True
    log.success(
        "Discord bot ready",
        [
            ("Extensions", len(EXTENSIONS)),
            ("Commands", len(bot.pending_application_commands)),
            ("Shops", len(shops)),
            ("Names", shop_summary),
        ],
    )


if __name__ == "__main__":
    try:
        bot.run(BOT_TOKEN)
    except KeyboardInterrupt:
        log.info("Shutdown requested")
    except Exception as exc:
        log.exception("Bot stopped unexpectedly", exc)
        raise
