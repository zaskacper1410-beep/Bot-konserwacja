import os
import json
import discord
from discord import app_commands

# =========================
# USTAWIENIA
# =========================

GUILD_ID = 1537385203985547364
OWNER_ROLE_ID = 1538951339739193355
MAINTENANCE_ROLE_ID = 1539355190707097650

DATA_FILE = "roles_backup.json"

# =========================
# BOT
# =========================

intents = discord.Intents.default()
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


# =========================
# ZAPIS RÓL
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
# SPRAWDZANIE ROLI WŁAŚCICIEL
# =========================

def has_owner_role(member):
    return any(role.id == OWNER_ROLE_ID for role in member.roles)


# =========================
# KOMENDA
# =========================

@tree.command(
    name="konserwacja",
    description="Zarządzanie przerwą konserwacyjną",
    guild=discord.Object(id=GUILD_ID)
)
@app_commands.describe(
    akcja="Wybierz, czy włączyć czy wyłączyć konserwację"
)
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

    # =========================
    # SPRAWDZENIE SERWERA
    # =========================

    if interaction.guild_id != GUILD_ID:
        await interaction.response.send_message(
            "❌ Ta komenda nie jest dostępna tutaj.",
            ephemeral=True
        )
        return

    # =========================
    # SPRAWDZENIE ROLI
    # =========================

    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "❌ Nie można sprawdzić Twojej roli.",
            ephemeral=True
        )
        return

    if not has_owner_role(interaction.user):
        await interaction.response.send_message(
            "❌ Nie masz uprawnień do tej komendy.",
            ephemeral=True
        )
        return

    guild = interaction.guild

    # =========================
    # POBRANIE RÓL
    # =========================

    maintenance_role = guild.get_role(MAINTENANCE_ROLE_ID)

    if maintenance_role is None:
        await interaction.response.send_message(
            "❌ Nie znaleziono roli „Przerwa konserwacyjna”.",
            ephemeral=True
        )
        return

    if guild.me is None:
        await interaction.response.send_message(
            "❌ Nie udało się pobrać informacji o bocie.",
            ephemeral=True
        )
        return

    bot_top_role = guild.me.top_role

    # =========================
    # WŁĄCZ KONSERWACJĘ
    # =========================

    if akcja.value == "wlacz":

        backup = load_backup()

        if backup:
            await interaction.response.send_message(
                "⚠️ Konserwacja jest już włączona.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        saved_members = 0

        for member in guild.members:

            # Pomijamy boty
            if member.bot:
                continue

            # Zapamiętujemy wszystkie role,
            # którymi bot może zarządzać.
            role_ids = []

            for role in member.roles:

                if role == guild.default_role:
                    continue

                if role.managed:
                    continue

                if role.id == MAINTENANCE_ROLE_ID:
                    continue

                if role >= bot_top_role:
                    continue

                role_ids.append(role.id)

            backup[str(member.id)] = role_ids

            # Usuwamy role, którymi bot może zarządzać,
            # ale zostawiamy rolę Właściciel.
            roles_to_remove = []

            for role in member.roles:

                if role == guild.default_role:
                    continue

                if role.managed:
                    continue

                if role.id == MAINTENANCE_ROLE_ID:
                    continue

                if role.id == OWNER_ROLE_ID:
                    continue

                if role >= bot_top_role:
                    continue

                roles_to_remove.append(role)

            if roles_to_remove:
                try:
                    await member.remove_roles(
                        *roles_to_remove,
                        reason="Włączenie trybu konserwacji"
                    )
                except discord.Forbidden:
                    pass

            # Dodajemy rolę konserwacyjną
            try:
                if maintenance_role not in member.roles:
                    await member.add_roles(
                        maintenance_role,
                        reason="Włączenie trybu konserwacji"
                    )
            except discord.Forbidden:
                pass

            saved_members += 1

        save_backup(backup)

        await interaction.followup.send(
            f"🔧 **Konserwacja włączona.**\n"
            f"Zapisano role {saved_members} osób.",
            ephemeral=True
        )

    # =========================
    # WYŁĄCZ KONSERWACJĘ
    # =========================

    elif akcja.value == "wylacz":

        backup = load_backup()

        if not backup:
            await interaction.response.send_message(
                "⚠️ Nie znaleziono zapisanych ról.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        restored_members = 0

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

                if role is None:
                    continue

                if role.managed:
                    continue

                if role >= bot_top_role:
                    continue

                roles_to_restore.append(role)

            if roles_to_restore:
                try:
                    await member.add_roles(
                        *roles_to_restore,
                        reason="Przywrócenie ról po konserwacji"
                    )
                except discord.Forbidden:
                    pass

            restored_members += 1

        # Czyścimy zapis po zakończeniu konserwacji
        save_backup({})

        await interaction.followup.send(
            f"✅ **Konserwacja wyłączona.**\n"
            f"Przywrócono role {restored_members} osób.",
            ephemeral=True
        )


# =========================
# START BOTA
# =========================

@bot.event
async def on_ready():
    await tree.sync(
        guild=discord.Object(id=GUILD_ID)
    )

    print(f"Bot zalogowany jako {bot.user}")


TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "Brak zmiennej DISCORD_TOKEN!"
    )

bot.run(TOKEN)
