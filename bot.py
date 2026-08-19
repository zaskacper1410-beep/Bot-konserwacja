import os
import sqlite3
import random
import asyncio
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands


# =========================================================
# USTAWIENIA
# =========================================================

GUILD_ID = 1537385203985547364

OWNER_ROLE_ID = 1538951339739193355
MAINTENANCE_ROLE_ID = 1539355190707097650

DATA_DIR = "/app/data"
DB_PATH = os.path.join(DATA_DIR, "bot.db")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GIVEAWAY_IMAGE = os.path.join(BASE_DIR, "giveaway.jpg")


# =========================================================
# BAZA DANYCH
# =========================================================

os.makedirs(DATA_DIR, exist_ok=True)


def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def setup_database():
    connection = db()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS giveaways (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER,
            creator_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            prize TEXT NOT NULL,
            duration_seconds INTEGER NOT NULL,
            winners_count INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            ends_at INTEGER NOT NULL,
            finished_at INTEGER,
            finished INTEGER DEFAULT 0,
            results_started INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS participants (
            giveaway_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (giveaway_id, user_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS maintenance_roles (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            PRIMARY KEY (guild_id, user_id, role_id)
        )
    """)

    connection.commit()
    connection.close()


setup_database()


# =========================================================
# BOT
# =========================================================

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

tree = bot.tree

# Zapobiega uruchomieniu dwóch timerów tego samego giveawayu
active_timers = set()


# =========================================================
# POMOCNICZE
# =========================================================

def now_timestamp():
    return int(datetime.now(timezone.utc).timestamp())


def is_owner(member):
    if not isinstance(member, discord.Member):
        return False

    return any(
        role.id == OWNER_ROLE_ID
        for role in member.roles
    )


async def owner_only(interaction):
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "❌ Nie można sprawdzić Twojej roli.",
            ephemeral=True
        )
        return False

    if not is_owner(interaction.user):
        await interaction.response.send_message(
            "❌ Ta komenda jest dostępna tylko dla roli **Właściciel**.",
            ephemeral=True
        )
        return False

    return True


def get_giveaway(giveaway_id):
    connection = db()
    cursor = connection.cursor()

    cursor.execute(
        "SELECT * FROM giveaways WHERE id = ?",
        (giveaway_id,)
    )

    result = cursor.fetchone()

    connection.close()

    return result


def get_participant_count(giveaway_id):
    connection = db()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT COUNT(*) AS count
        FROM participants
        WHERE giveaway_id = ?
    """, (giveaway_id,))

    result = cursor.fetchone()["count"]

    connection.close()

    return result


def get_participants(giveaway_id):
    connection = db()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT user_id
        FROM participants
        WHERE giveaway_id = ?
    """, (giveaway_id,))

    result = [
        row["user_id"]
        for row in cursor.fetchall()
    ]

    connection.close()

    return result


def parse_duration(text):
    """
    Obsługiwane:
    30
    30 min
    30m
    1h
    2h
    1d
    """

    value = text.lower().strip()

    try:

        if value.endswith("min"):
            number = int(value[:-3].strip())
            return number * 60

        if value.endswith("m"):
            number = int(value[:-1].strip())
            return number * 60

        if value.endswith("h"):
            number = int(value[:-1].strip())
            return number * 60 * 60

        if value.endswith("d"):
            number = int(value[:-1].strip())
            return number * 60 * 60 * 24

        number = int(value)

        return number * 60

    except ValueError:
        return None


# =========================================================
# EMBED GIVEAWAYA
# =========================================================

def build_giveaway_embed(
    giveaway,
    role,
    participant_count,
    finished=False
):

    embed = discord.Embed(
        title=f"🎁 {giveaway['prize']}",
        color=discord.Color.blurple()
    )

    embed.description = (
        "Kliknij 🎉 aby wziąć udział w giveawayu."
    )

    if finished:

        embed.add_field(
            name="Skończył się:",
            value=f"<t:{giveaway['finished_at']}:F>",
            inline=True
        )

    else:

        embed.add_field(
            name="Skończy się za:",
            value=f"<t:{giveaway['ends_at']}:R>",
            inline=True
        )

    embed.add_field(
        name="Ping:",
        value=role.mention,
        inline=True
    )

    embed.add_field(
        name="Osoby które wzięły udział:",
        value=str(participant_count),
        inline=True
    )

    # WAŻNE:
    # ID NIE JEST JUŻ POKAZYWANE PUBLICZNIE.

    if finished:
        embed.set_footer(
            text="Giveaway zakończony"
        )

    else:
        embed.set_footer(
            text="Powodzenia! 🎉"
        )

    embed.set_image(
        url="attachment://giveaway.jpg"
    )

    return embed


# =========================================================
# AKTUALIZACJA WIADOMOŚCI GIVEAWAYA
# =========================================================

async def update_giveaway_message(giveaway_id):

    giveaway = get_giveaway(giveaway_id)

    if giveaway is None:
        return

    guild = bot.get_guild(
        giveaway["guild_id"]
    )

    if guild is None:
        return

    channel = guild.get_channel(
        giveaway["channel_id"]
    )

    if channel is None:
        return

    try:
        message = await channel.fetch_message(
            giveaway["message_id"]
        )
    except Exception:
        return

    role = guild.get_role(
        giveaway["role_id"]
    )

    if role is None:
        return

    count = get_participant_count(
        giveaway_id
    )

    embed = build_giveaway_embed(
        giveaway,
        role,
        count,
        bool(giveaway["finished"])
    )

    if giveaway["finished"]:

        view = discord.ui.View(
            timeout=None
        )

        button = discord.ui.Button(
            label="🎉 Giveaway zakończony",
            style=discord.ButtonStyle.secondary,
            disabled=True
        )

        view.add_item(button)

    else:

        view = GiveawayJoinView(
            giveaway_id
        )

    try:

        # KLUCZOWA RZECZ:
        # zachowujemy istniejący załącznik,
        # dzięki czemu baner nie znika.

        await message.edit(
            embed=embed,
            view=view,
            attachments=message.attachments
        )

    except Exception as error:

        print(
            f"Błąd aktualizacji giveawayu {giveaway_id}: {error}"
        )


# =========================================================
# PRZYCISK WEŹ UDZIAŁ
# =========================================================

class GiveawayJoinButton(discord.ui.Button):

    def __init__(self, giveaway_id):

        super().__init__(
            label="🎉 Weź udział",
            style=discord.ButtonStyle.primary,
            custom_id=f"giveaway_join_{giveaway_id}"
        )

        self.giveaway_id = giveaway_id

    async def callback(self, interaction):

        giveaway = get_giveaway(
            self.giveaway_id
        )

        if giveaway is None:

            await interaction.response.send_message(
                "❌ Ten giveaway nie istnieje.",
                ephemeral=True
            )

            return

        if giveaway["finished"]:

            await interaction.response.send_message(
                "❌ Ten giveaway już się zakończył.",
                ephemeral=True
            )

            return

        if now_timestamp() >= giveaway["ends_at"]:

            await interaction.response.send_message(
                "❌ Ten giveaway właśnie się zakończył.",
                ephemeral=True
            )

            return

        connection = db()
        cursor = connection.cursor()

        cursor.execute("""
            SELECT 1
            FROM participants
            WHERE giveaway_id = ?
            AND user_id = ?
        """, (
            self.giveaway_id,
            interaction.user.id
        ))

        already_joined = cursor.fetchone()

        if already_joined:

            connection.close()

            await interaction.response.send_message(
                "ℹ️ Już bierzesz udział w tym giveawayu.",
                ephemeral=True
            )

            return

        cursor.execute("""
            INSERT INTO participants (
                giveaway_id,
                user_id
            )
            VALUES (?, ?)
        """, (
            self.giveaway_id,
            interaction.user.id
        ))

        connection.commit()
        connection.close()

        await interaction.response.send_message(
            "✅ **Pomyślnie wziąłeś udział w losowaniu!**",
            ephemeral=True
        )

        await update_giveaway_message(
            self.giveaway_id
        )


class GiveawayJoinView(discord.ui.View):

    def __init__(self, giveaway_id):

        super().__init__(
            timeout=None
        )

        self.add_item(
            GiveawayJoinButton(
                giveaway_id
            )
        )


# =========================================================
# MODAL TWORZENIA GIVEAWAYA
# =========================================================

class GiveawayModal(discord.ui.Modal):

    def __init__(self):
        super().__init__(
            title="Utwórz Giveaway"
        )

    czas = discord.ui.TextInput(
        label="Czas trwania",
        placeholder="Min: 30 min",
        required=True,
        max_length=20
    )

    zwyciezcy = discord.ui.TextInput(
        label="Liczba zwycięzców",
        placeholder="Min: 1",
        required=True,
        max_length=5
    )

    nagroda = discord.ui.TextInput(
        label="Nagroda",
        placeholder="Wpisz nagrodę",
        required=True,
        max_length=200
    )

    async def on_submit(self, interaction):

        duration = parse_duration(
            self.czas.value
        )

        if duration is None:

            await interaction.response.send_message(
                "❌ Nieprawidłowy czas.\n"
                "Przykład: `30 min`",
                ephemeral=True
            )

            return

        if duration < 30 * 60:

            await interaction.response.send_message(
                "❌ Giveaway musi trwać minimum **30 minut**.",
                ephemeral=True
            )

            return

        try:
            winners = int(
                self.zwyciezcy.value.strip()
            )

        except ValueError:

            await interaction.response.send_message(
                "❌ Liczba zwycięzców musi być liczbą.",
                ephemeral=True
            )

            return

        if winners < 1:

            await interaction.response.send_message(
                "❌ Liczba zwycięzców musi wynosić minimum **1**.",
                ephemeral=True
            )

            return

        prize = self.nagroda.value.strip()

        if not prize:

            await interaction.response.send_message(
                "❌ Musisz wpisać nagrodę.",
                ephemeral=True
            )

            return

        await interaction.response.send_message(
            "🏷️ **Wybierz rolę, która ma zostać oznaczona:**",
            view=RoleSelectView(
                duration,
                winners,
                prize,
                interaction.user.id
            ),
            ephemeral=True
        )


# =========================================================
# WYBÓR ROLI
# =========================================================

class GiveawayRoleSelect(discord.ui.RoleSelect):

    def __init__(
        self,
        duration,
        winners,
        prize,
        creator_id
    ):

        super().__init__(
            placeholder="Wybierz rolę do oznaczenia...",
            min_values=1,
            max_values=1
        )

        self.duration = duration
        self.winners = winners
        self.prize = prize
        self.creator_id = creator_id

    async def callback(self, interaction):

        if interaction.user.id != self.creator_id:

            await interaction.response.send_message(
                "❌ To nie jest Twój formularz.",
                ephemeral=True
            )

            return

        role = self.values[0]

        if role.is_default():

            await interaction.response.send_message(
                "❌ Nie możesz wybrać `@everyone`.",
                ephemeral=True
            )

            return

        if role.managed:

            await interaction.response.send_message(
                "❌ Nie możesz wybrać zarządzanej roli.",
                ephemeral=True
            )

            return

        guild = interaction.guild
        channel = interaction.channel

        if guild is None or channel is None:
            return

        created_at = now_timestamp()
        ends_at = created_at + self.duration

        connection = db()
        cursor = connection.cursor()

        cursor.execute("""
            INSERT INTO giveaways (
                guild_id,
                channel_id,
                creator_id,
                role_id,
                prize,
                duration_seconds,
                winners_count,
                created_at,
                ends_at,
                finished,
                results_started
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
        """, (
            guild.id,
            channel.id,
            self.creator_id,
            role.id,
            self.prize,
            self.duration,
            self.winners,
            created_at,
            ends_at
        ))

        giveaway_id = cursor.lastrowid

        connection.commit()
        connection.close()

        giveaway = get_giveaway(
            giveaway_id
        )

        embed = build_giveaway_embed(
            giveaway,
            role,
            0,
            False
        )

        view = GiveawayJoinView(
            giveaway_id
        )

        allowed_mentions = discord.AllowedMentions(
            roles=True
        )

        if os.path.exists(GIVEAWAY_IMAGE):

            file = discord.File(
                GIVEAWAY_IMAGE,
                filename="giveaway.jpg"
            )

            message = await channel.send(
                content=role.mention,
                embed=embed,
                view=view,
                file=file,
                allowed_mentions=allowed_mentions
            )

        else:

            # Awaryjnie, jeśli obrazka nie ma
            embed.remove_image()

            message = await channel.send(
                content=role.mention,
                embed=embed,
                view=view,
                allowed_mentions=allowed_mentions
            )

        connection = db()
        cursor = connection.cursor()

        cursor.execute("""
            UPDATE giveaways
            SET message_id = ?
            WHERE id = ?
        """, (
            message.id,
            giveaway_id
        ))

        connection.commit()
        connection.close()

        await interaction.response.edit_message(
            content=(
                "✅ **Giveaway został utworzony!**\n"
                f"ID to **{giveaway_id}**"
            ),
            view=None
        )

        asyncio.create_task(
            giveaway_timer(
                giveaway_id
            )
        )


class RoleSelectView(discord.ui.View):

    def __init__(
        self,
        duration,
        winners,
        prize,
        creator_id
    ):

        super().__init__(
            timeout=120
        )

        self.add_item(
            GiveawayRoleSelect(
                duration,
                winners,
                prize,
                creator_id
            )
        )


# =========================================================
# TIMER
# =========================================================

async def giveaway_timer(giveaway_id):

    if giveaway_id in active_timers:
        return

    active_timers.add(
        giveaway_id
    )

    try:

        giveaway = get_giveaway(
            giveaway_id
        )

        if giveaway is None:
            return

        if giveaway["finished"]:
            return

        wait_time = max(
            0,
            giveaway["ends_at"] - now_timestamp()
        )

        await asyncio.sleep(
            wait_time
        )

        giveaway = get_giveaway(
            giveaway_id
        )

        if giveaway is None:
            return

        if giveaway["finished"]:
            return

        await finish_giveaway(
            giveaway_id
        )

    finally:

        active_timers.discard(
            giveaway_id
        )


# =========================================================
# ZAKOŃCZENIE GIVEAWAYA
# =========================================================

async def finish_giveaway(giveaway_id):

    giveaway = get_giveaway(
        giveaway_id
    )

    if giveaway is None:
        return

    if giveaway["finished"]:
        return

    guild = bot.get_guild(
        giveaway["guild_id"]
    )

    if guild is None:
        return

    channel = guild.get_channel(
        giveaway["channel_id"]
    )

    if channel is None:
        return

    role = guild.get_role(
        giveaway["role_id"]
    )

    if role is None:
        return

    finished_at = now_timestamp()

    connection = db()
    cursor = connection.cursor()

    cursor.execute("""
        UPDATE giveaways
        SET finished = 1,
            finished_at = ?
        WHERE id = ?
    """, (
        finished_at,
        giveaway_id
    ))

    connection.commit()
    connection.close()

    # -----------------------------------------------------
    # AKTUALIZUJEMY GIVEAWAY
    # -----------------------------------------------------

    await update_giveaway_message(
        giveaway_id
    )

    # -----------------------------------------------------
    # POBIERAMY UCZESTNIKÓW
    # -----------------------------------------------------

    participants = get_participants(
        giveaway_id
    )

    # -----------------------------------------------------
    # WIADOMOŚĆ „CZY JESTEŚCIE GOTOWI”
    # -----------------------------------------------------

    allowed_mentions = discord.AllowedMentions(
        roles=True
    )

    announcement = await channel.send(
        content=(
            f"{role.mention} **Czy jesteście gotowi na wyniki???**"
        ),
        allowed_mentions=allowed_mentions
    )

    try:

        await announcement.add_reaction(
            "🔥"
        )

    except Exception as error:

        print(
            f"Nie udało się dodać reakcji: {error}"
        )

    # -----------------------------------------------------
    # CZEKAMY MINUTĘ
    # -----------------------------------------------------

    await asyncio.sleep(
        60
    )

    try:

        await announcement.delete()

    except Exception:
        pass

    # -----------------------------------------------------
    # BRAK UCZESTNIKÓW
    # -----------------------------------------------------

    if not participants:

        embed = discord.Embed(
            title=f"🎁 {giveaway['prize']}",
            description=(
                "❌ Nikt nie wziął udziału w giveawayu."
            ),
            color=discord.Color.blurple()
        )

        await channel.send(
            embed=embed
        )

        return

    # -----------------------------------------------------
    # LOSOWANIE
    # -----------------------------------------------------

    winners_count = min(
        giveaway["winners_count"],
        len(participants)
    )

    winners = random.SystemRandom().sample(
        participants,
        winners_count
    )

    winner_mentions = ", ".join(
        f"<@{user_id}>"
        for user_id in winners
    )

    # -----------------------------------------------------
    # WYNIK
    # -----------------------------------------------------

    result_embed = discord.Embed(
        title=f"🏆 {giveaway['prize']}",
        description=(
            f"**Wygrywa:** {winner_mentions}\n\n"
            "Gratulacje! Zgłoś się na ticket! 🎉"
        ),
        color=discord.Color.blurple()
    )

    await channel.send(
        embed=result_embed
    )


# =========================================================
# MODAL ZAKOŃCZENIA GIVEAWAYA
# =========================================================

class EndGiveawayModal(discord.ui.Modal):

    def __init__(self):
        super().__init__(
            title="Zakończ Giveaway"
        )

    giveaway_id = discord.ui.TextInput(
        label="ID Giveaway",
        placeholder="Wpisz numer ID, np. 7",
        required=True,
        max_length=10
    )

    async def on_submit(self, interaction):

        try:

            giveaway_id = int(
                self.giveaway_id.value.strip()
            )

        except ValueError:

            await interaction.response.send_message(
                "❌ ID musi być liczbą.",
                ephemeral=True
            )

            return

        giveaway = get_giveaway(
            giveaway_id
        )

        if giveaway is None:

            await interaction.response.send_message(
                f"❌ Giveaway o ID **{giveaway_id}** nie istnieje.",
                ephemeral=True
            )

            return

        if giveaway["finished"]:

            await interaction.response.send_message(
                f"ℹ️ Giveaway **#{giveaway_id}** już jest zakończony.",
                ephemeral=True
            )

            return

        await interaction.response.send_message(
            f"⏳ Kończę giveaway **#{giveaway_id}**...",
            ephemeral=True
        )

        await finish_giveaway(
            giveaway_id
        )


# =========================================================
# /GIVEAWAY
# =========================================================

giveaway_group = app_commands.Group(
    name="giveaway",
    description="Zarządzanie giveawayami",
    guild_ids=[GUILD_ID]
)


@giveaway_group.command(
    name="utworz",
    description="Tworzy nowy giveaway"
)
async def giveaway_utworz(
    interaction: discord.Interaction
):

    if not await owner_only(
        interaction
    ):
        return

    await interaction.response.send_modal(
        GiveawayModal()
    )


@giveaway_group.command(
    name="zakoncz",
    description="Otwiera okno zakończenia giveawayu"
)
async def giveaway_zakoncz(
    interaction: discord.Interaction
):

    if not await owner_only(
        interaction
    ):
        return

    await interaction.response.send_modal(
        EndGiveawayModal()
    )


tree.add_command(
    giveaway_group
)


# =========================================================
# /ID
# =========================================================

id_group = app_commands.Group(
    name="id",
    description="Informacje o giveawayach",
    guild_ids=[GUILD_ID]
)


# =========================================================
# /ID GIVEAWAY
# =========================================================

@id_group.command(
    name="giveaway",
    description="Pokazuje ID aktywnego lub ostatniego giveawayu"
)
async def id_giveaway(
    interaction: discord.Interaction
):

    if not await owner_only(
        interaction
    ):
        return

    connection = db()
    cursor = connection.cursor()

    # Najpierw szukamy aktywnego giveawayu
    cursor.execute("""
        SELECT *
        FROM giveaways
        WHERE guild_id = ?
        AND finished = 0
        ORDER BY id DESC
        LIMIT 1
    """, (
        GUILD_ID,
    ))

    giveaway = cursor.fetchone()

    # Jeśli nie ma aktywnego, pokazujemy ostatni
    if giveaway is None:

        cursor.execute("""
            SELECT *
            FROM giveaways
            WHERE guild_id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (
            GUILD_ID,
        ))

        giveaway = cursor.fetchone()

    connection.close()

    if giveaway is None:

        await interaction.response.send_message(
            "ℹ️ Nie ma jeszcze żadnego giveawayu.",
            ephemeral=True
        )

        return

    if giveaway["finished"]:

        status = "ostatni zakończony giveaway"

    else:

        status = "aktywny giveaway"

    await interaction.response.send_message(
        f"🆔 **ID to - {giveaway['id']} -**\n"
        f"Status: **{status}**",
        ephemeral=True
    )


# =========================================================
# /ID STATYSTYKI
# =========================================================

@id_group.command(
    name="statystyki",
    description="Pokazuje statystyki giveawayu"
)
@app_commands.describe(
    id="Numer ID giveawayu"
)
async def id_statystyki(
    interaction: discord.Interaction,
    id: int
):

    if not await owner_only(
        interaction
    ):
        return

    giveaway = get_giveaway(
        id
    )

    if giveaway is None:

        await interaction.response.send_message(
            f"❌ Giveaway o ID **{id}** nie istnieje.",
            ephemeral=True
        )

        return

    guild = interaction.guild

    creator = guild.get_member(
        giveaway["creator_id"]
    )

    role = guild.get_role(
        giveaway["role_id"]
    )

    count = get_participant_count(
        id
    )

    creator_text = (
        creator.mention
        if creator
        else f"<@{giveaway['creator_id']}>"
    )

    role_text = (
        role.mention
        if role
        else f"<@&{giveaway['role_id']}>"
    )

    if giveaway["finished"]:

        finished_text = (
            f"<t:{giveaway['finished_at']}:F>"
        )

    else:

        finished_text = (
            "Giveaway nadal trwa."
        )

    embed = discord.Embed(
        title=f"📊 Statystyki giveawayu #{id}",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="👥 Liczba osób które wzięły udział",
        value=str(count),
        inline=False
    )

    embed.add_field(
        name="👤 Stworzony przez",
        value=creator_text,
        inline=False
    )

    embed.add_field(
        name="🏷️ Ping",
        value=role_text,
        inline=False
    )

    embed.add_field(
        name="🎁 Nagroda",
        value=giveaway["prize"],
        inline=False
    )

    embed.add_field(
        name="🏆 Liczba zwycięzców",
        value=str(giveaway["winners_count"]),
        inline=False
    )

    embed.add_field(
        name="⏰ Kiedy się zakończył",
        value=finished_text,
        inline=False
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


tree.add_command(
    id_group
)


# =========================================================
# KONSERWACJA
# =========================================================

class MaintenanceView(discord.ui.View):

    def __init__(self):
        super().__init__(
            timeout=120
        )

    @discord.ui.button(
        label="Włącz",
        style=discord.ButtonStyle.danger
    )
    async def enable(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if not await owner_only(
            interaction
        ):
            return

        guild = interaction.guild

        if guild is None:
            return

        maintenance_role = guild.get_role(
            MAINTENANCE_ROLE_ID
        )

        if maintenance_role is None:

            await interaction.response.send_message(
                "❌ Nie znaleziono roli Przerwa konserwacyjna.",
                ephemeral=True
            )

            return

        connection = db()
        cursor = connection.cursor()

        cursor.execute("""
            SELECT COUNT(*) AS count
            FROM maintenance_roles
            WHERE guild_id = ?
        """, (
            guild.id,
        ))

        if cursor.fetchone()["count"] > 0:

            connection.close()

            await interaction.response.send_message(
                "⚠️ Konserwacja jest już włączona.",
                ephemeral=True
            )

            return

        if guild.me is None:

            connection.close()
            return

        bot_top_role = guild.me.top_role

        saved = 0

        for member in guild.members:

            if member.bot:
                continue

            roles_to_remove = []

            for role in member.roles:

                if role.is_default():
                    continue

                if role.managed:
                    continue

                if role.id in (
                    MAINTENANCE_ROLE_ID,
                    OWNER_ROLE_ID
                ):
                    continue

                if role >= bot_top_role:
                    continue

                cursor.execute("""
                    INSERT OR IGNORE INTO maintenance_roles (
                        guild_id,
                        user_id,
                        role_id
                    )
                    VALUES (?, ?, ?)
                """, (
                    guild.id,
                    member.id,
                    role.id
                ))

                roles_to_remove.append(
                    role
                )

            if roles_to_remove:

                try:

                    await member.remove_roles(
                        *roles_to_remove,
                        reason="Włączenie przerwy konserwacyjnej"
                    )

                except discord.Forbidden:
                    pass

            try:

                if maintenance_role not in member.roles:

                    await member.add_roles(
                        maintenance_role,
                        reason="Włączenie przerwy konserwacyjnej"
                    )

            except discord.Forbidden:
                pass

            saved += 1

        connection.commit()
        connection.close()

        await interaction.response.send_message(
            f"🔧 **Przerwa konserwacyjna została włączona.**\n"
            f"Przetworzono: **{saved}** osób.",
            ephemeral=True
        )

    @discord.ui.button(
        label="Wyłącz",
        style=discord.ButtonStyle.success
    )
    async def disable(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if not await owner_only(
            interaction
        ):
            return

        guild = interaction.guild

        if guild is None:
            return

        maintenance_role = guild.get_role(
            MAINTENANCE_ROLE_ID
        )

        connection = db()
        cursor = connection.cursor()

        cursor.execute("""
            SELECT user_id, role_id
            FROM maintenance_roles
            WHERE guild_id = ?
        """, (
            guild.id,
        ))

        rows = cursor.fetchall()

        if not rows:

            connection.close()

            await interaction.response.send_message(
                "⚠️ Nie znaleziono zapisanych ról.",
                ephemeral=True
            )

            return

        if guild.me is None:

            connection.close()
            return

        bot_top_role = guild.me.top_role

        restored_users = set()

        for row in rows:

            member = guild.get_member(
                row["user_id"]
            )

            role = guild.get_role(
                row["role_id"]
            )

            if member is None:
                continue

            if role is None:
                continue

            if role.managed:
                continue

            if role >= bot_top_role:
                continue

            try:

                if role not in member.roles:

                    await member.add_roles(
                        role,
                        reason="Wyłączenie przerwy konserwacyjnej"
                    )

            except discord.Forbidden:
                pass

            restored_users.add(
                member.id
            )

        for member_id in restored_users:

            member = guild.get_member(
                member_id
            )

            if member is None:
                continue

            if maintenance_role is None:
                continue

            try:

                if maintenance_role in member.roles:

                    await member.remove_roles(
                        maintenance_role,
                        reason="Wyłączenie przerwy konserwacyjnej"
                    )

            except discord.Forbidden:
                pass

        cursor.execute("""
            DELETE FROM maintenance_roles
            WHERE guild_id = ?
        """, (
            guild.id,
        ))

        connection.commit()
        connection.close()

        await interaction.response.send_message(
            f"✅ **Przerwa konserwacyjna została wyłączona.**\n"
            f"Przywrócono role dla: **{len(restored_users)}** osób.",
            ephemeral=True
        )


# =========================================================
# /KONSERWACJA
# =========================================================

@tree.command(
    name="konserwacja",
    description="Zarządzanie przerwą konserwacyjną",
    guild=discord.Object(id=GUILD_ID)
)
async def maintenance_command(
    interaction: discord.Interaction
):

    if not await owner_only(
        interaction
    ):
        return

    await interaction.response.send_message(
        "🔧 **Przerwa konserwacyjna**\n\n"
        "Wybierz akcję:",
        view=MaintenanceView(),
        ephemeral=True
    )


# =========================================================
# GOTOWE GIVEAWAYY PO RESTARCIE
# =========================================================

@bot.event
async def on_ready():

    setup_database()

    guild_object = discord.Object(
        id=GUILD_ID
    )

    try:

        await tree.sync(
            guild=guild_object
        )

        print(
            "Komendy slash zostały zsynchronizowane."
        )

    except Exception as error:

        print(
            f"Błąd synchronizacji komend: {error}"
        )

    print(
        f"Bot zalogowany jako {bot.user}"
    )

    # -----------------------------------------------------
    # ODNAWIANIE TIMERÓW
    # -----------------------------------------------------

    connection = db()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id
        FROM giveaways
        WHERE guild_id = ?
        AND finished = 0
    """, (
        GUILD_ID,
    ))

    active = cursor.fetchall()

    connection.close()

    for row in active:

        asyncio.create_task(
            giveaway_timer(
                row["id"]
            )
        )


# =========================================================
# TOKEN
# =========================================================

TOKEN = os.getenv(
    "DISCORD_TOKEN"
)

if not TOKEN:

    raise RuntimeError(
        "Brak zmiennej DISCORD_TOKEN!"
    )


bot.run(TOKEN)
