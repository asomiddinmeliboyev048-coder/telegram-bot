import asyncio
import os
import subprocess
import logging
import tempfile
import time
from pathlib import Path
from functools import wraps

import yt_dlp
import edge_tts
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from flask import Flask
from dotenv import load_dotenv
from telebot.async_telebot import AsyncTeleBot
from telebot import types

# ================= LOAD .ENV =================
load_dotenv()

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required!")

# Spotify API credentials
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

# Initialize Spotify client if credentials available
spotify_client = None
if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    try:
        client_credentials_manager = SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET
        )
        spotify_client = spotipy.Spotify(client_credentials_manager=client_credentials_manager)
        logger.info("Spotify client initialized successfully")
    except Exception as e:
        logger.warning(f"Failed to initialize Spotify client: {e}")

# Async bot instance
bot = AsyncTeleBot(BOT_TOKEN)

# Semaphore for limiting concurrent heavy tasks
video_semaphore = asyncio.Semaphore(3)  # Reduced for queue-based processing
music_semaphore = asyncio.Semaphore(10)
circle_semaphore = asyncio.Semaphore(2)  # Separate for circle videos

TEMP_DIR = tempfile.gettempdir()

CHANNEL = -1003877967882
OWNER_ID = 7171330738

# User state storage
user_state = {}
user_voice = {}

# Track active user tasks for cleanup
active_tasks = {}

# Video processing queue for sequential but fast processing
video_queue = asyncio.Queue()

# Background cleanup task
async def background_cleanup(file_path, delay=5):
    """Cleanup files in background after delay seconds"""
    await asyncio.sleep(delay)
    safe_remove(file_path)

# ================= TEMP FILE CLEANUP =================
def cleanup_temp_files(cid):
    """Clean up temporary files for a user"""
    patterns = [f"{cid}.*", f"{cid}_*"]
    for pattern in patterns:
        for file in Path(TEMP_DIR).glob(pattern):
            try:
                file.unlink()
                logger.info(f"Cleaned up: {file}")
            except Exception as e:
                logger.error(f"Error cleaning up {file}: {e}")

def safe_remove(filepath):
    """Safely remove a file if it exists"""
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
            logger.info(f"Removed: {filepath}")
    except Exception as e:
        logger.error(f"Error removing {filepath}: {e}")

# ================= FLASK (RENDER FIX) =================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running"

def run_web():
    import threading
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

import threading
threading.Thread(target=run_web, daemon=True).start()
# =====================================================

# ================= FORCE JOIN MIDDLEWARE =================
async def check_subscription(user_id):
    """Check if user is subscribed to channel"""
    if user_id == OWNER_ID:
        return True
    try:
        member = await bot.get_chat_member(CHANNEL, user_id)
        return member.status in ["member", "creator", "administrator"]
    except Exception as e:
        logger.error(f"Subscription check error: {e}")
        return False

def require_subscription(handler):
    """Decorator to require channel subscription"""
    @wraps(handler)
    async def wrapper(message, *args, **kwargs):
        user_id = message.from_user.id
        cid = message.chat.id

        # Check subscription
        is_subscribed = await check_subscription(user_id)

        if not is_subscribed:
            # Show subscription required message
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("📢 Obuna bo'lish", url="https://t.me/meliboyevdev"))
            kb.add(types.InlineKeyboardButton("✅ Tekshirish", callback_data="check"))
            await bot.send_message(cid, "❗️ Kanalga a'zo bo'ling:", reply_markup=kb)
            return

        # User is subscribed, proceed with handler
        return await handler(message, *args, **kwargs)
    return wrapper

# Subscription check callback
@bot.callback_query_handler(func=lambda c: c.data == "check")
async def check_callback(call):
    """Handle subscription check callback"""
    user_id = call.from_user.id
    cid = call.message.chat.id

    is_subscribed = await check_subscription(user_id)

    if is_subscribed:
        await bot.edit_message_text("✅ Obuna tasdiqlandi!", cid, call.message.message_id)
        await bot.send_message(cid, "🔥 BOTGA XUSH KELIBSIZ", reply_markup=main_menu())
    else:
        await bot.answer_callback_query(call.id, "❌ Avval kanalga obuna bo'ling!", show_alert=True)

# ================= USERS =================
def save_user(uid):
    try:
        if not os.path.exists("users.txt"):
            open("users.txt", "w").close()
        users = open("users.txt").read().splitlines()
        if str(uid) not in users:
            with open("users.txt", "a") as f:
                f.write(str(uid) + "\n")
    except Exception as e:
        logger.error(f"Error saving user {uid}: {e}")

def get_users():
    try:
        if not os.path.exists("users.txt"):
            return []
        return open("users.txt").read().splitlines()
    except Exception as e:
        logger.error(f"Error reading users: {e}")
        return []

# ================= MENUS =================
def main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("🎤 Text → Voice", "🎬 Video → MP3")
    kb.add("🎧 Search Music", "🔵 Circle Video")
    return kb

def voice_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("👨 Erkak ovoz", "👩 Ayol ovoz")
    kb.add("🤡 Kulgili ovoz")
    kb.add("🔙 Orqaga")
    return kb

def admin_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📢 Broadcast","📊 Statistika")
    kb.add("📣 Auto Post")
    kb.add("🔙 Orqaga")
    return kb

# ================= START =================
@bot.message_handler(commands=['start'])
async def start(m):
    """Start command - with real-time subscription check"""
    cid = m.chat.id
    user_id = m.from_user.id

    try:
        # Real-time subscription check (no caching)
        is_subscribed = await check_subscription(user_id)

        if not is_subscribed:
            # Not subscribed - show subscription prompt
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("📢 Obuna bo'lish", url="https://t.me/meliboyevdev"))
            kb.add(types.InlineKeyboardButton("✅ Tekshirish", callback_data="check"))
            await bot.send_message(cid, "❗️ Kanalga a'zo bo'ling:", reply_markup=kb)
            return

        # Subscribed - show main menu immediately
        save_user(cid)
        user_state[cid] = None
        await bot.send_message(cid, "🔥 BOTGA XUSH KELIBSIZ\n\n"
                                  "🎤 Matnni ovozga o'girish\n"
                                  "🎬 Video dan MP3 ajratish\n"
                                  "🎧 YouTube dan musiqa yuklash\n"
                                  "🔵 Yumaloq video yaratish",
                               reply_markup=main_menu())
    except Exception as e:
        logger.error(f"Start error: {e}")

# ================= ADMIN =================
@bot.message_handler(commands=['admin'])
async def admin(m):
    """Admin panel"""
    try:
        if m.from_user.id == OWNER_ID:
            await bot.send_message(m.chat.id, "⚙️ Admin panel", reply_markup=admin_menu())
    except Exception as e:
        logger.error(f"Admin error: {e}")

AUTO_POST_TEXT = None

# ================= TEXT HANDLER =================
@bot.message_handler(content_types=['text'])
async def text_handler(m):
    """Main text handler with global subscription check"""
    cid = m.chat.id
    txt = m.text
    user_id = m.from_user.id
    state = user_state.get(cid)

    # Global subscription check
    is_subscribed = await check_subscription(user_id)
    if not is_subscribed:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📢 Obuna bo'lish", url="https://t.me/meliboyevdev"))
        kb.add(types.InlineKeyboardButton("✅ Tekshirish", callback_data="check"))
        await bot.send_message(cid, "❗️ Kanalga a'zo bo'ling:", reply_markup=kb)
        return

    try:
        if txt == "🔙 Orqaga":
            user_state[cid] = None
            await bot.send_message(cid, "📋 Asosiy menu", reply_markup=main_menu())
            return

        if txt == "🎤 Text → Voice":
            user_state[cid] = "choose_voice"
            await bot.send_message(cid, "👇 Ovoz tanlang:", reply_markup=voice_menu())
            return

        if txt in ["👨 Erkak ovoz", "👩 Ayol ovoz", "🤡 Kulgili ovoz"]:
            if "Kulgili" in txt:
                user_voice[cid] = "funny"
            else:
                user_voice[cid] = "male" if "Erkak" in txt else "female"
            user_state[cid] = "tts"
            await bot.send_message(cid, "✍️ Matn yuboring (o'zbek tilida):")
            return

        if state == "tts":
            await handle_tts(m)
            return

        if txt == "📊 Statistika" and user_id == OWNER_ID:
            await bot.send_message(cid, f"👥 Foydalanuvchilar: {len(get_users())}")
            return

        if txt == "📢 Broadcast" and user_id == OWNER_ID:
            user_state[cid] = "broadcast"
            await bot.send_message(cid, "📤 Yuboriladigan postni yuboring:")
            return

        if txt == "📣 Auto Post" and user_id == OWNER_ID:
            user_state[cid] = "autopost"
            await bot.send_message(cid, "📤 Auto post matnini yuboring:")
            return

        if state == "broadcast":
            await handle_broadcast(m)
            return

        if state == "autopost":
            await handle_autopost(m)
            return

        if txt == "🎬 Video → MP3":
            user_state[cid] = "mp3"
            await bot.send_message(cid, "🎥 Video yuboring (MP3 ga aylantiraman):")
            return

        if txt == "🎧 Search Music":
            user_state[cid] = "music"
            await bot.send_message(cid, "🎵 Qo'shiq nomi yoki ijrochi yozing:\n"
                                          "(Masalan: 'Eminem Lose Yourself')")
            return

        if txt == "🔵 Circle Video":
            user_state[cid] = "circle"
            await bot.send_message(cid, "🎥 Video yuboring (yumaloq video qilaman):")
            return

        if state == "music":
            await handle_music_search(m)
            return

    except Exception as e:
        logger.error(f"Text handler error for user {cid}: {e}")
        try:
            await bot.send_message(cid, f"❌ Xatolik: {str(e)[:100]}", reply_markup=main_menu())
        except Exception:
            pass

# ================= HELPER FUNCTIONS =================
async def handle_tts(m):
    """Text to speech handler with funny voice support"""
    cid = m.chat.id
    txt = m.text
    voice_type = user_voice.get(cid, "female")
    input_path = None
    output_path = None

    # Telegram caption limit is 1024 characters
    MAX_CAPTION = 900  # Leave room for ad text

    try:
        if voice_type == "funny":
            # Kulgili ovoz - using female voice then pitch shift
            voice = "uz-UZ-MadinaNeural"
        else:
            voice = "uz-UZ-SardorNeural" if voice_type == "male" else "uz-UZ-MadinaNeural"

        input_path = os.path.join(TEMP_DIR, f"{cid}_tts_input.mp3")
        output_path = os.path.join(TEMP_DIR, f"{cid}_tts_output.mp3")

        # Truncate text if too long for caption
        caption_text = txt if len(txt) <= 100 else txt[:100] + "..."

        # Generate voice
        msg = await bot.send_message(cid, "🎙️ Ovoz yaratilmoqda...")
        communicate = edge_tts.Communicate(text=txt, voice=voice)
        await communicate.save(input_path)

        # If funny voice, apply pitch shift using ffmpeg
        if voice_type == "funny":
            await bot.edit_message_text("🤡 Kulgili ovoz effekti qo'llanilmoqda...", cid, msg.message_id)

            # Try rubberband first (best quality pitch shift without speed change)
            # rubberband=pitch=1.5 raises pitch by 1.5x, keeps original speed
            cmd_rubberband = [
                "ffmpeg", "-y",
                "-i", input_path,
                "-af", "rubberband=pitch=1.5",
                "-ar", "44100",
                "-ac", "1",
                output_path
            ]

            # Fallback: asetrate+atempo method (pitch up 1.4x, tempo compensated)
            # asetrate increases sample rate (pitch & speed up)
            # atempo slows down to compensate (1/1.4 = 0.714)
            cmd_asetrate = [
                "ffmpeg", "-y",
                "-i", input_path,
                "-af", "asetrate=44100*1.4,atempo=1/1.4",
                "-ar", "44100",
                "-ac", "1",
                output_path
            ]

            file_to_send = input_path  # Default fallback

            try:
                loop = asyncio.get_event_loop()

                # First try rubberband (better quality)
                result = await loop.run_in_executor(
                    None,
                    lambda: subprocess.run(cmd_rubberband, capture_output=True, text=True, timeout=30)
                )

                if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    file_to_send = output_path
                    logger.info(f"Funny voice (rubberband) applied for user {cid}")
                else:
                    # Rubberband failed, try asetrate+atempo
                    logger.info(f"Rubberband not available, trying asetrate method for user {cid}")
                    result2 = await loop.run_in_executor(
                        None,
                        lambda: subprocess.run(cmd_asetrate, capture_output=True, text=True, timeout=30)
                    )

                    if result2.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                        file_to_send = output_path
                        logger.info(f"Funny voice (asetrate) applied for user {cid}")
                    else:
                        logger.warning(f"All funny voice effects failed, using original voice")
            except Exception as e:
                logger.error(f"Funny voice processing error: {e}")
                file_to_send = input_path
        else:
            file_to_send = input_path

        # Send voice with truncated caption
        await bot.edit_message_text("📤 Yuborilmoqda...", cid, msg.message_id)

        # Build caption (keep it short to avoid "caption too long" error)
        voice_emoji = "🤡" if voice_type == "funny" else "🎙️"
        caption = f"{voice_emoji} {caption_text}\n✅ @foyda1ii_bot"

        # Ensure caption doesn't exceed limit
        if len(caption) > MAX_CAPTION:
            caption = f"{voice_emoji} {caption_text[:50]}...\n✅ @foyda1ii_bot"

        with open(file_to_send, "rb") as f:
            await bot.send_voice(cid, f, caption=caption[:MAX_CAPTION])

        await bot.delete_message(cid, msg.message_id)
        user_state[cid] = None

    except Exception as e:
        logger.error(f"TTS error: {e}")
        await bot.send_message(cid, f"❌ Xatolik: {str(e)[:100]}", reply_markup=main_menu())
    finally:
        safe_remove(input_path)
        safe_remove(output_path)

async def handle_broadcast(m):
    """Broadcast message to all users"""
    cid = m.chat.id
    users = get_users()
    success = 0
    failed = 0

    try:
        await bot.send_message(cid, f"📤 Yuborilmoqda... ({len(users)} foydalanuvchi)")

        for u in users:
            try:
                await bot.copy_message(u, cid, m.message_id)
                success += 1
                await asyncio.sleep(0.05)  # Rate limiting
            except Exception as e:
                failed += 1
                logger.error(f"Broadcast failed for {u}: {e}")

        await bot.send_message(cid, f"✅ Yuborildi: {success}\n❌ Xatolik: {failed}")
        user_state[cid] = None

    except Exception as e:
        logger.error(f"Broadcast error: {e}")
        await bot.send_message(cid, f"❌ Xatolik: {str(e)[:100]}", reply_markup=main_menu())

async def handle_autopost(m):
    """Set auto post text"""
    cid = m.chat.id
    global AUTO_POST_TEXT

    try:
        AUTO_POST_TEXT = m.text
        await bot.copy_message(CHANNEL, cid, m.message_id)
        await bot.send_message(cid, "✅ Auto post saqlandi!")
    except Exception as e:
        logger.error(f"Autopost error: {e}")
        await bot.send_message(cid, f"❌ Kanalga yuborishda xatolik: {str(e)[:100]}")
    user_state[cid] = None

async def update_download_progress(cid, msg_id, progress_data, stop_event):
    """Update progress message every 2 seconds"""
    last_percent = -1
    while not stop_event.is_set():
        try:
            percent = progress_data.get('percent', 0)
            if percent != last_percent and percent < 100:
                last_percent = percent
                status_emoji = "⬇️" if percent < 50 else "🎵"
                try:
                    await bot.edit_message_text(
                        f"{status_emoji} Yuklanmoqda: {percent}%",
                        cid, msg_id
                    )
                except Exception:
                    pass  # Ignore edit errors
            await asyncio.sleep(2)
        except Exception:
            break

async def search_spotify(query):
    """Search Spotify for track with accurate metadata"""
    if not spotify_client:
        return None

    try:
        # Search for track
        results = spotify_client.search(q=query, type='track', limit=1)

        if results and 'tracks' in results and results['tracks']['items']:
            track = results['tracks']['items'][0]

            # Extract accurate metadata
            metadata = {
                'name': track['name'],
                'artist': track['artists'][0]['name'],
                'artists': [a['name'] for a in track['artists']],
                'album': track['album']['name'],
                'duration_ms': track['duration_ms'],
                'duration': track['duration_ms'] // 1000,
                'preview_url': track.get('preview_url'),
                'external_url': track['external_urls'].get('spotify'),
                'image_url': track['album']['images'][0]['url'] if track['album']['images'] else None,
                'track_id': track['id'],
                'popularity': track.get('popularity', 0)
            }

            # Create search query for yt-dlp (most accurate match)
            artist_name = metadata['artist']
            track_name = metadata['name']
            metadata['search_query'] = f"{artist_name} - {track_name} official audio"

            logger.info(f"Spotify found: {metadata['search_query']} (popularity: {metadata['popularity']})")
            return metadata

        return None
    except Exception as e:
        logger.error(f"Spotify search error: {e}")
        return None

async def download_music_async(cid, query, search_msg):
    """Spotify-first music download with progress status and fallback"""
    file_path = None
    progress_data = {'percent': 0}
    stop_event = asyncio.Event()

    # Acquire semaphore to limit concurrent downloads
    async with music_semaphore:
        try:
            # Update status
            await bot.edit_message_text("🔍 Spotify'dan qidirilmoqda...", cid, search_msg.message_id)

            # Clean up old files
            cleanup_temp_files(cid)

            # STEP 1: Try Spotify for accurate metadata
            spotify_metadata = await search_spotify(query)

            # STEP 2: If Spotify found, use its exact metadata; otherwise use original query
            if spotify_metadata:
                await bot.edit_message_text(
                    f"🎵 Spotify'da topildi:\n🎤 {spotify_metadata['artist']} - {spotify_metadata['name']}",
                    cid, search_msg.message_id
                )
                search_query = spotify_metadata['search_query']
                title = spotify_metadata['name']
                artist = spotify_metadata['artist']
                duration = spotify_metadata['duration']
                source = "spotify"
            else:
                await bot.edit_message_text("🔍 SoundCloud'dan qidirilmoqda...", cid, search_msg.message_id)
                search_query = f"scsearch1:{query}"
                title = None
                artist = None
                duration = 0
                source = "soundcloud"

            # STEP 3: Download with progress tracking
            await bot.edit_message_text("⬇️ 0%", cid, search_msg.message_id)

            # Start progress updater
            progress_task = asyncio.create_task(
                update_download_progress(cid, search_msg.message_id, progress_data, stop_event)
            )

            # yt-dlp options with progress hook
            def progress_hook(d):
                if d['status'] == 'downloading':
                    if 'downloaded_bytes' in d and 'total_bytes' in d and d['total_bytes']:
                        progress_data['percent'] = int(d['downloaded_bytes'] / d['total_bytes'] * 100)
                    elif 'downloaded_bytes' in d and 'total_bytes_estimate' in d and d['total_bytes_estimate']:
                        progress_data['percent'] = int(d['downloaded_bytes'] / d['total_bytes_estimate'] * 100)

            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join(TEMP_DIR, f'{cid}_%(title)s.%(ext)s'),
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
                'socket_timeout': 15,
                'retries': 2,
                'fragment_retries': 2,
                'retry_sleep': 2,
                'extract_flat': False,
                'default_search': 'auto',
                'playlist_items': '1',
                'buffersize': 4096,
                'noresizebuffer': True,
                'postprocessors': [
                    {
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '128',
                    }
                ],
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                },
                'progress_hooks': [progress_hook],
            }

            # Run yt-dlp in executor
            loop = asyncio.get_event_loop()
            info = None
            download_sources = [
                search_query,  # Primary source (Spotify query or SoundCloud)
                f"ytsearch1:{query} audio",  # YouTube fallback
                f"{query} audio",  # Auto search fallback
            ]

            for attempt, dl_query in enumerate(download_sources):
                try:
                    if attempt > 0:
                        await bot.edit_message_text(f"🔍 Boshqa manbadan qidirilmoqda... ({attempt}/2)", cid, search_msg.message_id)

                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = await loop.run_in_executor(None, lambda: ydl.extract_info(dl_query, download=True))

                    if info:
                        if 'entries' in info and info['entries']:
                            info = info['entries'][0]
                        break

                except Exception as e:
                    error_str = str(e).lower()
                    logger.warning(f"Download attempt {attempt + 1} failed: {e}")
                    if any(x in error_str for x in ['sign in', 'confirm you', 'robot', 'bot', 'verify']):
                        continue
                    if attempt < len(download_sources) - 1:
                        await asyncio.sleep(1)

            # Stop progress updater
            stop_event.set()
            try:
                await progress_task
            except Exception:
                pass

            # Check results
            if not info:
                await bot.edit_message_text(
                    "❌ Musiqa topilmadi. Iltimos, boshqa qo'shiq nomi yozib ko'ring.",
                    cid, search_msg.message_id
                )
                return

            # Use Spotify metadata if available, otherwise from source
            if not title:
                title = info.get('title', 'Unknown').split(' - ')[-1] if ' - ' in info.get('title', '') else info.get('title', 'Unknown')
                artist = info.get('uploader', info.get('channel', 'Unknown'))
                duration = info.get('duration', 0)
                source = info.get('extractor', 'unknown').split(':')[0]

            # Update status
            await bot.edit_message_text("📤 Yuborilmoqda...", cid, search_msg.message_id)

            # Find downloaded file
            downloaded_files = list(Path(TEMP_DIR).glob(f"{cid}_*.mp3"))
            if not downloaded_files:
                downloaded_files = list(Path(TEMP_DIR).glob(f"{cid}_*.*"))
                if not downloaded_files:
                    await bot.edit_message_text("❌ Fayl topilmadi.", cid, search_msg.message_id)
                    return

            file_path = str(downloaded_files[0])

            # Check file size
            file_size = os.path.getsize(file_path)
            if file_size > 50 * 1024 * 1024:
                await bot.edit_message_text("❌ Fayl hajmi juda katta (>50MB).", cid, search_msg.message_id)
                return

            # Source emoji
            source_emoji = {"spotify": "🟢", "soundcloud": "☁️", "youtube": "▶️"}.get(source.lower(), "�")

            # Send audio with Spotify-accurate metadata
            with open(file_path, "rb") as f:
                await bot.send_audio(
                    cid,
                    f,
                    title=title,
                    performer=artist,
                    duration=duration,
                    caption=f"{source_emoji} {artist} - {title}\n✅ @foyda1ii_bot",
                    reply_markup=main_menu()
                )

            await bot.delete_message(cid, search_msg.message_id)
            user_state[cid] = None

            # Background cleanup
            asyncio.create_task(background_cleanup(file_path))

        except Exception as e:
            stop_event.set()
            logger.error(f"Music search error: {e}")
            try:
                await bot.edit_message_text(
                    "❌ Xatolik yuz berdi. Iltimos, boshqa musiqa qidirib ko'ring.",
                    cid, search_msg.message_id
                )
            except Exception:
                pass
        finally:
            if not file_path:
                safe_remove(file_path)


def format_duration(seconds):
    """Format seconds to mm:ss"""
    if not seconds:
        return "00:00"
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"

async def search_spotify_top10(query):
    """Search Spotify for top 10 tracks"""
    if not spotify_client:
        return None

    try:
        results = spotify_client.search(q=query, type='track', limit=10)

        if results and 'tracks' in results and results['tracks']['items']:
            tracks = []
            for track in results['tracks']['items']:
                tracks.append({
                    'id': track['id'],
                    'name': track['name'],
                    'artist': track['artists'][0]['name'],
                    'artists_full': [a['name'] for a in track['artists']],
                    'album': track['album']['name'],
                    'duration': track['duration_ms'] // 1000,
                    'duration_ms': track['duration_ms'],
                    'preview_url': track.get('preview_url'),
                    'spotify_url': track['external_urls'].get('spotify'),
                    'popularity': track.get('popularity', 0)
                })
            return tracks
        return None
    except Exception as e:
        logger.error(f"Spotify top10 search error: {e}")
        return None

async def handle_music_search(m):
    """Professional music search - Spotify top 10 with inline keyboard"""
    cid = m.chat.id
    query = m.text
    user_state[cid] = None  # Clear state immediately

    try:
        # Send searching message
        search_msg = await bot.send_message(cid, "🔍 Spotify'dan qidirilmoqda...")

        # Search Spotify for top 10 results
        tracks = await search_spotify_top10(query)

        if not tracks:
            await bot.edit_message_text(
                "❌ Spotify'da natija topilmadi. Iltimos, boshqa so'rov yuboring.",
                cid, search_msg.message_id,
                reply_markup=main_menu()
            )
            return

        # Build professional numbered list
        result_text = f"🎵 *Topilgan natijalar:* `{query}`\n\n"
        result_text += "*Quyidagi qo'shiqlardan birini tanlang:*\n\n"

        for i, track in enumerate(tracks, 1):
            artist = track['artist']
            name = track['name']
            duration = format_duration(track['duration'])
            result_text += f"{i}. {artist} - {name} `[{duration}]`\n"

        # Create inline keyboard with numbers 1-10
        keyboard_rows = []

        # Number buttons (2 rows of 5)
        row1 = [types.InlineKeyboardButton(str(i), callback_data=f"music:{tracks[i-1]['id']}:{i}") for i in range(1, 6)]
        row2 = [types.InlineKeyboardButton(str(i), callback_data=f"music:{tracks[i-1]['id']}:{i}") for i in range(6, 11)]
        keyboard_rows.append(row1)
        keyboard_rows.append(row2)

        # Navigation buttons
        keyboard_rows.append([
            types.InlineKeyboardButton("⬅️ Orqaga", callback_data="music:back"),
            types.InlineKeyboardButton("❌ Yopish", callback_data="music:close")
        ])

        markup = types.InlineKeyboardMarkup(keyboard_rows)

        # Store tracks data in user context (temporary)
        user_state[cid + '_tracks'] = tracks
        user_state[cid + '_query'] = query

        await bot.edit_message_text(
            result_text,
            cid, search_msg.message_id,
            parse_mode='Markdown',
            reply_markup=markup
        )

    except Exception as e:
        logger.error(f"Music search error: {e}")
        await bot.send_message(cid, f"❌ Xatolik: {str(e)[:100]}", reply_markup=main_menu())

@bot.callback_query_handler(func=lambda c: c.data.startswith("music:"))
async def music_callback_handler(call):
    """Handle music selection from inline keyboard"""
    cid = call.message.chat.id
    data = call.data
    msg_id = call.message.message_id

    try:
        await bot.answer_callback_query(call.id)

        # Handle navigation buttons
        if data == "music:back":
            await bot.edit_message_text("📋 Asosiy menu", cid, msg_id, reply_markup=main_menu())
            user_state[cid] = None
            return

        if data == "music:close":
            await bot.delete_message(cid, msg_id)
            user_state[cid] = None
            return

        # Handle music selection: music:{track_id}:{number}
        parts = data.split(":")
        if len(parts) >= 2:
            track_id = parts[1]
            track_num = parts[2] if len(parts) > 2 else "?"

            # Get tracks from user context
            tracks = user_state.get(cid + '_tracks', [])
            track = None
            for t in tracks:
                if t['id'] == track_id:
                    track = t
                    break

            if not track:
                await bot.edit_message_text("❌ Qo'shiq ma'lumotlari topilmadi.", cid, msg_id)
                return

            # Update message to show loading
            await bot.edit_message_text(
                f"🎵 {track['artist']} - {track['name']}\n\n⏳ Yuklanmoqda...",
                cid, msg_id
            )

            # Download the track
            await download_single_track(cid, track, msg_id)

    except Exception as e:
        logger.error(f"Music callback error: {e}")
        await bot.send_message(cid, f"❌ Xatolik: {str(e)[:100]}", reply_markup=main_menu())

async def download_single_track(cid, track, msg_id):
    """Download single track from Spotify metadata"""
    file_path = None
    progress_data = {'percent': 0}
    stop_event = asyncio.Event()

    async with music_semaphore:
        try:
            # Create search query from Spotify metadata
            search_query = f"{track['artist']} - {track['name']} official audio"

            # Progress updater
            async def update_progress():
                last_percent = -1
                while not stop_event.is_set():
                    try:
                        percent = progress_data.get('percent', 0)
                        if percent != last_percent and percent < 100:
                            last_percent = percent
                            try:
                                await bot.edit_message_text(
                                    f"🎵 {track['artist']} - {track['name']}\n⬇️ {percent}%",
                                    cid, msg_id
                                )
                            except Exception:
                                pass
                        await asyncio.sleep(2)
                    except Exception:
                        break

            # Progress hook for yt-dlp
            def progress_hook(d):
                if d['status'] == 'downloading':
                    if 'downloaded_bytes' in d and 'total_bytes' in d and d['total_bytes']:
                        progress_data['percent'] = int(d['downloaded_bytes'] / d['total_bytes'] * 100)
                    elif 'downloaded_bytes' in d and 'total_bytes_estimate' in d:
                        progress_data['percent'] = int(d['downloaded_bytes'] / d['total_bytes_estimate'] * 100)

            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join(TEMP_DIR, f'{cid}_%(title)s.%(ext)s'),
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
                'socket_timeout': 15,
                'retries': 2,
                'postprocessors': [
                    {
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '128',
                    }
                ],
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                },
                'progress_hooks': [progress_hook],
            }

            # Start progress updater
            progress_task = asyncio.create_task(update_progress())

            # Download
            loop = asyncio.get_event_loop()
            info = None

            # Try multiple sources
            sources = [
                search_query,
                f"ytsearch1:{track['artist']} {track['name']}",
                f"scsearch1:{track['artist']} {track['name']}",
            ]

            for src in sources:
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = await loop.run_in_executor(None, lambda: ydl.extract_info(src, download=True))
                    if info:
                        break
                except Exception as e:
                    logger.warning(f"Source failed {src}: {e}")
                    continue

            # Stop progress updater
            stop_event.set()
            try:
                await progress_task
            except Exception:
                pass

            if not info:
                await bot.edit_message_text(
                    "❌ Yuklab olishda xatolik. Iltimos, boshqa qo'shiqni tanlang.",
                    cid, msg_id
                )
                return

            # Find downloaded file
            downloaded_files = list(Path(TEMP_DIR).glob(f"{cid}_*.mp3"))
            if not downloaded_files:
                downloaded_files = list(Path(TEMP_DIR).glob(f"{cid}_*.*"))

            if not downloaded_files:
                await bot.edit_message_text("❌ Fayl topilmadi.", cid, msg_id)
                return

            file_path = str(downloaded_files[0])

            # Check file size
            if os.path.getsize(file_path) > 50 * 1024 * 1024:
                await bot.edit_message_text("❌ Fayl hajmi juda katta (>50MB).", cid, msg_id)
                return

            # Send audio
            await bot.edit_message_text("📤 Yuborilmoqda...", cid, msg_id)

            with open(file_path, "rb") as f:
                await bot.send_audio(
                    cid,
                    f,
                    title=track['name'],
                    performer=track['artist'],
                    duration=track['duration'],
                    caption=f"🟢 {track['artist']} - {track['name']}\n✅ @foyda1ii_bot",
                    reply_markup=main_menu()
                )

            await bot.delete_message(cid, msg_id)

            # Background cleanup
            asyncio.create_task(background_cleanup(file_path))

        except Exception as e:
            logger.error(f"Download single track error: {e}")
            await bot.edit_message_text(
                "❌ Xatolik yuz berdi. Iltimos, boshqa qo'shiqni tanlang.",
                cid, msg_id
            )
            safe_remove(file_path)

# ================= VIDEO QUEUE WORKER =================
async def video_queue_worker():
    """Process circle videos from queue sequentially but fast"""
    while True:
        try:
            # Get task from queue
            task = await video_queue.get()
            cid, input_path, output_path, msg_id = task

            async with circle_semaphore:
                try:
                    await handle_circle_video(cid, input_path, output_path, msg_id)
                except Exception as e:
                    logger.error(f"Queue processing error for {cid}: {e}")
                finally:
                    # Mark task as done
                    video_queue.task_done()

        except Exception as e:
            logger.error(f"Queue worker error: {e}")
            await asyncio.sleep(1)

# ================= VIDEO HANDLER =================
@bot.message_handler(content_types=['video'])
async def video_handler(m):
    """Handle video uploads with queue-based processing for circle videos"""
    cid = m.chat.id
    state = user_state.get(cid)

    if state not in ["mp3", "circle"]:
        return

    input_path = None
    output_path = None

    try:
        # Send initial message
        msg = await bot.send_message(cid, "⏳ Video yuklanmoqda...")

        # Download video file
        file_info = await bot.get_file(m.video.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)

        input_path = os.path.join(TEMP_DIR, f"{cid}_input.mp4")
        with open(input_path, "wb") as f:
            f.write(downloaded_file)

        file_size = os.path.getsize(input_path)
        logger.info(f"Downloaded video: {file_size} bytes for user {cid}")

        # Check size limit (100MB for processing)
        if file_size > 100 * 1024 * 1024:
            await bot.edit_message_text("❌ Video hajmi juda katta (>100MB). Kichikroq video yuboring.", cid, msg.message_id)
            asyncio.create_task(background_cleanup(input_path))
            return

        # Process based on state
        if state == "mp3":
            output_path = os.path.join(TEMP_DIR, f"{cid}_output.mp3")
            async with video_semaphore:
                try:
                    await handle_video_to_mp3(cid, input_path, output_path, msg.message_id)
                finally:
                    # Background cleanup for MP3
                    asyncio.create_task(background_cleanup(input_path))
                    asyncio.create_task(background_cleanup(output_path))

        elif state == "circle":
            output_path = os.path.join(TEMP_DIR, f"{cid}_circle.mp4")
            # Add to queue for sequential processing
            await video_queue.put((cid, input_path, output_path, msg.message_id))
            await bot.edit_message_text(f"🔵 Navbatga qo'shildi. {video_queue.qsize()} ta video kutmoqda...", cid, msg.message_id)

        user_state[cid] = None

    except Exception as e:
        logger.error(f"Video processing error for user {cid}: {e}")
        try:
            await bot.send_message(cid, f"❌ Xatolik: {str(e)[:100]}", reply_markup=main_menu())
        except Exception:
            pass
        # Background cleanup on error
        if input_path:
            asyncio.create_task(background_cleanup(input_path))
        if output_path:
            asyncio.create_task(background_cleanup(output_path))

async def run_ffmpeg_async(cmd):
    """Run ffmpeg command asynchronously"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=300))

async def handle_video_to_mp3(cid, input_path, output_path, msg_id):
    """Convert video to high quality MP3 - async version"""
    try:
        await bot.edit_message_text("🎵 Audio ajratib olinmoqda (320kbps)...", cid, msg_id)

        # FFmpeg command for high quality MP3 extraction
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vn",  # No video
            "-ar", "44100",  # Sample rate
            "-ac", "2",  # Stereo
            "-b:a", "320k",  # Bitrate 320kbps
            "-f", "mp3",
            output_path
        ]

        result = await run_ffmpeg_async(cmd)

        if result.returncode != 0:
            logger.error(f"FFmpeg error: {result.stderr}")
            await bot.edit_message_text("❌ Konvertatsiyada xatolik.", cid, msg_id)
            return

        # Check output file
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            await bot.edit_message_text("❌ Audio fayl yaratilmadi.", cid, msg_id)
            return

        # Send audio
        await bot.edit_message_text("📤 MP3 yuborilmoqda...", cid, msg_id)

        with open(output_path, "rb") as f:
            await bot.send_audio(
                cid,
                f,
                title="Video Audio",
                performer="@foyda1ii_bot",
                caption="✅ Video dan MP3\n✅ @foyda1ii_bot",
                reply_markup=main_menu()
            )

        await bot.delete_message(cid, msg_id)

    except subprocess.TimeoutExpired:
        await bot.edit_message_text("❌ Vaqt tugadi. Video juda katta.", cid, msg_id)
    except Exception as e:
        logger.error(f"MP3 conversion error: {e}")
        await bot.edit_message_text(f"❌ Xatolik: {str(e)[:100]}", cid, msg_id)

async def check_ffmpeg_installed():
    """Check if ffmpeg is installed on the system - async"""
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        )
        return result.returncode == 0
    except Exception:
        return False

async def handle_circle_video(cid, input_path, output_path, msg_id):
    """Convert video to circle video note format - TURBO MODE (3-4x faster)"""
    try:
        # Check ffmpeg is installed
        ffmpeg_installed = await check_ffmpeg_installed()
        if not ffmpeg_installed:
            await bot.edit_message_text("❌ Serverda ffmpeg o'rnatilmagan.", cid, msg_id)
            logger.error("FFmpeg is not installed on the server")
            return

        # TURBO SETTINGS: Strict 320x320, 24fps, maximum compression
        CIRCLE_SIZE = 320
        FPS = 24  # Lower fps for 3-4x speed boost

        await bot.edit_message_text(f"🔵 Turbo rejim: {CIRCLE_SIZE}x{CIRCLE_SIZE} @ {FPS}fps", cid, msg_id)

        # Get video dimensions
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            input_path
        ]
        try:
            loop = asyncio.get_event_loop()
            probe_result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
            )
            dims = probe_result.stdout.strip().split('x')
            width, height = int(dims[0]), int(dims[1])
            min_dim = min(width, height)
            crop_x = (width - min_dim) // 2
            crop_y = (height - min_dim) // 2
        except Exception:
            # Default to center crop if probe fails
            min_dim = "min(iw,ih)"
            crop_x = "(iw-min(iw,ih))/2"
            crop_y = "(ih-min(iw,ih))/2"

        # TURBO FFmpeg command - 3-4x faster processing
        # Key optimizations: -tune zerolatency, fps=24, strict 320x320, max threads
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            # Strict 320x320, 24fps for maximum speed
            "-vf", f"crop={min_dim}:{min_dim}:{crop_x}:{crop_y},fps={FPS},scale={CIRCLE_SIZE}:{CIRCLE_SIZE}:flags=fast_bilinear,setsar=1:1",
            "-c:v", "libx264",
            "-preset", "ultrafast",      # Maximum speed preset
            "-tune", "zerolatency",       # Ultra-low latency mode (key for speed)
            "-crf", "32",                # Higher compression = smaller file = faster
            "-threads", "0",              # ALL CPU cores
            "-c:a", "aac",
            "-b:a", "64k",               # Lower audio bitrate for speed
            "-ar", "22050",              # Lower sample rate
            "-ac", "1",                  # Mono audio
            "-movflags", "+faststart",
            "-t", "60",                  # Max 60 seconds
            "-pix_fmt", "yuv420p",
            "-fflags", "+fastseek",
            "-max_muxing_queue_size", "1024",  # Prevent buffer issues
            output_path
        ]

        result = await run_ffmpeg_async(cmd)

        if result.returncode != 0:
            logger.error(f"FFmpeg error: {result.stderr}")
            await bot.edit_message_text(f"❌ Video konvertatsiyada xatolik:\n{result.stderr[:100]}", cid, msg_id)
            return

        # Check output
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            await bot.edit_message_text("❌ Video fayl yaratilmadi.", cid, msg_id)
            return

        # Send video note (circle video)
        await bot.edit_message_text("📤 Yumaloq video yuborilmoqda...", cid, msg_id)

        with open(output_path, "rb") as f:
            await bot.send_video_note(cid, f, length=CIRCLE_SIZE, reply_markup=main_menu())

        await bot.delete_message(cid, msg_id)

        # Background cleanup - don't wait
        asyncio.create_task(background_cleanup(input_path))
        asyncio.create_task(background_cleanup(output_path))

    except subprocess.TimeoutExpired:
        await bot.edit_message_text("❌ Vaqt tugadi. Video juda katta.", cid, msg_id)
    except Exception as e:
        logger.error(f"Circle video error: {e}")
        await bot.edit_message_text(f"❌ Xatolik: {str(e)[:100]}", cid, msg_id)

# ================= AUTO POST (ASYNC) =================
async def auto_post_loop():
    """Async auto post loop"""
    while True:
        try:
            if AUTO_POST_TEXT:
                users = get_users()
                for u in users:
                    try:
                        await bot.send_message(u, AUTO_POST_TEXT)
                        await asyncio.sleep(0.05)
                    except Exception as e:
                        logger.error(f"Auto post error for {u}: {e}")
        except Exception as e:
            logger.error(f"Auto post loop error: {e}")
        await asyncio.sleep(3600)

# ================= RUN (ASYNC) =================
async def main():
    """Main async function"""
    logger.info("Starting async bot...")

    # Start auto post loop in background
    asyncio.create_task(auto_post_loop())

    # Start video queue worker for sequential but fast circle video processing
    asyncio.create_task(video_queue_worker())
    logger.info("Video queue worker started")

    # Start bot polling
    while True:
        try:
            logger.info("🔥 ASYNC BOT ISHLAYAPTI...")
            await bot.infinity_polling(
                skip_pending=True,
                timeout=60,
                interval=3
            )
        except Exception as e:
            logger.error(f"Polling error: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
