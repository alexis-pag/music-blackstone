"""
╔══════════════════════════════════════════════════════════════════╗
║           🎵 DISCORD MUSIC BOT Python — Fichier unique          ║
╠══════════════════════════════════════════════════════════════════╣
║  Commandes : !play !skip !stop !queue !pause !resume            ║
║  Support   : YouTube (via yt-dlp)                               ║
║  Multi-serveurs : dict[guild_id, GuildQueue]                    ║
╠══════════════════════════════════════════════════════════════════╣
║  Installation :                                                 ║
║    pip install discord.py[voice] yt-dlp PyNaCl                 ║
║    + FFmpeg installé sur le système                             ║
║      Linux : sudo apt install ffmpeg                            ║
║      macOS : brew install ffmpeg                                ║
║      Windows : https://ffmpeg.org/download.html                 ║
║                                                                 ║
║  Lancement :                                                    ║
║    Configurez DISCORD_TOKEN dans un fichier .env               ║
║    python bot.py                                               ║
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio
import os
import sys
import datetime
import discord
import aiohttp
from discord.ext import commands
import yt_dlp
from dotenv import load_dotenv
from flask import Flask
from threading import Thread

# Charger les variables d'environnement depuis le fichier .env
load_dotenv()

# ─── ENCODAGE UTF-8 POUR WINDOWS ─────────────────────────────────────────────
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

TOKEN  = os.getenv("DISCORD_TOKEN")
PREFIX = "!"

# Options yt-dlp : extraction audio uniquement, meilleure qualité
YTDL_OPTIONS = {
    "format":            "bestaudio/best",
    "noplaylist":        True,
    "quiet":             True,
    "no_warnings":       True,
    "default_search":    "auto",
    "source_address":    "0.0.0.0",
    "nocheckcertificate": True,
}

# Options FFmpeg pour le streaming (reconnect en cas de coupure réseau)
FFMPEG_PATH = "ffmpeg"
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -probesize 32 -analyzeduration 0",
    "options":        "-vn",  # Pas de vidéo
}

# ─── LOGGER ───────────────────────────────────────────────────────────────────

class Logger:
    RESET  = "\x1b[0m"
    COLORS = {"INFO": "\x1b[36m", "WARN": "\x1b[33m", "ERROR": "\x1b[31m"}

    @classmethod
    def _log(cls, level: str, *args):
        ts    = datetime.datetime.now().strftime("%H:%M:%S")
        color = cls.COLORS.get(level, "")
        # On ajoute flush=True pour que Render affiche les logs immédiatement
        print(f"{color}[{ts}] [{level}]{cls.RESET}", *args, flush=True)

    @classmethod
    def info(cls, *a):  cls._log("INFO",  *a)
    @classmethod
    def warn(cls, *a):  cls._log("WARN",  *a)
    @classmethod
    def error(cls, *a): cls._log("ERROR", *a)

log = Logger()

# ─── FILE D'ATTENTE PAR SERVEUR ───────────────────────────────────────────────

class Track:
    """Représente une piste audio."""
    def __init__(self, url: str, title: str, duration: str, requested_by: str, text_channel: discord.TextChannel):
        self.url           = url           # URL du stream audio direct
        self.title         = title
        self.duration      = duration
        self.requested_by  = requested_by
        self.text_channel  = text_channel

    def __repr__(self):
        return f"<Track title={self.title!r} duration={self.duration}>"


class GuildQueue:
    """File d'attente isolée par serveur Discord."""

    def __init__(self, guild_id: int):
        self.guild_id     = guild_id
        self.tracks:  list[Track] = []
        self.current: Track | None = None
        self.voice:   discord.VoiceClient | None = None  # Connexion vocale
        self.is_paused = False
        self.text_channel: discord.TextChannel | None = None

    def add(self, track: Track):
        self.tracks.append(track)

    def next(self) -> Track | None:
        return self.tracks.pop(0) if self.tracks else None

    def clear(self):
        self.tracks.clear()
        self.current = None

    @property
    def is_empty(self) -> bool:
        return not self.current and len(self.tracks) == 0


# Stockage global des files : guild_id → GuildQueue
_queues: dict[int, GuildQueue] = {}

def is_any_playing() -> bool:
    """Vérifie si au moins un serveur joue de la musique."""
    return any(q.voice and q.voice.is_playing() for q in _queues.values())

# --- Mini serveur HTTP pour Render ---
app = Flask("keep_alive")
server_thread = None

@app.route("/")
def home():
    if is_any_playing():
        return "Bot is alive! (Music playing)"
    return "Bot is idle. (No music)"

def run_server():
    # Render utilise souvent le port 10000 par défaut
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def start_keep_alive():
    global server_thread
    if server_thread is None or not server_thread.is_alive():
        log.info("🌐 Démarrage du serveur HTTP de maintien en vie...")
        server_thread = Thread(target=run_server)
        server_thread.daemon = True
        server_thread.start()

def stop_keep_alive():
    global server_thread
    if server_thread and server_thread.is_alive():
        log.info("🌐 Arrêt du maintien en vie demandé (le thread continuera de tourner sur Render).")
        server_thread = None

# Stockage global pour la commande revers : member_id → Task
_revers_tasks: dict[int, asyncio.Task] = {}


def get_queue(guild_id: int) -> GuildQueue | None:
    return _queues.get(guild_id)

def get_or_create(guild_id: int) -> GuildQueue:
    if guild_id not in _queues:
        _queues[guild_id] = GuildQueue(guild_id)
    return _queues[guild_id]

def delete_queue(guild_id: int):
    if guild_id in _queues:
        _queues[guild_id].clear()
        del _queues[guild_id]

# ─── EXTRACTION AUDIO (yt-dlp) ────────────────────────────────────────────────

async def extract_info(url: str) -> dict:
    """Extrait les métadonnées et l'URL du stream via yt-dlp (thread pool)."""
    
    # Options dynamiques : ajouter le fichier cookies s'il existe
    options = YTDL_OPTIONS.copy()
    if os.path.exists("cookies.txt"):
        log.info("🍪 Utilisation de cookies.txt pour YouTube.")
        options["cookiefile"] = "cookies.txt"
    else:
        log.warn("🍪 cookies.txt non trouvé, extraction sans cookies (risque de blocage).")

    ytdl = yt_dlp.YoutubeDL(options)

    loop = asyncio.get_event_loop()
    # yt-dlp est synchrone → exécuter dans un thread séparé
    data = await loop.run_in_executor(
        None,
        lambda: ytdl.extract_info(url, download=False)
    )

    # Si c'est une playlist, prendre la première entrée
    if "entries" in data:
        data = data["entries"][0]

    return data


def format_duration(seconds: int | None) -> str:
    """Formate une durée en secondes → MM:SS ou HH:MM:SS."""
    if not seconds:
        return "??:??"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

# ─── LECTEUR AUDIO ────────────────────────────────────────────────────────────

async def play_next(queue: GuildQueue):
    """Lit la prochaine piste de la file. Appelé automatiquement après chaque piste."""

    track = queue.next()
    if not track:
        # File vide → message + déconnexion différée
        # On utilise le dernier canal connu de la file ou un canal par défaut
        if queue.text_channel:
            await queue.text_channel.send(
                "✅ File terminée. Déconnexion dans 30 secondes..."
            )
        await asyncio.sleep(30)
        q = get_queue(queue.guild_id)
        if q and q.is_empty and q.voice:
            await q.voice.disconnect()
            delete_queue(queue.guild_id)
        return

    queue.current = track

    try:
        log.info(f'▶ "{track.title}" [{queue.guild_id}]')
        log.info(f"🔗 URL stream : {track.url[:50]}...") # Log l'URL pour debug
        
        # Créer la source audio FFmpeg depuis l'URL du stream
        source = discord.FFmpegPCMAudio(track.url, executable=FFMPEG_PATH, **FFMPEG_OPTIONS)
        # PCMVolumeTransformer permet de contrôler le volume
        source = discord.PCMVolumeTransformer(source, volume=0.5)

        def after_play(error):
            """Callback appelé par discord.py à la fin de la piste."""
            if error:
                log.error(f"Erreur lecture : {error}")
            # Planifier la piste suivante dans la boucle asyncio
            asyncio.run_coroutine_threadsafe(
                _after_track(queue, error, track.text_channel),
                queue.voice.loop
            )

        queue.voice.play(source, after=after_play)

        if track.text_channel:
            await track.text_channel.send(
                f"▶️ Lecture de **{track.title}** ({track.duration})\n"
                f"› Demandé par {track.requested_by}"
            )

    except Exception as e:
        log.error(f'Erreur lecture "{track.title}" : {e}')
        if track.text_channel:
            await track.text_channel.send(
                f"❌ Impossible de lire **{track.title}** : {e}"
            )
        # Passer à la suivante automatiquement
        await play_next(queue)


async def _after_track(queue: GuildQueue, error, text_channel: discord.TextChannel):
    """Transition entre deux pistes."""
    queue.current  = None
    queue.is_paused = False

    if error:
        log.error(f"Erreur après lecture [{queue.guild_id}] : {error}")
        if text_channel:
            await text_channel.send(f"❌ Erreur de lecture : {error}")

    await play_next(queue)


async def add_to_queue(ctx: commands.Context, url: str):
    """Extrait les infos, ajoute à la file et démarre si besoin."""

    queue = get_or_create(ctx.guild.id)

    # ── Rejoindre le salon vocal si pas déjà connecté ─────────────────────
    if not queue.voice or not queue.voice.is_connected():
        try:
            log.info(f"🎤 Tentative de connexion au salon vocal...")
            voice_channel = ctx.author.voice.channel
            # Augmenter le timeout à 120s pour Render
            queue.voice = await voice_channel.connect(timeout=120.0, reconnect=True)
            queue.text_channel = ctx.channel
            log.info(f"✅ Connecté → {voice_channel.name}")
        except asyncio.TimeoutError:
            log.error("❌ Timeout lors de la connexion au salon vocal.")
            return await ctx.reply("❌ Délai d'attente dépassé pour rejoindre le salon vocal. Réessayez.")
        except Exception as e:
            log.error(f"❌ Erreur connexion vocale : {e}")
            return await ctx.reply(f"❌ Impossible de rejoindre le salon : {e}")

    # ── Extraire les métadonnées ───────────────────────────────────────────
    try:
        log.info(f"🔎 Extraction des infos YouTube : {url}")
        data  = await extract_info(url)
    except Exception as e:
        log.error(f"❌ Erreur extraction YouTube : {e}")
        return await ctx.reply(f"❌ Impossible de récupérer la musique : {e}")
    track = Track(
        url           = data.get("url") or data.get("webpage_url", url),
        title         = data.get("title", "Titre inconnu"),
        duration      = format_duration(data.get("duration")),
        requested_by  = str(ctx.author),
        text_channel  = ctx.channel,
    )

    queue.add(track)

    # ── Démarrer si rien ne joue, sinon confirmer l'ajout ─────────────────
    if not queue.voice.is_playing() and not queue.voice.is_paused():
        await play_next(queue)
    else:
        await ctx.reply(
            f"✅ **{track.title}** ajouté en position #{len(queue.tracks)}"
        )

# ─── BOT ET COMMANDES ─────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True   # Obligatoire pour lire le contenu des messages
intents.voice_states    = True    # Obligatoire pour les salons vocaux

bot = commands.Bot(command_prefix=PREFIX, intents=intents)


def can_use_music():
    """Vérifie si l'utilisateur est Admin OU a le rôle 'droit a la musique'."""
    async def predicate(ctx):
        if ctx.author.guild_permissions.administrator:
            return True
        role = discord.utils.get(ctx.author.roles, name="droit a la musique")
        if role:
            return True
        raise commands.CheckFailure("❌ Vous devez être **Administrateur** ou avoir le rôle **'droit a la musique'**.")
    return commands.check(predicate)


async def self_ping():
    """Tâche de fond pour pinguer le serveur lui-même et éviter la veille sur Render Free."""
    url = os.getenv("RENDER_EXTERNAL_URL")
    if not url:
        log.info("🌐 RENDER_EXTERNAL_URL non configuré, saut du self-ping.")
        return

    log.info(f"🌐 Activation du self-ping pour {url}")
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(url) as response:
                    if response.status == 200:
                        log.info(f"🌐 Self-ping réussi ({response.status})")
                    else:
                        log.warn(f"🌐 Self-ping échec ({response.status})")
            except Exception as e:
                log.error(f"🌐 Erreur lors du self-ping : {e}")
            
            # Attendre 10 minutes (600 secondes) avant le prochain ping
            await asyncio.sleep(600)


@bot.event
async def on_ready():
    log.info(f"✅ Connecté en tant que {bot.user} (ID: {bot.user.id})")
    log.info(f"📡 {len(bot.guilds)} serveur(s)")
    
    # Démarrer la tâche de self-ping
    bot.loop.create_task(self_ping())

    # Vérifier si l'encodeur Opus est chargé
    if not discord.opus.is_loaded():
        log.warn("🔊 Opus n'est pas chargé nativement, tentative de chargement...")
        try:
            discord.opus.load_opus() # Cela peut échouer selon l'OS
        except Exception as e:
            log.error(f"❌ Impossible de charger Opus manuellement : {e}")

    # Vérification de FFmpeg
    try:
        import subprocess
        res = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        if res.returncode == 0:
            log.info("🎞️ FFmpeg détecté avec succès !")
        else:
            log.error(f"❌ FFmpeg détecté mais erreur : {res.stderr[:100]}")
    except FileNotFoundError:
        log.error("❌ FFmpeg n'est PAS installé sur ce serveur ! La lecture échouera.")

    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.listening, name="!play <url>")
    )

@bot.event
async def on_voice_state_update(member, before, after):
    """Gère le nettoyage si le bot se retrouve seul dans un salon."""
    if member.bot: return

    # Optionnel : déconnexion automatique si tout le monde quitte
    # Mais on garde simple ici.
    pass


# ── !join [channel_name] ──────────────────────────────────────────────────────
@bot.command(name="join", help="Rejoint votre salon vocal ou un salon spécifié")
@can_use_music()
async def join(ctx: commands.Context, *, channel_name: str = None):
    target_channel = None

    if channel_name:
        # Chercher le salon par nom (insensible à la casse)
        target_channel = discord.utils.get(
            ctx.guild.voice_channels, 
            name=channel_name
        )
        if not target_channel:
            return await ctx.reply(f"❌ Salon vocal `{channel_name}` introuvable.")
    else:
        # Si aucun nom, rejoindre le salon de l'utilisateur
        if not ctx.author.voice:
            return await ctx.reply("❌ Rejoignez un salon vocal d'abord ou spécifiez un nom !")
        target_channel = ctx.author.voice.channel

    queue = get_or_create(ctx.guild.id)

    if queue.voice and queue.voice.is_connected():
        await queue.voice.move_to(target_channel)
    else:
        queue.voice = await target_channel.connect()
        queue.text_channel = ctx.channel

    await ctx.reply(f"✅ Connecté à **{target_channel.name}**")


# ── !play <url> ───────────────────────────────────────────────────────────────
@bot.command(name="play", help="Joue de la musique depuis une URL YouTube")
@can_use_music()
async def play(ctx: commands.Context, *, url: str = None):
    log.info(f"▶ Commande !play reçue pour : {url}")
    if not url:
        return await ctx.reply("❌ Usage : `!play <url_youtube>`")

    if not ctx.author.voice:
        return await ctx.reply("❌ Rejoignez un salon vocal d'abord !")

    perms = ctx.author.voice.channel.permissions_for(ctx.guild.me)
    if not perms.connect or not perms.speak:
        return await ctx.reply("❌ Permissions insuffisantes sur ce salon vocal.")

    loading = await ctx.reply("🔍 Chargement...")
    try:
        await add_to_queue(ctx, url)
    except Exception as e:
        await ctx.reply(f"❌ {e}")
    finally:
        await loading.delete()


# ── !skip ─────────────────────────────────────────────────────────────────────
@bot.command(name="skip", help="Passe à la piste suivante")
@can_use_music()
async def skip(ctx: commands.Context):
    queue = get_queue(ctx.guild.id)

    if not queue or not queue.current:
        return await ctx.reply("❌ Aucune musique en cours.")
    if not ctx.author.voice:
        return await ctx.reply("❌ Rejoignez le salon vocal d'abord.")

    title = queue.current.title
    queue.voice.stop()  # → déclenche after_play → _after_track → play_next

    msg = (f"⏭️ **{title}** passée → lecture de la suivante."
           if queue.tracks else
           f"⏭️ **{title}** passée. Plus de pistes en attente.")
    await ctx.reply(msg)


# ── !stop ─────────────────────────────────────────────────────────────────────
@bot.command(name="stop", help="Arrête la musique et quitte le salon vocal")
@can_use_music()
async def stop(ctx: commands.Context):
    queue = get_queue(ctx.guild.id)

    if not queue or queue.is_empty:
        return await ctx.reply("❌ Aucune musique en cours.")
    if not ctx.author.voice:
        return await ctx.reply("❌ Rejoignez le salon vocal d'abord.")

    queue.clear()
    if queue.voice:
        queue.voice.stop()
        await queue.voice.disconnect()
    delete_queue(ctx.guild.id)

    await ctx.reply("⏹️ Lecture arrêtée, file vidée, bot déconnecté.")


# ── !end ──────────────────────────────────────────────────────────────────────
@bot.command(name="end", help="Déconnecte et éteint complètement le bot")
@can_use_music()
async def end(ctx: commands.Context):
    log.warn(f"🛑 Arrêt demandé par {ctx.author}")
    await ctx.reply("👋 Déconnexion et extinction du bot...")

    # Arrêter les tâches de revers
    for task in _revers_tasks.values():
        task.cancel()
    _revers_tasks.clear()

    # Déconnecter de tous les salons vocaux
    for queue in list(_queues.values()):
        if queue.voice and queue.voice.is_connected():
            await queue.voice.disconnect()
        queue.clear()
    _queues.clear()

    await bot.close()


# ── !restart ──────────────────────────────────────────────────────────────────
@bot.command(name="restart", help="Redémarre complètement le bot")
@can_use_music()
async def restart(ctx: commands.Context):
    log.warn(f"🔄 Redémarrage demandé par {ctx.author}")
    await ctx.reply("🔄 Redémarrage en cours...")

    # Arrêter les tâches de revers
    for task in _revers_tasks.values():
        task.cancel()
    _revers_tasks.clear()

    # Déconnecter de tous les salons vocaux
    for queue in list(_queues.values()):
        if queue.voice and queue.voice.is_connected():
            await queue.voice.disconnect()
        queue.clear()
    _queues.clear()

    await bot.close()

    # Redémarrer le processus
    os.execv(sys.executable, ["python"] + sys.argv)


# ── !queue ────────────────────────────────────────────────────────────────────
@bot.command(name="queue", aliases=["q"], help="Affiche la file d'attente")
@can_use_music()
async def show_queue(ctx: commands.Context):
    queue = get_queue(ctx.guild.id)

    if not queue or queue.is_empty:
        return await ctx.reply("📭 La file d'attente est vide.")

    lines = []

    if queue.current:
        status = "⏸️ En pause" if queue.is_paused else "▶️ En lecture"
        lines.append(f"**{status} :**")
        lines.append(
            f"`1.` **{queue.current.title}** ({queue.current.duration})"
            f" — {queue.current.requested_by}"
        )

    if queue.tracks:
        lines.append("\n**File d'attente :**")
        MAX = 10
        for i, t in enumerate(queue.tracks[:MAX]):
            lines.append(
                f"`{i + 2}.` **{t.title}** ({t.duration}) — {t.requested_by}"
            )
        if len(queue.tracks) > MAX:
            lines.append(f"\n... et **{len(queue.tracks) - MAX}** piste(s) de plus.")

    total = len(queue.tracks) + (1 if queue.current else 0)
    lines.append(f"\n📋 **Total : {total} piste(s)**")

    content = "\n".join(lines)
    await ctx.reply(content[:1900] + ("…" if len(content) > 1900 else ""))


# ── !pause ────────────────────────────────────────────────────────────────────
@bot.command(name="pause", help="Met la musique en pause")
@can_use_music()
async def pause(ctx: commands.Context):
    queue = get_queue(ctx.guild.id)

    if not queue or not queue.current:
        return await ctx.reply("❌ Aucune musique en cours.")
    if not queue.voice.is_playing():
        return await ctx.reply("❌ La musique est déjà en pause.")

    queue.voice.pause()
    queue.is_paused = True
    await ctx.reply(
        f"⏸️ **{queue.current.title}** mis en pause. `!resume` pour reprendre."
    )


# ── !resume ───────────────────────────────────────────────────────────────────
@bot.command(name="resume", help="Reprend la musique après une pause")
@can_use_music()
async def resume(ctx: commands.Context):
    queue = get_queue(ctx.guild.id)

    if not queue or not queue.current:
        return await ctx.reply("❌ Aucune musique en cours.")
    if not queue.voice.is_paused():
        return await ctx.reply("❌ La musique n'est pas en pause.")

    queue.voice.resume()
    queue.is_paused = False
    await ctx.reply(f"▶️ Reprise de **{queue.current.title}**.")


# ─── MODÉRATION ──────────────────────────────────────────────────────────────

@bot.command(name="kick", help="Expulse un membre du serveur")
@commands.has_permissions(kick_members=True)
async def kick(ctx: commands.Context, member: discord.Member, *, reason: str = "Aucune raison fournie"):
    await member.kick(reason=reason)
    await ctx.reply(f"👢 **{member}** a été expulsé. Raison : {reason}")

@bot.command(name="ban", help="Bannit un membre du serveur")
@commands.has_permissions(ban_members=True)
async def ban(ctx: commands.Context, member: discord.Member, *, reason: str = "Aucune raison fournie"):
    await member.ban(reason=reason)
    await ctx.reply(f"🔨 **{member}** a été banni. Raison : {reason}")

@bot.command(name="mute", help="Réduit un membre au silence (timeout)")
@commands.has_permissions(moderate_members=True)
async def mute(ctx: commands.Context, member: discord.Member, minutes: int = 10, *, reason: str = "Aucune raison fournie"):
    duration = datetime.timedelta(minutes=minutes)
    await member.timeout(duration, reason=reason)
    await ctx.reply(f"🔇 **{member}** a été mute pendant {minutes} minutes. Raison : {reason}")

@bot.command(name="unmute", help="Retire le silence (timeout) d'un membre")
@commands.has_permissions(moderate_members=True)
async def unmute(ctx: commands.Context, member: discord.Member, *, reason: str = "Aucune raison fournie"):
    await member.timeout(None, reason=reason)
    await ctx.reply(f"🔊 **{member}** n'est plus mute.")

@bot.command(name="vmute", help="Mute un membre en vocal")
@commands.has_permissions(mute_members=True)
async def vmute(ctx: commands.Context, member: discord.Member):
    if not member.voice:
        return await ctx.reply("❌ Ce membre n'est pas en vocal.")
    await member.edit(mute=True)
    await ctx.reply(f"🔇 **{member}** a été mute en vocal.")

@bot.command(name="vunmute", help="Démute un membre en vocal")
@commands.has_permissions(mute_members=True)
async def vunmute(ctx: commands.Context, member: discord.Member):
    if not member.voice:
        return await ctx.reply("❌ Ce membre n'est pas en vocal.")
    await member.edit(mute=False)
    await ctx.reply(f"🔊 **{member}** a été démute en vocal.")


@bot.group(name="revers", help="Fait sauter un membre entre deux salons vocaux en boucle", invoke_without_command=True)
@commands.has_permissions(administrator=True)
async def revers(ctx: commands.Context, member: discord.Member):
    if not member.voice or not member.voice.channel:
        return await ctx.reply("❌ Ce membre doit être dans un salon vocal.")

    if member.id in _revers_tasks:
        return await ctx.reply(f"❌ Un revers est déjà en cours pour **{member.display_name}**.")

    # Trouver un autre salon pour le switch
    voice_channels = ctx.guild.voice_channels
    if len(voice_channels) < 2:
        return await ctx.reply("❌ Il faut au moins 2 salons vocaux sur le serveur.")

    current_channel = member.voice.channel
    other_channel = next((c for c in voice_channels if c != current_channel), None)

    async def do_revers():
        try:
            channels = [current_channel, other_channel]
            idx = 1
            while True:
                if not member.voice: break
                await member.move_to(channels[idx])
                idx = 1 - idx # Switch 0 <-> 1
                await asyncio.sleep(1.5)
        except Exception as e:
            log.error(f"Erreur revers pour {member}: {e}")
        finally:
            _revers_tasks.pop(member.id, None)

    task = asyncio.create_task(do_revers())
    _revers_tasks[member.id] = task
    await ctx.reply(f"🌀 **Revers lancé** pour **{member.display_name}** !")


@revers.command(name="fin", help="Arrête le revers pour un membre")
@commands.has_permissions(administrator=True)
async def revers_fin(ctx: commands.Context, member: discord.Member):
    task = _revers_tasks.get(member.id)
    if not task:
        return await ctx.reply(f"❌ Aucun revers en cours pour **{member.display_name}**.")

    task.cancel()
    _revers_tasks.pop(member.id, None)
    await ctx.reply(f"✅ **Revers terminé** pour **{member.display_name}**.")


# ─── GESTION DES ERREURS ──────────────────────────────────────────────────────

@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandNotFound):
        return  # Ignorer les commandes inconnues
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.reply(f"❌ Argument manquant. `!help {ctx.command}`")
    if isinstance(error, (commands.MissingPermissions, commands.CheckFailure)):
        return await ctx.reply(str(error))
    log.error(f"Erreur commande !{ctx.command} : {error}")
    await ctx.reply(f"❌ Erreur inattendue : {error}")


# ─── LANCEMENT ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if TOKEN == "VOTRE_TOKEN_ICI" or not TOKEN:
        log.warn("⚠️  Token non configuré !")
        log.warn("   Définissez DISCORD_TOKEN ou modifiez la variable TOKEN ligne ~30.")
        sys.exit(1)

    # Démarrer le serveur HTTP uniquement sur Render
    if os.getenv("RENDER"):
        start_keep_alive()
    else:
        log.info("🏠 Lancement local (pas de serveur HTTP de maintien).")

    try:
        bot.run(TOKEN, log_handler=None)
    except discord.LoginFailure:
        log.error("❌ Token invalide ! Vérifiez votre configuration.")
        sys.exit(1)
    except Exception as e:
        log.error(f"❌ Erreur lors du lancement : {e}")
        sys.exit(1)
