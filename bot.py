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
GIVEAWAY_IMAGE = "giveaway.jpg"


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
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

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
            finished INTEGER DEFAULT 0
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


# =========================================================
# POMOCNICZE
# =========================================================

def is_owner(member: discord.Member) -> bool:
    return any(role.id == OWNER_ROLE_ID for role in member.roles)


def now_timestamp() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def format_duration(seconds: int) -> str:
    minutes = seconds // 60
    hours = minutes // 60
    minutes = minutes % 60

    if hours:
        return f"{hours} godz. {minutes} min."
    return f"{minutes} min."


def participant_count(giveaway_id: int) -> int:
    connection = db()
    cursor = connection.cursor()

    cursor.execute(
        "SELECT COUNT(*) AS count FROM participants WHERE giveaway_id = ?",
        (giveaway_id,)
    )

    result = cursor.fetchone()["count"]

    connection.close()
    return result


# =========================================================
# SPRAWDZANIE WŁAŚCICIELA
# =========================================================

async def owner_only(interaction: discord.Interaction) -> bool:

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


# =========================================================
# GIVEAWAY - MODAL
# =========================================================

class GiveawayModal(discord.ui.Modal, title="Utwórz Giveaway"):

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

    async def on_submit(self, interaction: discord.Interaction):

        # -------------------------------------------------
        # CZAS
        # -------------------------------------------------

        raw_time = self.czas.value.lower().strip()

        try:
            if raw_time.endswith("min"):
                minutes = int(raw_time.replace("min", "").strip())

            elif raw_time.endswith("m"):
                minutes = int(raw_time[:-1].strip())

            else:
                minutes = int(raw_time)

        except ValueError:
            await interaction.response.send_message(
                "❌ Nieprawidłowy czas.\n"
                "Przykład: `30 min`",
                ephemeral=True
            )
            return

        if minutes < 30:
            await interaction.response.send_message(
                "❌ Giveaway musi trwać minimum **30 minut**.",
                ephemeral=True
            )
            return

        duration_seconds = minutes * 60

        # -------------------------------------------------
        # LICZBA ZWYCIĘZCÓW
        # -------------------------------------------------

        try:
            winners = int(self.zwyciezcy.value.strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ Liczba zwycięzców musi być liczbą.",
                ephemeral=True
            )
            return

        if winners < 1:
            await interaction.response.send_message(
                "❌ Musi być przynajmniej **1 zwycięzca**.",
                ephemeral=True
            )
            return

        # -------------------------------------------------
        # NAGRODA
        # -------------------------------------------------

        prize = self.nagroda.value.strip()

        if not prize:
            await interaction.response.send_message(
                "❌ Musisz wpisać nagrodę.",
                ephemeral=True
            )
            return

        # -------------------------------------------------
        # WYBÓR ROLI
        # -------------------------------------------------

        await interaction.response.send_message(
            "🏷️ **Wybierz rolę, która ma zostać oznaczona w giveawayu:**",
            view=RoleSelectView(
                duration_seconds,
                winners,
                prize,
                interaction.user.id
            ),
            ephemeral=True
        )


# =========================================================
# WYBÓR ROLI
# =========================================================

class RoleSelect(discord.ui.RoleSelect):

    def __init__(
        self,
        duration_seconds: int,
        winners: int,
        prize: str,
        creator_id: int
    ):
        super().__init__(
            placeholder="Wybierz rolę do oznaczenia...",
            min_values=1,
            max_values=1
        )

        self.duration_seconds = duration_seconds
        self.winners = winners
        self.prize = prize
        self.creator_id = creator_id

    async def callback(self, interaction: discord.Interaction):

        role = self.values[0]

        # Nie pozwalamy oznaczać @everyone
        if role.is_default():
            await interaction.response.send_message(
                "❌ Nie możesz wybrać roli `@everyone`.",
                ephemeral=True
            )
            return

        # Twórca musi być tym samym użytkownikiem
        if interaction.user.id != self.creator_id:
            await interaction.response.send_message(
                "❌ To nie jest Twój formularz.",
                ephemeral=True
            )
            return

        channel = interaction.channel

        if channel is None:
            await interaction.response.send_message(
                "❌ Nie znaleziono kanału.",
                ephemeral=True
            )
            return

        giveaway_id = create_giveaway(
            guild_id=interaction.guild.id,
            channel_id=channel.id,
            creator_id=self.creator_id,
            role_id=role.id,
            prize=self.prize,
            duration_seconds=self.duration_seconds,
            winners_count=self.winners
        )

        ends_at = now_timestamp() + self.duration_seconds

        embed = create_giveaway_embed(
            giveaway_id=giveaway_id,
            prize=self.prize,
            role=role,
            participant_total=0,
            ends_at=ends_at,
            finished=False
        )

        file = None

        if os.path.exists(GIVEAWAY_IMAGE):
            file = discord.File(
                GIVEAWAY_IMAGE,
                filename="giveaway.jpg"
            )

            embed.set_image(
                url="attachment://giveaway.jpg"
            )

        view = GiveawayJoinView(giveaway_id)

        try:

            if file:
                message = await channel.send(
                    content=role.mention,
                    embed=embed,
                    view=view,
                    file=file
                )
            else:
                message = await channel.send(
                    content=role.mention,
                    embed=embed,
                    view=view
                )

            update_giveaway_message(giveaway_id, message.id)

        except discord.Forbidden:

            await interaction.response.send_message(
                "❌ Bot nie ma uprawnień do wysyłania wiadomości na tym kanale.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"✅ **Giveaway został utworzony!**\n"
            f"ID to **{giveaway_id}**",
            ephemeral=True
        )

        asyncio.create_task(
            giveaway_timer(giveaway_id)
        )


class RoleSelectView(discord.ui.View):

    def __init__(
        self,
        duration_seconds: int,
        winners: int,
        prize: str,
        creator_id: int
    ):
        super().__init__(timeout=120)

        self.add_item(
            RoleSelect(
                duration_seconds,
                winners,
                prize,
                creator_id
            )
        )


# =========================================================
# BAZA - GIVEAWAY
# =========================================================

def create_giveaway(
    guild_id,
    channel_id,
    creator_id,
    role_id,
    prize,
    duration_seconds,
    winners_count
):

    connection = db()
    cursor = connection.cursor()

    created_at = now_timestamp()
    ends_at = created_at + duration_seconds

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
            ends_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        guild_id,
        channel_id,
        creator_id,
        role_id,
        prize,
        duration_seconds,
        winners_count,
        created_at,
        ends_at
    ))

    giveaway_id = cursor.lastrowid

    connection.commit()
    connection.close()

    return giveaway_id


def update_giveaway_message(giveaway_id, message_id):

    connection = db()
    cursor = connection.cursor()

    cursor.execute(
        "UPDATE giveaways SET message_id = ? WHERE id = ?",
        (message_id, giveaway_id)
    )

    connection.commit()
    connection.close()


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


# =========================================================
# EMBED GIVEAWAYA
# =========================================================

def create_giveaway_embed(
    giveaway_id,
    prize,
    role,
    participant_total,
    ends_at,
    finished
):

    embed = discord.Embed(
        title=f"🎁 {prize}",
        color=discord.Color.blurple()
    )

    embed.description = (
        "Kliknij 🎉 aby wziąć udział w giveawayu.\n"
    )

    if finished:

        embed.add_field(
            name="Skończył się:",
            value=f"<t:{ends_at}:F>",
            inline=True
        )

    else:

        embed.add_field(
            name="Skończy się za:",
            value=f"<t:{ends_at}:R>",
            inline=True
        )

    embed.add_field(
        name="Ping:",
        value=role.mention,
        inline=True
    )

    embed.add_field(
        name="Osoby które wzięły udział:",
        value=str(participant_total),
        inline=True
    )

    embed.set_footer(
        text=f"Giveaway ID: {giveaway_id}"
    )

    return embed


# =========================================================
# PRZYCISK UDZIAŁU
# =========================================================

class GiveawayJoinButton(discord.ui.Button):

    def __init__(self, giveaway_id):

        super().__init__(
            label="🎉 Weź udział",
            style=discord.ButtonStyle.primary,
            custom_id=f"giveaway_join_{giveaway_id}"
        )

        self.giveaway_id = giveaway_id

    async def callback(self, interaction: discord.Interaction):

        giveaway = get_giveaway(self.giveaway_id)

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

        connection = db()
        cursor = connection.cursor()

        cursor.execute("""
            SELECT 1
            FROM participants
            WHERE giveaway_id = ? AND user_id = ?
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

        await update_giveaway_display(self.giveaway_id)


class GiveawayJoinView(discord.ui.View):

    def __init__(self, giveaway_id):

        super().__init__(
            timeout=None
        )

        self.add_item(
            GiveawayJoinButton(giveaway_id)
        )


# =========================================================
# AKTUALIZACJA GIVEAWAYA
# =========================================================

async def update_giveaway_display(giveaway_id):

    giveaway = get_giveaway(giveaway_id)

    if giveaway is None:
        return

    guild = bot.get_guild(giveaway["guild_id"])

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

    count = participant_count(
        giveaway_id
    )

    embed = create_giveaway_embed(
        giveaway_id=giveaway_id,
        prize=giveaway["prize"],
        role=role,
        participant_total=count,
        ends_at=(
            giveaway["finished_at"]
            if giveaway["finished"]
            else giveaway["ends_at"]
        ),
        finished=bool(giveaway["finished"])
    )

    if giveaway["finished"]:

        view = discord.ui.View(timeout=None)

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
        await message.edit(
            embed=embed,
            view=view
        )
    except Exception:
        pass


# =========================================================
# TIMER GIVEAWAYA
# =========================================================

async def giveaway_timer(giveaway_id):

    giveaway = get_giveaway(
        giveaway_id
    )

    if giveaway is None:
        return

    wait_time = max(
        0,
        giveaway["ends_at"] - now_timestamp()
    )

    await asyncio.sleep(
        wait_time
    )

    # Sprawdzamy jeszcze raz
    giveaway = get_giveaway(
        giveaway_id
    )

    if giveaway is None or giveaway["finished"]:
        return

    await finish_giveaway(
        giveaway_id
    )


# =========================================================
# KONIEC GIVEAWAYA
# =========================================================

async def finish_giveaway(giveaway_id):

    giveaway = get_giveaway(
        giveaway_id
    )

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
    # AKTUALIZUJEMY PIERWSZĄ WIADOMOŚĆ
    # -----------------------------------------------------

    await update_giveaway_display(
        giveaway_id
    )

    # -----------------------------------------------------
    # LOSOWANIE
    # -----------------------------------------------------

    connection = db()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT user_id
        FROM participants
        WHERE giveaway_id = ?
    """, (
        giveaway_id,
    ))

    participants = [
        row["user_id"]
        for row in cursor.fetchall()
    ]

    connection.close()

    # -----------------------------------------------------
    # ZAPOWIEDŹ
    # -----------------------------------------------------

    announcement = await channel.send(
        content=(
            f"{role.mention} **Czy jesteście gotowi na wyniki???**"
        )
    )

    try:
        await announcement.add_reaction("🔥")
    except Exception:
        pass

    # Czekamy minutę
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

        await channel.send(
            embed=discord.Embed(
                description=(
                    f"🎁 **{giveaway['prize']}**\n\n"
                    "❌ Nikt nie wziął udziału w giveawayu."
                ),
                color=discord.Color.blurple()
            )
        )

        return

    # -----------------------------------------------------
    # LOSOWANIE
    # -----------------------------------------------------

    amount = min(
        giveaway["winners_count"],
        len(participants)
    )

    winners = random.SystemRandom().sample(
        participants,
        amount
    )

    mentions = []

    for winner_id in winners:
        mentions.append(
            f"<@{winner_id}>"
        )

    winners_text = ", ".join(
        mentions
    )

    # -----------------------------------------------------
    # WYNIK
    # -----------------------------------------------------

    result_embed = discord.Embed(
        color=discord.Color.blurple()
    )

    result_embed.title = (
        f"🏆 {giveaway['prize']}"
    )

    result_embed.description = (
        f"**Wygrywa:** {winners_text}\n\n"
        "Gratulacje! Zgłoś się na ticket! 🎉"
    )

    await channel.send(
        embed=result_embed
    )


# =========================================================
# /GIVEAWAY
# =========================================================

@tree.command(
    name="giveaway",
    description="Tworzy nowy giveaway",
    guild=discord.Object(id=GUILD_ID)
)
async def giveaway_command(
    interaction: discord.Interaction
):

    if not await owner_only(
        interaction
    ):
        return

    await interaction.response.send_modal(
        GiveawayModal()
    )


# =========================================================
# /ID GIVEAWAY
# =========================================================

id_group = app_commands.Group(
    name="id",
    description="Komendy dotyczące ID giveawayów",
    guild_ids=[GUILD_ID]
)


@id_group.command(
    name="giveaway",
    description="Pokazuje ID aktywnego lub ostatniego giveaway"
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

    cursor.execute("""
        SELECT *
        FROM giveaways
        ORDER BY id DESC
        LIMIT 1
    """)

    giveaway = cursor.fetchone()

    connection.close()

    if giveaway is None:

        await interaction.response.send_message(
            "ℹ️ Nie ma jeszcze żadnego giveawayu.",
            ephemeral=True
        )

        return

    status = (
        "aktywny"
        if not giveaway["finished"]
        else "ostatni zakończony"
    )

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

    count = participant_count(
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

        # Czy konserwacja już działa?
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

        saved = 0

        if guild.me is None:
            connection.close()
            return

        bot_top_role = guild.me.top_role

        for member in guild.members:

            if member.bot:
                continue

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
                    INSERT OR IGNORE INTO maintenance_roles
                    VALUES (?, ?, ?)
                """, (
                    guild.id,
                    member.id,
                    role.id
                ))

            roles_to_remove = [
                role
                for role in member.roles
                if (
                    not role.is_default()
                    and not role.managed
                    and role.id != MAINTENANCE_ROLE_ID
                    and role.id != OWNER_ROLE_ID
                    and role < bot_top_role
                )
            ]

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

        restored_users = set()

        if guild.me is None:
            connection.close()
            return

        bot_top_role = guild.me.top_role

        for row in rows:

            member = guild.get_member(
                row["user_id"]
            )

            role = guild.get_role(
                row["role_id"]
            )

            if member is None or role is None:
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

            if member and maintenance_role:

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
# START
# =========================================================

@bot.event
async def on_ready():

    setup_database()

    guild_object = discord.Object(
        id=GUILD_ID
    )

    await tree.sync(
        guild=guild_object
    )

    print(
        f"Bot zalogowany jako {bot.user}"
    )

    # -----------------------------------------------------
    # ODNAWIANIE AKTYWNYCH GIVEAWAYÓW PO RESTARTCIE
    # -----------------------------------------------------

    connection = db()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id
        FROM giveaways
        WHERE finished = 0
    """)

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
