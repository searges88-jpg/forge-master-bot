import os
import threading
import sqlite3
from typing import Optional

import discord
from discord import app_commands
from flask import Flask

# ------------------------------------------------------------
# Webes életjelzés a Render Web Service számára
# ------------------------------------------------------------

app = Flask(__name__)


@app.route("/")
def home():
    return "A Forge Master Discord bot aktívan fut!"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)


# ------------------------------------------------------------
# Adatbázis: minden Discord-felhasználó saját profilja
# ------------------------------------------------------------

DB_FILE = os.environ.get("DATABASE_PATH", "forge_master_profiles.db")


def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                user_id INTEGER NOT NULL,
                pillar TEXT NOT NULL,
                current_level INTEGER NOT NULL,
                summons_used INTEGER NOT NULL,
                summon_cost REAL NOT NULL,
                lucky_percent REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, pillar)
            )
            """
        )
        conn.commit()


def get_profile(user_id: int, pillar: str):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT current_level, summons_used, summon_cost, lucky_percent
            FROM profiles
            WHERE user_id = ? AND pillar = ?
            """,
            (user_id, pillar),
        ).fetchone()
        return dict(row) if row else None


def save_profile(
    user_id: int,
    pillar: str,
    current_level: int,
    summons_used: int,
    summon_cost: float,
    lucky_percent: float,
):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            INSERT INTO profiles
                (user_id, pillar, current_level, summons_used, summon_cost, lucky_percent)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, pillar) DO UPDATE SET
                current_level = excluded.current_level,
                summons_used = excluded.summons_used,
                summon_cost = excluded.summon_cost,
                lucky_percent = excluded.lucky_percent
            """,
            (
                user_id,
                pillar,
                current_level,
                summons_used,
                summon_cost,
                lucky_percent,
            ),
        )
        conn.commit()


# ------------------------------------------------------------
# Játékbeli szabályok
# A current_level a jelenleg fejlesztett szint.
# A summons_used az ezen a szinten már elhasznált idézések száma.
# ------------------------------------------------------------

PILLARS = {
    "skills": {
        "display": "Skill",
        "points_per_level": 110,
        "points_per_summon": 5,
        "default_cost": 200.0,
        "has_lucky": False,
    },
    "mounts": {
        "display": "Mount",
        "points_per_level": 20,
        "points_per_summon": 1,
        "default_cost": 200.0,
        "has_lucky": True,
    },
    "pets": {
        "display": "Pet",
        "points_per_level": 23,
        "points_per_summon": 1,
        "default_cost": 100.0,
        "has_lucky": True,
    },
}

MAX_LEVEL = 100


def expected_summons_with_lucky(points_needed: int, lucky_probability: float) -> float:
    """
    Egy idézés legalább 1 pontot ad.
    Lucky siker esetén még +1 pontot ad.
    Várható idézésszám dinamikus programozással:
    E(n) = 1 + (1-p) * E(n-1) + p * E(n-2)
    """
    if points_needed <= 0:
        return 0.0

    expected = [0.0] * (points_needed + 1)

    for points in range(1, points_needed + 1):
        if points == 1:
            expected[points] = 1.0
        else:
            expected[points] = (
                1.0
                + (1.0 - lucky_probability) * expected[points - 1]
                + lucky_probability * expected[points - 2]
            )

    return expected[points_needed]


def calculate(pillar: str, current_level: int, summons_used: int,
              summon_cost: float, lucky_percent: float):
    rules = PILLARS[pillar]

    points_per_level = rules["points_per_level"]
    points_per_summon = rules["points_per_summon"]

    summons_per_level = points_per_level // points_per_summon

    # A megadott szinten még hátralévő idézések.
    remaining_current_level = summons_per_level - summons_used

    # A jelenlegi szint után hátralévő szintek teljes idézésszáma.
    later_levels = MAX_LEVEL - current_level
    remaining_summons_without_lucky = (
        remaining_current_level + later_levels * summons_per_level
    )

    # A hátralévő fejlesztési pontok száma.
    points_needed = remaining_summons_without_lucky * points_per_summon

    normal_cost = remaining_summons_without_lucky * summon_cost

    lucky_probability = lucky_percent / 100.0
    if rules["has_lucky"] and lucky_probability > 0:
        expected_summons = expected_summons_with_lucky(
            points_needed, lucky_probability
        )
        expected_cost = expected_summons * summon_cost
    else:
        expected_summons = float(remaining_summons_without_lucky)
        expected_cost = normal_cost

    return {
        "summons_per_level": summons_per_level,
        "remaining_current_level": remaining_current_level,
        "later_levels": later_levels,
        "points_needed": points_needed,
        "normal_summons": remaining_summons_without_lucky,
        "normal_cost": normal_cost,
        "expected_summons": expected_summons,
        "expected_cost": expected_cost,
    }


def fmt_number(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}".replace(",", " ")
    return f"{value:,.1f}".replace(",", " ").replace(".", ",")


# ------------------------------------------------------------
# Discord bot
# ------------------------------------------------------------

intents = discord.Intents.default()


class ForgeMasterBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        print("A slash parancsok szinkronizálása megtörtént.")


client = ForgeMasterBot()


@client.event
async def on_ready():
    print(f"Bejelentkezve mint: {client.user} (ID: {client.user.id})")


@client.tree.command(
    name="ascension",
    description="Skill, Mount vagy Pet fejlesztési költség kalkulálása és profil mentése.",
)
@app_commands.describe(
    pillar="Melyik fejlesztést szeretnéd kiszámolni?",
    current_level="A jelenleg fejlesztett szinted (1–100).",
    summons_used="Ezen a szinten már felhasznált idézések száma.",
    summon_cost="Egy idézés ára pontban. Skill/Mount esetén egyéni; Pet fixen 100.",
    lucky_percent="Lucky esély százalékban. Mount/Pet esetén; alapból 0%.",
)
@app_commands.choices(
    pillar=[
        app_commands.Choice(name="Skill", value="skills"),
        app_commands.Choice(name="Mount", value="mounts"),
        app_commands.Choice(name="Pet", value="pets"),
    ]
)
async def ascension(
    interaction: discord.Interaction,
    pillar: app_commands.Choice[str],
    current_level: app_commands.Range[int, 1, 100],
    summons_used: Optional[app_commands.Range[int, 0, 1000]] = None,
    summon_cost: Optional[app_commands.Range[float, 1, 1000000]] = None,
    lucky_percent: Optional[app_commands.Range[float, 0, 100]] = None,
):
    key = pillar.value
    rules = PILLARS[key]
    previous = get_profile(interaction.user.id, key)

    # Ha a felhasználó nem ír be új értéket, a korábban mentett értéket használjuk.
    if previous:
        # Ha a játékos másik szintre lép, az új szinten alapból 0 idézést
        # feltételezünk, hacsak külön nem ad meg más értéket.
        if summons_used is not None:
            used_value = summons_used
        elif current_level != previous["current_level"]:
            used_value = 0
        else:
            used_value = previous["summons_used"]

        cost_value = previous["summon_cost"] if summon_cost is None else summon_cost
        lucky_value = previous["lucky_percent"] if lucky_percent is None else lucky_percent
    else:
        used_value = 0 if summons_used is None else summons_used
        cost_value = rules["default_cost"] if summon_cost is None else summon_cost
        lucky_value = 0.0 if lucky_percent is None else lucky_percent

    # Pet idézés ára fixen 100 pont.
    if key == "pets":
        cost_value = 100.0

    if not rules["has_lucky"]:
        lucky_value = 0.0

    summons_per_level = (
        rules["points_per_level"] // rules["points_per_summon"]
    )

    if used_value >= summons_per_level:
        await interaction.response.send_message(
            (
                f"Az adott {rules['display']} szinten legfeljebb "
                f"{summons_per_level - 1} már felhasznált idézés adható meg. "
                "Ha a szint már teljesült, írd be a következő szintet, "
                "és az aktuális szinten felhasznált idézések számát állítsd 0-ra."
            ),
            ephemeral=True,
        )
        return

    save_profile(
        interaction.user.id,
        key,
        current_level,
        used_value,
        cost_value,
        lucky_value,
    )

    result = calculate(
        key,
        current_level,
        used_value,
        cost_value,
        lucky_value,
    )

    embed = discord.Embed(
        title=f"⚒️ Forge Master – {rules['display']} kalkulátor",
        description=(
            f"**Jelenlegi szint:** {current_level}/100\n"
            f"**Ezen a szinten már felhasznált idézés:** {used_value}"
        ),
        color=discord.Color.blurple(),
    )

    embed.add_field(
        name="Hátralévő fejlesztés",
        value=(
            f"**Hátralévő szintek a jelenlegi után:** {result['later_levels']}\n"
            f"**Idézés / szint:** {result['summons_per_level']}\n"
            f"**Idézés a jelenlegi szint befejezéséhez:** "
            f"{result['remaining_current_level']}\n"
            f"**Szükséges pont:** {fmt_number(result['points_needed'])}"
        ),
        inline=False,
    )

    embed.add_field(
        name="Lucky nélküli költség",
        value=(
            f"**Idézések:** {fmt_number(result['normal_summons'])}\n"
            f"**Ár / idézés:** {fmt_number(cost_value)} pont\n"
            f"**Összesen:** {fmt_number(result['normal_cost'])} pont"
        ),
        inline=False,
    )

    if rules["has_lucky"]:
        embed.add_field(
            name="Lucky kalkuláció",
            value=(
                f"**Lucky esély:** {fmt_number(lucky_value)}%\n"
                f"**Várható idézésszám:** {fmt_number(result['expected_summons'])}\n"
                f"**Várható költség:** {fmt_number(result['expected_cost'])} pont"
            ),
            inline=False,
        )
        embed.set_footer(
            text="A Lucky-s érték statisztikai várható érték, nem garantált költség."
        )
    else:
        embed.set_footer(text="A profilod automatikusan el lett mentve.")

    await interaction.response.send_message(embed=embed)


@client.tree.command(
    name="profil",
    description="Megmutatja a saját elmentett Forge Master adataidat.",
)
async def profile(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 Elmentett Forge Master profil",
        description="Adataid a Discord-fiókodhoz vannak mentve.",
        color=discord.Color.blurple(),
    )

    found_any = False
    for key, rules in PILLARS.items():
        data = get_profile(interaction.user.id, key)
        if not data:
            continue

        found_any = True
        text = (
            f"Szint: **{data['current_level']}/100**\n"
            f"Aktuális szinten felhasznált idézés: **{data['summons_used']}**\n"
            f"Idézés ára: **{fmt_number(data['summon_cost'])} pont**"
        )
        if rules["has_lucky"]:
            text += f"\nLucky: **{fmt_number(data['lucky_percent'])}%**"
        embed.add_field(name=rules["display"], value=text, inline=False)

    if not found_any:
        embed.description = (
            "Még nincs mentett adatod. Használd az `/ascension` parancsot "
            "a Skill, Mount vagy Pet profilod létrehozásához."
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


if __name__ == "__main__":
    init_db()

    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()

    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        print("Hiba: A DISCORD_TOKEN környezeti változó nincs beállítva!")
    else:
        client.run(TOKEN)
