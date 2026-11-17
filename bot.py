import os
import threading
import discord
from discord import app_commands
from flask import Flask

# Pici webes szerver, hogy a Render Web Service ne álljon le
app = Flask('')

@app.route('/')
def home():
    return "A Forge Master Discord bot aktívan fut!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# Discord Bot logika
class ForgeMasterBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        print("A parancsok szinkronizálása megtörtént.")

client = ForgeMasterBot()

@client.event
async def on_ready():
    print(f"Bejelentkezve mint: {client.user.name} (ID: {client.user.id})")

@client.tree.command(name="ascension", description="Forge Master ascension kalkulátor és követelmény ellenőrző.")
@app_commands.describe(
    pillar="Melyik pillért szeretnéd fejleszteni?",
    current_level="A jelenlegi szinted az adott pillérben"
)
@app_commands.choices(pillar=[
    app_commands.Choice(name="Forge (Kovácsműhely - min. 35. szint)", value="forge"),
    app_commands.Choice(name="Pets (Háziállatok - min. 100. szint)", value="pets"),
    app_commands.Choice(name="Skills (Képességek - min. 100. szint)", value="skills"),
    app_commands.Choice(name="Mounts (Hátasok - min. 100. szint)", value="mounts")
])
async def ascension(interaction: discord.Interaction, pillar: str, current_level: int):
    min_requirements = {"forge": 35, "pets": 100, "skills": 100, "mounts": 100}
    req = min_requirements.get(pillar, 0)
    
    if current_level < req:
        embed = discord.Embed(
            title="⚠️ Még nem állsz készen az Ascensionre!",
            description=f"A(z) **{pillar.capitalize()}** pillér ascendálásához legalább **{req}. szint** szükséges.",
            color=discord.Color.red()
        )
        embed.add_field(name="Jelenlegi szinted", value=str(current_level), inline=True)
        embed.add_field(name="Hiányzó szintek", value=str(req - current_level), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    embed = discord.Embed(
        title=f"🛡️ Forge Master Kalkulátor: {pillar.capitalize()}",
        description=f"A megadott `{current_level}`-es szint alapján a feltételek teljesülnek!",
        color=discord.Color.green()
    )
    embed.add_field(
        name="Ajánlott stratégia", 
        value="A biztonságos fejlődéshez érdemes megvárni, amíg az erőforrások bőségesebben rendelkezésre állnak.", 
        inline=False
    )
    embed.set_footer(text="Forge Master Clan Bot")
    
    await interaction.response.send_message(embed=embed)

if __name__ == "__main__":
    t = threading.Thread(target=run_web)
    t.start()
    
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        print("Hiba: A DISCORD_TOKEN környezeti változó nincs beállítva!")
    else:
        bot.run(TOKEN)
