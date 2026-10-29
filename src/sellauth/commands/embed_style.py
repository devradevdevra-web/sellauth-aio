
from __future__ import annotations

import discord


BRAND_FOOTER = "SellAuth AIO  •  Secure • Automatic Delivery"


class CompactEmbed(discord.Embed):
    

    @staticmethod
    def _label(name: object) -> str:
        text = str(name)
        if text.startswith("`"):
            end = text.find("`", 1)
            if end > 0:
                icon = text[: end + 1]
                label = text[end + 1 :].strip()
                return f"{icon} **{label}:**"
        return f"**{text}:**"

    def add_field(self, *, name: object, value: object, inline: bool = False):
        del inline
        line = f"{self._label(name)} {value}"
        if not self.description:
            self.description = f">>> {line}"
        elif ">>>" not in self.description:
            self.description += f"\n\n>>> {line}"
        else:
            self.description += f"\n{line}"
        return self


def styled_embed(
    title: str, description: str = "", color: int = 0x8B5CF6
) -> CompactEmbed:
    
    embed = CompactEmbed(
        title=title,
        description=description,
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text=BRAND_FOOTER)
    return embed
