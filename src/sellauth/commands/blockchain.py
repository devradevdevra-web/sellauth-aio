
from __future__ import annotations
import discord
from discord.ext import commands
from .common import C_GREEN, C_ORANGE, embed, get_api, bounded_text, run_api

class BlockchainCog(commands.Cog):
    def __init__(self, bot: discord.Bot): self.api = get_api(bot)

    @discord.slash_command(name="check-blockchain-id", description="Check Litecoin transaction confirmations")
    async def check_blockchain_id(self, ctx: discord.ApplicationContext, transaction_id: str):
        try: txid = bounded_text(transaction_id, "transaction_id", 128)
        except ValueError as exc: await ctx.respond(embed=embed("⚠️ Invalid transaction ID", str(exc), C_ORANGE), ephemeral=True); return
        data = await run_api(ctx, lambda: self.api.get_blockchain_transaction(txid))
        if data is None: return
        confirmations = int(data.get("confirmations") or 0)
        outputs = data.get("outputs") or []
        inputs = data.get("inputs") or []
        sender = ((inputs[0].get("addresses") or ["N/A"])[0] if inputs else "N/A")
        recipient = ((outputs[0].get("addresses") or ["N/A"])[0] if outputs else "N/A")
        value = (sum(int(item.get("value") or 0) for item in outputs) / 100000000) if outputs else 0
        e = embed("✅ Litecoin transaction" if confirmations else "⏳ Transaction pending", color=C_GREEN if confirmations else C_ORANGE)
        e.add_field(name="Sender", value=f"`{sender}`", inline=False); e.add_field(name="Recipient", value=f"`{recipient}`", inline=False)
        e.add_field(name="Value", value=f"`{value:.8f} LTC`", inline=True); e.add_field(name="Confirmations", value=f"`{confirmations}`", inline=True)
        await ctx.respond(embed=e)

def setup(bot: discord.Bot): bot.add_cog(BlockchainCog(bot))
