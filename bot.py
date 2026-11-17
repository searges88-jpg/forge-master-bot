import math
import os
import sqlite3
import threading
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
# Játékbeli szabályok
# current_level: a jelenleg fejlesztett szint.
# current_points: az ezen a szinten már összegyűjtött pont (pl. 95/110).
# ------------------------------------------------------------

PILLARS = {
    "skills": {
        "display": "Skill",
        "points_per_level": 110,
        "points_per_summon": 5,
        "default_cost": 40.0,
        "has_lucky": False,
    },
    "mounts": {
        "display": "Mount",
        "points_per_level": 20,
        "points_per_summon": 1,
        "default_cost": 50.0,
        "has_lucky": True,
    },
    "pets": {
        "display": "Pet",
        "points_per_level": 23,
        "points_per_summon": 1,
        "default_cost": 100.0,
        "fixed_cost": 100.0,
        "has_lucky": True,
    },
}

MAX_LEVEL = 100

# ------------------------------------------------------------
# Adatbázis: minden Discord-felhasználó saját profilja
# ------------------------------------------------------------

DB_FILE = os.environ.get("DATABASE_PATH", "forge_master_profiles.db")


def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles_v2 (
                user_id INTEGER NOT NULL,
                pillar TEXT NOT NULL,
                current_level INTEGER NOT NULL,
                current_points INTEGER NOT NULL,
                summon_cost REAL NOT NULL,
                lucky_percent REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, pillar)
            )
            """
        )
        # Régi (idézésszámos) profilok átmásolása pontokra, egyszer.
        old = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='profiles'"
        ).fetchone()
        if old:
            for user_id, pillar, level, used, cost, lucky in conn.execute(
                "SELECT user_id, pillar, current_level, summons_used, "
                "summon_cost, lucky_percent FROM profiles"
            ).fetchall():
                rules = PILLARS.get(pillar)
                if not rules:
                    continue
                points = min(used * rules["points_per_summon"],
                             rules["points_per_level"] - 1)
                conn.execute(
                    "INSERT OR IGNORE INTO profiles_v2 VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, pillar, level, points, cost, lucky),
                )
            conn.execute("ALTER TABLE profiles RENAME TO profiles_old_backup")
        conn.commit()


def get_profile(user_id: int, pillar: str):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT current_level, current_points, summon_cost, lucky_percent
            FROM profiles_v2
            WHERE user_id = ? AND pillar = ?
            """,
            (user_id, pillar),
        ).fetchone()
        return dict(row) if row else None


def save_profile(user_id: int, pillar: str, current_level: int,
                 current_points: int, summon_cost: float, lucky_percent: float):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            INSERT INTO profiles_v2
                (user_id, pillar, current_level, current_points, summon_cost, lucky_percent)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, pillar) DO UPDATE SET
                current_level = excluded.current_level,
                current_points = excluded.current_points,
                summon_cost = excluded.summon_cost,
                lucky_percent = excluded.lucky_percent
            """,
            (user_id, pillar, current_level, current_points, summon_cost, lucky_percent),
        )
        conn.commit()


# ------------------------------------------------------------
# Számítás
# ------------------------------------------------------------


def expected_summons_with_lucky(points_needed: int, lucky_probability: float) -> float:
    """
    Egy idézés 1 pontot ad, Lucky siker esetén +1 pontot.
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


def calculate(pillar: str, current_level: int, current_points: int,
              summon_cost: float, lucky_percent: float):
    rules = PILLARS[pillar]
    ppl = rules["points_per_level"]
    pps = rules["points_per_summon"]

    remaining_points_current = ppl - current_points
    # A 100. szint a végpont: 1-ről 100-ra 99 szintet kell feltölteni.
    later_levels = MAX_LEVEL - 1 - current_level
    points_needed = remaining_points_current + later_levels * ppl

    summons_current = math.ceil(remaining_points_current / pps)
    normal_summons = math.ceil(points_needed / pps)
    normal_cost = normal_summons * summon_cost

    lucky_probability = lucky_percent / 100.0
    if rules["has_lucky"] and lucky_probability > 0:
        expected_summons = expected_summons_with_lucky(points_needed, lucky_probability)
    else:
        expected_summons = float(normal_summons)

    return {
        "remaining_points_current": remaining_points_current,
        "summons_current": summons_current,
        "later_levels": later_levels,
        "points_needed": points_needed,
        "normal_summons": normal_summons,
        "normal_cost": normal_cost,
        "expected_summons": expected_summons,
        "expected_cost": expected_summons * summon_cost,
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
        self.tree.add_command(ascension_group)
        await self.tree.sync()
        print("A slash parancsok szinkronizálása megtörtént.")


client = ForgeMasterBot()


@client.event
async def on_ready():
    print(f"Bejelentkezve mint: {client.user} (ID: {client.user.id})")


@client.tree.error
async def on_app_command_error(interaction: discord.Interaction,
                               error: app_commands.AppCommandError):
    print(f"Parancshiba: {error!r}")
    msg = "Hiba történt a parancs futtatása közben. Próbáld újra később."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


async def run_ascension(interaction: discord.Interaction, key: str,
                        current_level: int, current_points: Optional[int],
                        summon_cost: Optional[float], lucky_percent: Optional[float]):
    rules = PILLARS[key]
    ppl = rules["points_per_level"]
    previous = get_profile(interaction.user.id, key)

    if current_level >= MAX_LEVEL:
        await interaction.response.send_message(
            f"🎉 A {rules['display']} már {MAX_LEVEL}. szinten van, nincs több fejlesztés. "
            "Itt az ideje az Ascensionnek!",
            ephemeral=True,
        )
        return

    # Ha nincs megadva új érték, a korábban mentettet használjuk.
    if previous:
        if current_points is not None:
            points_value = current_points
        elif current_level != previous["current_level"]:
            points_value = 0  # új szinten alapból 0 pont
        else:
            points_value = previous["current_points"]
        cost_value = previous["summon_cost"] if summon_cost is None else summon_cost
        lucky_value = previous["lucky_percent"] if lucky_percent is None else lucky_percent
    else:
        points_value = 0 if current_points is None else current_points
        cost_value = rules["default_cost"] if summon_cost is None else summon_cost
        lucky_value = 0.0 if lucky_percent is None else lucky_percent

    if "fixed_cost" in rules:
        cost_value = rules["fixed_cost"]
    if not rules["has_lucky"]:
        lucky_value = 0.0

    if points_value >= ppl:
        await interaction.response.send_message(
            f"A {rules['display']} szinten legfeljebb {ppl - 1}/{ppl} pont adható meg. "
            "Ha a szint már megvan, írd be a következő szintet 0 ponttal.",
            ephemeral=True,
        )
        return

    save_profile(interaction.user.id, key, current_level, points_value,
                 cost_value, lucky_value)
    result = calculate(key, current_level, points_value, cost_value, lucky_value)

    embed = discord.Embed(
        title=f"⚒️ Forge Master – {rules['display']} kalkulátor",
        description=(
            f"**Jelenlegi szint:** {current_level}/{MAX_LEVEL}\n"
            f"**Pont ezen a szinten:** {points_value}/{ppl}"
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Hátralévő fejlesztés",
        value=(
            f"**Hiányzó pont a szint befejezéséhez:** {result['remaining_points_current']} "
            f"({result['summons_current']} idézés)\n"
            f"**Hátralévő szintek a jelenlegi után:** {result['later_levels']}\n"
            f"**Szükséges pont összesen:** {fmt_number(result['points_needed'])}"
        ),
        inline=False,
    )
    cost_label = f"{fmt_number(cost_value)} pont"
    if "fixed_cost" in rules:
        cost_label += " (fix)"
    embed.add_field(
        name="Lucky nélküli költség" if rules["has_lucky"] else "Költség",
        value=(
            f"**Idézések:** {fmt_number(result['normal_summons'])}\n"
            f"**Ár / idézés:** {cost_label}\n"
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
        embed.set_footer(text="A Lucky-s érték statisztikai várható érték, nem garantált költség.")
    else:
        embed.set_footer(text="A profilod automatikusan el lett mentve.")

    await interaction.response.send_message(embed=embed)


ascension_group = app_commands.Group(
    name="ascension",
    description="Skill, Mount vagy Pet fejlesztési költség kalkulálása és profil mentése.",
)


@ascension_group.command(name="skill", description="Skill fejlesztési költség kalkulálása.")
@app_commands.describe(
    current_level="A jelenlegi szinted (1–100, a 100 a maximum).",
    current_points="Hány ponton állsz ezen a szinten? Pl. 95, ha 95/110.",
    summon_cost="Egy idézés ára Skill Ticketben (alapból 40, tech fa nélkül).",
)
async def ascension_skill(
    interaction: discord.Interaction,
    current_level: app_commands.Range[int, 1, 100],
    current_points: Optional[app_commands.Range[int, 0, 109]] = None,
    summon_cost: Optional[app_commands.Range[float, 1, 1000000]] = None,
):
    await run_ascension(interaction, "skills", current_level, current_points, summon_cost, None)


@ascension_group.command(name="mount", description="Mount fejlesztési költség kalkulálása.")
@app_commands.describe(
    current_level="A jelenlegi szinted (1–100, a 100 a maximum).",
    current_points="Hány ponton állsz ezen a szinten? Pl. 12, ha 12/20.",
    summon_cost="Egy idézés ára Clock Winderben (alapból 50, tech fa nélkül).",
    lucky_percent="Lucky esély százalékban (alapból 0%).",
)
async def ascension_mount(
    interaction: discord.Interaction,
    current_level: app_commands.Range[int, 1, 100],
    current_points: Optional[app_commands.Range[int, 0, 19]] = None,
    summon_cost: Optional[app_commands.Range[float, 1, 1000000]] = None,
    lucky_percent: Optional[app_commands.Range[float, 0, 100]] = None,
):
    await run_ascension(interaction, "mounts", current_level, current_points, summon_cost, lucky_percent)


@ascension_group.command(name="pet", description="Pet fejlesztési költség kalkulálása (idézés fixen 100 pont).")
@app_commands.describe(
    current_level="A jelenlegi szinted (1–100, a 100 a maximum).",
    current_points="Hány ponton állsz ezen a szinten? Pl. 15, ha 15/23.",
    lucky_percent="Lucky esély százalékban (alapból 0%).",
)
async def ascension_pet(
    interaction: discord.Interaction,
    current_level: app_commands.Range[int, 1, 100],
    current_points: Optional[app_commands.Range[int, 0, 22]] = None,
    lucky_percent: Optional[app_commands.Range[float, 0, 100]] = None,
):
    await run_ascension(interaction, "pets", current_level, current_points, None, lucky_percent)


@client.tree.command(name="profil", description="Megmutatja a saját elmentett Forge Master adataidat.")
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
            f"Szint: **{data['current_level']}/{MAX_LEVEL}**\n"
            f"Pont ezen a szinten: **{data['current_points']}/{rules['points_per_level']}**\n"
            f"Idézés ára: **{fmt_number(data['summon_cost'])} pont**"
        )
        if rules["has_lucky"]:
            text += f"\nLucky: **{fmt_number(data['lucky_percent'])}%**"
        embed.add_field(name=rules["display"], value=text, inline=False)

    if not found_any:
        embed.description = (
            "Még nincs mentett adatod. Használd az `/ascension skill`, "
            "`/ascension mount` vagy `/ascension pet` parancsot a profilod létrehozásához."
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


if __name__ == "__main__":
    init_db()
    threading.Thread(target=run_web, daemon=True).start()
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        print("Hiba: A DISCORD_TOKEN környezeti változó nincs beállítva!")
    else:
        client.run(TOKEN)
