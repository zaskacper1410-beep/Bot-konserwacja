import os
import json
import discord
from discord import app_commands

# =========================
# USTAWIENIA
# =========================

GUILD_ID = 1537385203985547364
OWNER_ID = 862080420005937218
MAINTENANCE_ROLE_ID = 1539355190707097650

DATA_FILE = "roles_backup.json"

intents = discord.Intents.default()
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


# =========================
# ZAPIS / ODCZYT RÓL
# =========================

def load_backup():
    if not os.path.exists(DATA_FILE):
        return {}

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return {}


def save_backup(data):
    with open(DATA_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)


# =========================
# SPRAWDZANIE UPRAWNIEŃ
# =========================

def is_owner(user_id):
    return user_id == OWNER_ID


# =========================
# /KONSERWACJA WLACZ
# =========================

@tree.command(
    name="konserwacja",
    description="Zarządzanie trybem przerwy konserwacyjnej",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(akcja="Włącz albo wyłącz tryb konserwacji")
@app_commands.choices(
    akcja=[
        app_commands.Choice(name="Włącz", value="wlacz"),
        app_commands.Choice(name="Wyłącz", value="wylacz")
    ]
)
async def konserwacja(
    interaction: discord.Interaction,
    akcja: app_commands.Choice[str]
):

    # TYLKO TY
    if not is_owner(interaction.user.id):
        await interaction.response.send_message(
            "❌ Nie masz uprawnień do używania tego bota.",
            ephemeral=True
        )
        return

    # TYLKO NASZ SERWER
    if interaction.guild_id != GUILD_ID:
        await interaction.response.send_message(
            "❌ Ta komenda nie może być używana na tym serwerze.",
            ephemeral=True
        )
        return

    guild = interaction.guild
    maintenance_role = guild.get_role(MAINTENANCE_ROLE_ID)

    if maintenance_role is None:
        await interaction.response.send_message(
            "❌ Nie znaleziono roli „Przerwa konserwacyjna”.",
            ephemeral=True
        )
        return

    # =========================
    # WŁĄCZANIE
    # =========================

    if akcja.value == "wlacz":

        backup = load_backup()

        if backup:
            await interaction.response.send_message(
                "⚠️ Konserwacja jest już włączona albo istnieje zapis poprzedniej konserwacji.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        for member in guild.members:

            # Pomijamy boty
            if member.bot:
                continue

            # Zapamiętujemy role użytkownika
            role_ids = [
                role.id
                for role in member.roles
                if role != guild.default_role
                and not role.managed
                and role < guild.me.top_role
            ]

            backup[str(member.id)] = role_ids

            # Usuwamy role, którymi bot może zarządzać
            removable_roles = [
                role
                for role in member.roles
                if role != guild.default_role
                and not role.managed
                and role < guild.me.top_role
                and role != maintenance_role
            ]

            if removable_roles:
                try:
                    await member.remove_roles(
                        *removable_roles,
                        reason="Włączenie trybu konserwacji"
                    )
                except discord.Forbidden:
                    pass

            # Nadajemy rolę konserwacyjną
            try:
                if maintenance_role not in member.roles:
                    await member.add_roles(
                        maintenance_role,
                        reason="Włączenie trybu konserwacji"
                    )
            except discord.Forbidden:
                pass

        save_backup(backup)

        await interaction.followup.send(
            "🔧 **Tryb konserwacji został włączony.**\n"
            "Role użytkowników zostały zapisane i zastąpione rolą `Przerwa konserwacyjna`.",
            ephemeral=True
        )

    # =========================
    # WYŁĄCZANIE
    # =========================

    elif akcja.value == "wylacz":

        backup = load_backup()

        if not backup:
            await interaction.response.send_message(
                "⚠️ Nie znaleziono zapisu poprzednich ról.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        restored = 0

        for member_id, role_ids in backup.items():

            member = guild.get_member(int(member_id))

            if member is None:
                continue

            # Usuwamy rolę konserwacyjną
            try:
                if maintenance_role in member.roles:
                    await member.remove_roles(
                        maintenance_role,
                        reason="Wyłączenie trybu konserwacji"
                    )
            except discord.Forbidden:
                pass

            # Przywracamy poprzednie role
            roles_to_restore = []

            for role_id in role_ids:
                role = guild.get_role(role_id)

                if role is not None:
                    if not role.managed and role < guild.me.top_role:
                        roles_to_restore.append(role)

            if roles_to_restore:
                try:
                    await member.add_roles(
                        *roles_to_restore,
                        reason="Przywrócenie ról po konserwacji"
                    )
                except discord.Forbidden:
                    pass

            restored += 1

        # Czyścimy zapis
        save_backup({})

        await interaction.followup.send(
            f"✅ **Tryb konserwacji został wyłączony.**\n"
            f"Przywrócono role dla {restored} osób.",
            ephemeral=True
        )


# =========================
# URUCHOMIENIE BOTA
# =========================

@bot.event
async def on_ready():
    await tree.sync(guild=discord.Object(id=GUILD_ID))
    print(f"Bot zalogowany jako {bot.user}")


TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError("Brak zmiennej DISCORD_TOKEN!")

bot.run(TOKEN)
