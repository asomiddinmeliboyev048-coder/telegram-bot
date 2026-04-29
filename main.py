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

# Async bot instance
bot = AsyncTeleBot(BOT_TOKEN)

# Semaphore for limiting concurrent heavy tasks (5 max concurrent video processing)
video_semaphore = asyncio.Semaphore(5)
music_semaphore = asyncio.Semaphore(10)

TEMP_DIR = tempfile.gettempdir()

CHANNEL = -1003877967882
OWNER_ID = 7171330738

# User state storage
user_state = {}
user_voice = {}

# Track active user tasks for cleanup
active_tasks = {}

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

async def download_music_async(cid, query, search_msg):
    """Async music download with semaphore and status updates"""
    file_path = None

    # Acquire semaphore to limit concurrent downloads
    async with music_semaphore:
        try:
            # Update status
            await bot.edit_message_text("🔍 YouTube'dan qidirilmoqda...", cid, search_msg.message_id)

            # Clean up old files
            cleanup_temp_files(cid)

            # yt-dlp options optimized for SPEED (bestaudio only, no post-processing)
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
                'default_search': 'ytsearch1',
                'playlist_items': '1',
                # Speed optimizations
                'buffersize': 4096,
                'noresizebuffer': True,
                # Audio extraction only - no re-encoding for speed
                'postprocessors': [
                    {
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '128',  # Lower quality = faster
                    }
                ],
                # User agent
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                }
            }

            search_query = f"ytsearch1:{query} audio"

            # Run yt-dlp in executor to not block event loop
            loop = asyncio.get_event_loop()

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    info = await loop.run_in_executor(None, lambda: ydl.extract_info(search_query, download=True))
                except Exception as e:
                    logger.error(f"Download error: {e}")
                    await bot.edit_message_text("⏳ Qayta urinilmoqda...", cid, search_msg.message_id)
                    # Retry with simpler query
                    search_query = f"ytsearch1:{query}"
                    info = await loop.run_in_executor(None, lambda: ydl.extract_info(search_query, download=True))

                if not info or 'entries' not in info or not info['entries']:
                    await bot.edit_message_text("❌ Hech narsa topilmadi. Boshqa so'rov yuboring.", cid, search_msg.message_id)
                    return

                entry = info['entries'][0]
                title = entry.get('title', 'Unknown')
                artist = entry.get('uploader', entry.get('channel', 'Unknown'))
                duration = entry.get('duration', 0)

                # Update status
                await bot.edit_message_text("📤 Yuborilmoqda...", cid, search_msg.message_id)

                # Find the downloaded file
                downloaded_files = list(Path(TEMP_DIR).glob(f"{cid}_*.mp3"))
                if not downloaded_files:
                    # Try other audio formats
                    downloaded_files = list(Path(TEMP_DIR).glob(f"{cid}_*.*"))
                    if not downloaded_files:
                        await bot.edit_message_text("❌ Fayl topilmadi.", cid, search_msg.message_id)
                        return

                file_path = str(downloaded_files[0])

                # Check file size (Telegram limit ~50MB for bots)
                file_size = os.path.getsize(file_path)
                if file_size > 50 * 1024 * 1024:
                    await bot.edit_message_text("❌ Fayl hajmi juda katta (>50MB).", cid, search_msg.message_id)
                    return

                # Send the audio
                with open(file_path, "rb") as f:
                    await bot.send_audio(
                        cid,
                        f,
                        title=title,
                        performer=artist,
                        duration=duration,
                        caption=f"🎵 {artist} - {title}\n✅ @foyda1ii_bot",
                        reply_markup=main_menu()
                    )

                await bot.delete_message(cid, search_msg.message_id)
                user_state[cid] = None

        except Exception as e:
            logger.error(f"Music search error: {e}")
            error_msg = str(e)
            try:
                if "Timeout" in error_msg:
                    await bot.edit_message_text("❌ Server javob bermadi. Iltimos, keyinroq urinib ko'ring.", cid, search_msg.message_id)
                elif "Connection" in error_msg:
                    await bot.edit_message_text("❌ Internet bog'lanishida muammo. Qayta urinib ko'ring.", cid, search_msg.message_id)
                else:
                    await bot.edit_message_text(f"❌ Xatolik: {error_msg[:100]}", cid, search_msg.message_id)
            except Exception:
                pass
        finally:
            safe_remove(file_path)


async def handle_music_search(m):
    """Search and download music from YouTube - async with status updates"""
    cid = m.chat.id
    query = m.text

    try:
        # Send initial message immediately (non-blocking)
        search_msg = await bot.send_message(cid, "🔍 Qidirilmoqda...")

        # Create background task for download
        task = asyncio.create_task(download_music_async(cid, query, search_msg))
        active_tasks[cid] = task

        # Wait for task to complete
        await task

    except Exception as e:
        logger.error(f"Music search start error: {e}")
        await bot.send_message(cid, f"❌ Xatolik: {str(e)[:100]}", reply_markup=main_menu())
    finally:
        if cid in active_tasks:
            del active_tasks[cid]

# ================= VIDEO HANDLER =================
@bot.message_handler(content_types=['video'])
async def video_handler(m):
    """Handle video uploads with semaphore for limiting concurrent processing"""
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
            safe_remove(input_path)
            return

        # Process with semaphore to limit concurrent heavy tasks
        if state == "mp3":
            output_path = os.path.join(TEMP_DIR, f"{cid}_output.mp3")
            async with video_semaphore:
                await handle_video_to_mp3(cid, input_path, output_path, msg.message_id)

        elif state == "circle":
            output_path = os.path.join(TEMP_DIR, f"{cid}_circle.mp4")
            async with video_semaphore:
                await handle_circle_video(cid, input_path, output_path, msg.message_id)

        user_state[cid] = None

    except Exception as e:
        logger.error(f"Video processing error for user {cid}: {e}")
        try:
            await bot.send_message(cid, f"❌ Xatolik: {str(e)[:100]}", reply_markup=main_menu())
        except Exception:
            pass
    finally:
        safe_remove(input_path)
        safe_remove(output_path)

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
    """Convert video to circle video note format - async version"""
    try:
        # Check ffmpeg is installed
        ffmpeg_installed = await check_ffmpeg_installed()
        if not ffmpeg_installed:
            await bot.edit_message_text("❌ Serverda ffmpeg o'rnatilmagan.", cid, msg_id)
            logger.error("FFmpeg is not installed on the server")
            return

        await bot.edit_message_text("🔵 Yumaloq video yaratilmoqda (400x400)...", cid, msg_id)

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

        # FFmpeg command for circle video - 400x400, square format
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", f"crop={min_dim}:{min_dim}:{crop_x}:{crop_y},scale=400:400:force_original_aspect_ratio=increase,crop=400:400,setsar=1:1",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "28",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-ac", "2",
            "-movflags", "+faststart",
            "-t", "60",
            "-pix_fmt", "yuv420p",
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
            await bot.send_video_note(cid, f, length=400, reply_markup=main_menu())

        await bot.delete_message(cid, msg_id)

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
