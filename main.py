# ================= IMMEDIATE DEBUG =================
print("DEBUG: Kod o'qilmoqda...")

# ================= RENDER OPTIMIZED IMPORTS =================
# Fast startup - lazy load heavy modules
import asyncio
import os
import subprocess
import logging
import tempfile
import time
import sys
from pathlib import Path

from dotenv import load_dotenv
from telebot import TeleBot
from telebot import types

# Lazy imports - loaded only when needed
yt_dlp = None
edge_tts = None

# ================= FAST STARTUP =================
load_dotenv()
print("DEBUG: Environment yuklandi...")

# Optimize logging for Render
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s - %(message)s',  # Simplified format
    handlers=[logging.StreamHandler()]  # Console only for Render
)
logger = logging.getLogger(__name__)

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Will be validated in main() to allow safe startup wrapper

# YouTube only - No Spotify
music_cache = {}  # Simple memory cache for music

def lazy_load_modules():
    """Lazy load heavy modules on first use"""
    global yt_dlp, edge_tts
    if yt_dlp is None:
        import yt_dlp as yd
        yt_dlp = yd
    if edge_tts is None:
        import edge_tts as et
        edge_tts = et
    return yt_dlp, edge_tts

# Music cache helpers
def get_cache_key(query):
    return query.lower().strip()

def get_cached_audio(query):
    key = get_cache_key(query)
    if key in music_cache:
        cache_time, file_path = music_cache[key]
        if time.time() - cache_time < 3600 and os.path.exists(file_path):  # 1 hour cache
            return file_path
        else:
            del music_cache[key]
    return None

def cache_audio(query, file_path):
    key = get_cache_key(query)
    music_cache[key] = (time.time(), file_path)
    # Keep cache size manageable
    if len(music_cache) > 50:
        oldest = min(music_cache, key=lambda k: music_cache[k][0])
        del music_cache[oldest]

# Async helper for CPU-bound tasks
async def run_in_thread(func, *args, **kwargs):
    """Run function in thread pool using asyncio.to_thread (Python 3.9+)"""
    try:
        # Python 3.9+ has asyncio.to_thread
        return await asyncio.to_thread(func, *args, **kwargs)
    except AttributeError:
        # Fallback for older Python versions
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

# Async bot instance
bot = AsyncTeleBot(BOT_TOKEN)

# RENDER OPTIMIZED: Lower semaphore limits for stability on shared CPU
video_semaphore = asyncio.Semaphore(2)      # Was 3 - reduce memory pressure
music_semaphore = asyncio.Semaphore(5)      # Was 10 - limit concurrent downloads
circle_semaphore = asyncio.Semaphore(1)   # Was 2 - sequential processing

# Use Render disk or system temp
TEMP_DIR = os.getenv("RENDER_DISK_PATH", tempfile.gettempdir())
logger.info(f"Using temp dir: {TEMP_DIR}")

CHANNEL = -1003877967882
OWNER_ID = 7171330738

# User state storage - with size limit for memory protection
user_state = {}
user_voice = {}
active_tasks = {}

# Video processing queue
video_queue = asyncio.Queue(maxsize=10)  # Limit queue size

# ================= RENDER OPTIMIZED CLEANUP =================
async def periodic_temp_cleanup():
    """Periodic cleanup of old temp files - runs every 5 minutes"""
    while True:
        try:
            await asyncio.sleep(300)  # 5 minutes
            cleaned = 0
            total_size = 0
            
            # Clean files older than 10 minutes
            cutoff_time = time.time() - 600
            
            for file in Path(TEMP_DIR).glob("*"):
                try:
                    if file.is_file():
                        file_time = file.stat().st_mtime
                        file_size = file.stat().st_size
                        
                        if file_time < cutoff_time:
                            file.unlink()
                            cleaned += 1
                        else:
                            total_size += file_size
                except Exception:
                    pass
            
            # Log cleanup stats
            if cleaned > 0:
                logger.info(f"Cleaned {cleaned} old temp files")
            if total_size > 100 * 1024 * 1024:  # 100MB
                logger.warning(f"Temp dir large: {total_size / 1024 / 1024:.1f}MB")
                
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

async def background_cleanup(file_path, delay=5):
    """Cleanup files in background after delay seconds"""
    await asyncio.sleep(delay)
    safe_remove(file_path)

def cleanup_temp_files(cid):
    """Clean up temporary files for a user"""
    patterns = [f"{cid}.*", f"{cid}_*", f"music_{cid}_*"]
    for pattern in patterns:
        for file in Path(TEMP_DIR).glob(pattern):
            try:
                file.unlink()
                logger.info(f"Cleaned: {file.name}")
            except Exception:
                pass

def safe_remove(filepath):
    """Safely remove a file if it exists"""
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
    except Exception:
        pass

# ================= RENDER PORT BINDING (REQUIRED) =================
# Flask health check - ONLY runs in if __name__ == '__main__' block
# This prevents double startup when module is imported
def run_flask_server():
    """Run Flask web server for Render health check - called once in main()"""
    try:
        import os
        from flask import Flask
        from threading import Thread
        
        app = Flask(__name__)
        
        @app.route('/')
        def home():
            return "Bot is running!"
        
        port = int(os.environ.get('PORT', 10000))  # Render default port
        
        def run():
            app.run(host='0.0.0.0', port=port, threaded=True, debug=False)
        
        Thread(target=run, daemon=True).start()
        logger.info(f"✅ Flask health check active on port {port}")
        return True
    except ImportError:
        logger.warning("⚠️ Flask not installed - health check disabled")
        return False
    except Exception as e:
        logger.error(f"❌ Flask startup error: {e}")
        return False

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
    kb.add("🤡 Kulgili ovoz", "🎭 Venom ovoz")
    kb.add("⬅️ Orqaga")
    return kb

@bot.message_handler(func=lambda m: m.text == "⬅️ Orqaga")
def handle_back_button(m):
    """Handle back button from voice menu"""
    try:
        print(f"DEBUG: Back button from {m.chat.id}")
        cid = m.chat.id
        user_state[cid] = None
        bot.send_chat_action(cid, 'typing')
        bot.send_message(cid, "📋 Asosiy menu", reply_markup=main_menu())
        print(f"DEBUG: Back button processed for {m.chat.id}")
    except Exception as e:
        print(f"ERROR in back button: {e}")
        logger.error(f"Back button error: {e}")

def admin_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📢 Broadcast","📊 Statistika")
    kb.add("📣 Auto Post")
    kb.add("🔙 Orqaga")
    return kb

# ================= START =================
@bot.message_handler(commands=['start'])
def start(m):
    """Minimal start handler for testing"""
    try:
        print(f"DEBUG: /start received from {m.chat.id}")
        bot.send_chat_action(m.chat.id, 'typing')
        bot.reply_to(m, 'Salom! Bot ishlayapti!')
        print(f"DEBUG: /start reply sent to {m.chat.id}")
    except Exception as e:
        print(f"ERROR in start handler: {e}")
        logger.error(f"Start error: {e}")

# ================= ADMIN =================
@bot.message_handler(commands=['admin'])
def admin(m):
    """Admin panel"""
    try:
        print(f"DEBUG: /admin received from {m.chat.id}")
        bot.send_chat_action(m.chat.id, 'typing')
        if m.from_user.id == OWNER_ID:
            bot.send_message(m.chat.id, "⚙️ Admin panel", reply_markup=admin_menu())
        print(f"DEBUG: /admin processed for {m.chat.id}")
    except Exception as e:
        print(f"ERROR in admin handler: {e}")
        logger.error(f"Admin error: {e}")

AUTO_POST_TEXT = None

# ================= TEXT HANDLER =================
@bot.message_handler(content_types=['text'])
async def text_handler(m):
    """Main text message handler with voice menu support"""
    cid = m.chat.id
    txt = m.text
    # Log every incoming message
    logger.info(f"📩 [MESSAGE] User: {cid}, Text: {txt[:100]}")
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

        if txt in ["👨 Erkak ovoz", "👩 Ayol ovoz", "🤡 Kulgili ovoz", "🎭 Venom ovoz"]:
            if "Kulgili" in txt:
                user_voice[cid] = "funny"
            elif "Venom" in txt or "🎭" in txt:
                user_voice[cid] = "venom"
            else:
                user_voice[cid] = "male" if "Erkak" in txt else "female"
            user_state[cid] = "tts"
            await bot.send_message(cid, "✍️ Matn yuboring (o'zbek tilida):")
            return

        # SMART STATE: Check if user is in voice selection but hasn't chosen voice type
        if state == "choose_voice":
            await bot.send_message(cid, "⚠️ Iltimos, avval ovoz turini tanlang!", reply_markup=voice_menu())
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
    """Text to speech handler with funny and venom voice support"""
    # LAZY LOAD: Load edge_tts only when needed
    lazy_load_modules()
    
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
        elif voice_type == "venom":
            # Venom ovoz - using male voice deep effect
            voice = "uz-UZ-SardorNeural"
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
        
        # FIX: Check if audio was actually created
        if not os.path.exists(input_path) or os.path.getsize(input_path) < 100:
            await bot.edit_message_text(
                "❌ Audio yaratilmadi. Iltimos, qisqaroq matn yuboring.",
                cid, msg.message_id
            )
            return

        # VENOM VOICE: MAXIMUM SCARY EFFECT
        if voice_type == "venom":
            await bot.edit_message_text("🎭 Venom ovoz effekti qo'llanilmoqda...", cid, msg.message_id)
            
            # MAXIMUM SCARY Venom effect: Deep pitch, heavy echo, boosted bass
            # bass=g=12 = heavy bass boost (powerful)
            # asetrate=44100*0.5 = 50% deeper pitch (very deep)
            # atempo=2.0 = double speed compensation for asetrate
            # aecho=0.8:0.88:60:0.4 = heavy echo for scary effect
            cmd_venom = [
                "ffmpeg", "-y",
                "-i", input_path,
                "-af", "bass=g=12,asetrate=44100*0.5,atempo=2.0,aecho=0.8:0.88:60:0.4",
                "-ar", "44100",
                "-ac", "1",
                output_path
            ]
            
            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: subprocess.run(cmd_venom, capture_output=True, text=True, timeout=30)
                )
                
                if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    file_to_send = output_path
                    logger.info(f"Venom voice applied for user {cid}")
                else:
                    logger.warning(f"Venom effect failed, using original voice")
                    file_to_send = input_path
            except Exception as e:
                logger.error(f"Venom voice processing error: {e}")
                file_to_send = input_path
        
        # FUNNY VOICE: Apply pitch shift using ffmpeg
        elif voice_type == "funny":
            await bot.edit_message_text("🤡 Kulgili ovoz effekti qo'llanilmoqda...", cid, msg.message_id)

            # Try rubberband first (best quality pitch shift without speed change)
            cmd_rubberband = [
                "ffmpeg", "-y",
                "-i", input_path,
                "-af", "rubberband=pitch=1.5",
                "-ar", "44100",
                "-ac", "1",
                output_path
            ]

            # Fallback: asetrate+atempo method
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

                # First try rubberband
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

# YouTube only - No Spotify

async def download_music_async(cid, query, search_msg):
    """YouTube-only music download with progress status"""
    # LAZY LOAD: Load yt_dlp only when needed
    ydl_module, _ = lazy_load_modules()
    
    file_path = None
    progress_data = {'percent': 0}
    stop_event = asyncio.Event()

    # Acquire semaphore to limit concurrent downloads
    async with music_semaphore:
        try:
            # Update status
            await bot.edit_message_text("🔍 YouTube'dan qidirilmoqda...", cid, search_msg.message_id)

            # Clean up old files
            cleanup_temp_files(cid)

            # STEP 1: Search YouTube
            youtube_results = await search_music_ultrafast(query, limit=10)

            # STEP 2: Use YouTube for download
            search_query = f"ytsearch1:{query}"
            title = None
            artist = None
            duration = 0
            source = "youtube"

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
                'default_search': 'ytsearch',
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

            # Run yt-dlp in thread pool for async operation
            info = None
            download_sources = [
                search_query,  # YouTube primary
                f"ytsearch1:{query} audio",  # YouTube fallback
                f"{query} audio",  # Auto search fallback
            ]

            for attempt, dl_query in enumerate(download_sources):
                try:
                    if attempt > 0:
                        await bot.edit_message_text(f"🔍 Boshqa manbadan qidirilmoqda... ({attempt}/2)", cid, search_msg.message_id)

                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = await run_in_thread(ydl.extract_info, dl_query, download=True)

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

            # Extract metadata from source
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
            source_emoji = {"youtube": "▶️", "soundcloud": "☁️"}.get(source.lower(), "🎵")

            # Send audio with metadata
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
    """Format duration in seconds to MM:SS"""
    if not seconds:
        return "0:00"
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes}:{secs:02d}"

# ================= EXTREME SPEED MUSIC SEARCH (YouTube Only) =================
# Search cache (5 minutes)
_search_cache = {}
_CACHE_TTL = 300

# AUDIO FILE CACHE - avoid re-downloading same songs
_audio_cache = {}  # url -> (file_path, timestamp)
_AUDIO_CACHE_TTL = 600  # 10 minutes
_audio_cache_lock = asyncio.Lock()

def get_cached_search(query):
    """Get cached search results if available and not expired"""
    if query in _search_cache:
        result, timestamp = _search_cache[query]
        if time.time() - timestamp < _CACHE_TTL:
            return result
        del _search_cache[query]
    return None

def cache_search(query, results):
    """Cache search results with timestamp"""
    _search_cache[query] = (results, time.time())

async def get_cached_audio(url):
    """Get cached audio file if available"""
    async with _audio_cache_lock:
        if url in _audio_cache:
            file_path, timestamp = _audio_cache[url]
            if time.time() - timestamp < _AUDIO_CACHE_TTL:
                if os.path.exists(file_path):
                    logger.info(f"Audio cache hit: {url[:50]}...")
                    return file_path
            # Expired or file missing
            del _audio_cache[url]
    return None

async def cache_audio(url, file_path):
    """Cache audio file path"""
    async with _audio_cache_lock:
        _audio_cache[url] = (file_path, time.time())
        logger.info(f"Audio cached: {url[:50]}...")

async def search_music_ultrafast(query, limit=10):
    """EXTREME SPEED music search - YouTube direct, no Spotify"""
    # LAZY LOAD: Load yt_dlp only when needed
    ydl_module, _ = lazy_load_modules()
    
    # Check cache first
    cached = get_cached_search(query)
    if cached:
        logger.info(f"Cache hit for: {query}")
        return cached

    try:
        # ULTRA-FAST yt-dlp options - minimal overhead
        ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'default_search': 'ytsearch10',
            'extract_flat': True,     # FAST: no full extraction
            'skip_download': True,    # FAST: skip download
        }

        # Direct YouTube search with multiple strategies
        search_variants = [
            f"ytsearch{limit}:{query} audio",
            f"ytsearch{limit}:{query} official",
            f"ytsearch{limit}:{query}",
        ]

        for search_query in search_variants:
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    result = await asyncio.wait_for(
                        run_in_thread(ydl.extract_info, search_query, download=False),
                        timeout=12  # Hard timeout
                    )

                if result and 'entries' in result and result['entries']:
                    tracks = []
                    seen = set()  # Deduplicate

                    for entry in result['entries'][:limit]:
                        if not entry:
                            continue

                        # Skip duplicates by video ID
                        vid_id = entry.get('id')
                        if vid_id in seen:
                            continue
                        seen.add(vid_id)

                        # Extract info quickly
                        title = entry.get('title', 'Unknown Track')
                        uploader = entry.get('uploader', entry.get('channel', 'Unknown'))
                        duration = entry.get('duration', 0)

                        # Skip very short videos (likely not music)
                        if duration and duration < 30:
                            continue

                        tracks.append({
                            'id': vid_id,
                            'name': title,
                            'artist': uploader,
                            'duration': int(duration) if duration else 0,
                            'duration_ms': int(duration * 1000) if duration else 0,
                            'url': entry.get('webpage_url', entry.get('url', '')),
                            'thumbnail': entry.get('thumbnail', ''),
                        })

                    if tracks:
                        cache_search(query, tracks)
                        return tracks

            except asyncio.TimeoutError:
                logger.warning(f"Search timeout for: {search_query}")
                continue
            except Exception as e:
                logger.warning(f"Search variant failed: {e}")
                continue

        return None
    except Exception as e:
        logger.error(f"Music search error: {e}")
        return None

# Alias for YouTube search (used by handle_music_search)
search_music_youtube = search_music_ultrafast

async def handle_music_search(m):
    """YOUTUBE ONLY: Music search with 10 results and inline keyboard"""
    cid = m.chat.id
    query = m.text.strip()
    search_msg = None

    try:
        user_state[cid] = None  # Clear state immediately

        # Send searching message
        search_msg = await bot.send_message(cid, "🔍 YouTube'dan qidirilmoqda...")

        # YOUTUBE ONLY: Search using yt-dlp
        tracks = await search_music_youtube(query, limit=10)

        # Empty results - NO KEYBOARD, just message
        if not tracks or len(tracks) == 0:
            await bot.edit_message_text(
                "❌ Topilmadi",
                cid, search_msg.message_id
            )
            return

        # Build numbered list (1-10)
        result_text = f"🎵 *Topilgan natijalar:* `{query}`\n\n"
        result_text += "*Quyidagi qo'shiqlardan birini tanlang:*\n\n"

        for i, track in enumerate(tracks[:10], 1):  # Max 10 results
            artist = str(track.get('artist', 'Unknown'))
            name = str(track.get('name', 'Unknown'))
            duration = format_duration(track.get('duration', 0))
            result_text += f"{i}. {artist} - {name} ({duration})\n"

        # Inline keyboard: buttons 1-10 (all in one row or split)
        keyboard_rows = []
        track_count = min(len(tracks), 10)
        
        # Row 1: buttons 1-5
        if track_count >= 1:
            row1 = []
            for i in range(1, min(6, track_count + 1)):
                row1.append(types.InlineKeyboardButton(str(i), callback_data=f"yt_dl:{i}"))
            keyboard_rows.append(row1)
        
        # Row 2: buttons 6-10 (if available)
        if track_count >= 6:
            row2 = []
            for i in range(6, track_count + 1):
                row2.append(types.InlineKeyboardButton(str(i), callback_data=f"yt_dl:{i}"))
            keyboard_rows.append(row2)

        markup = types.InlineKeyboardMarkup(keyboard_rows)

        # Store track data by index
        user_state[cid + '_tracks'] = {str(i+1): t for i, t in enumerate(tracks[:10])}

        await bot.edit_message_text(
            result_text,
            cid, search_msg.message_id,
            parse_mode='Markdown',
            reply_markup=markup
        )

    except Exception as e:
        logger.error(f"Music search error: {e}")
        try:
            if search_msg:
                await bot.edit_message_text(
                    f"❌ Xatolik: {str(e)[:100]}",
                    cid, search_msg.message_id,
                    reply_markup=main_menu()
                )
            else:
                await bot.send_message(cid, f"❌ Xatolik: {str(e)[:100]}", reply_markup=main_menu())
        except Exception:
            pass

@bot.callback_query_handler(func=lambda c: c.data.startswith("yt_dl:"))
async def youtube_download_handler(call):
    """YOUTUBE: Handle download from YouTube search results"""
    cid = call.message.chat.id
    data = call.data
    msg_id = call.message.message_id

    try:
        await bot.answer_callback_query(call.id)

        # Get index from callback data (yt_dl:1, yt_dl:2, etc.)
        index = data[6:]  # Remove "yt_dl:" prefix
        
        # Get track from stored data
        tracks_dict = user_state.get(cid + '_tracks', {})
        track = tracks_dict.get(index)

        if not track:
            await bot.edit_message_text(
                "❌ Qo'shiq ma'lumotlari topilmadi. Qayta urinib ko'ring.",
                cid, msg_id,
                reply_markup=main_menu()
            )
            return

        # Update message to show loading
        try:
            await bot.edit_message_text(
                f"🎵 {track.get('artist', 'Unknown')} - {track.get('name', 'Unknown')}\n\n⏳ Yuklanmoqda...",
                cid, msg_id
            )
        except Exception:
            pass

        # Download audio only from YouTube
        url = track.get('url', '')
        if not url:
            await bot.edit_message_text(
                "❌ Video URL topilmadi",
                cid, msg_id,
                reply_markup=main_menu()
            )
            return
            
        # Download audio only
        await download_youtube_audio(cid, track, url, msg_id)

    except Exception as e:
        logger.error(f"YouTube download error: {e}")
        try:
            await bot.edit_message_text(
                f"❌ Xatolik: {str(e)[:100]}",
                cid, msg_id,
                reply_markup=main_menu()
            )
        except Exception:
            pass

@bot.callback_query_handler(func=lambda c: c.data.startswith("dl:"))
async def download_url_handler(call):
    """Handle download from YouTube URL (legacy)"""
    cid = call.message.chat.id
    data = call.data
    msg_id = call.message.message_id

    try:
        await bot.answer_callback_query(call.id)

        url = data[3:]  # Remove "dl:" prefix

        # Get track info from stored data
        tracks_dict = user_state.get(cid + '_tracks', {})
        track = tracks_dict.get(url)

        if not track:
            track = {
                'id': 'unknown',
                'name': 'Track',
                'artist': 'Unknown',
                'duration': 0
            }

        # Update message to show loading
        try:
            await bot.edit_message_text(
                f"🎵 {track['artist']} - {track['name']}\n\n⏳ Yuklanmoqda...",
                cid, msg_id
            )
        except Exception:
            pass

        # STABILITY: Wrap download in try-except
        try:
            await download_youtube_audio(cid, track, url, msg_id)
        except Exception as download_error:
            logger.error(f"Download failed: {download_error}")
            await bot.edit_message_text(
                f"❌ Yuklab olishda xatolik: {str(download_error)[:100]}",
                cid, msg_id,
                reply_markup=main_menu()
            )

    except Exception as e:
        logger.error(f"Download error: {e}")
        await bot.send_message(cid, f"❌ Xatolik: {str(e)[:100]}", reply_markup=main_menu())

@bot.callback_query_handler(func=lambda c: c.data in ["music:back", "music:close"])
async def music_navigation_handler(call):
    """Handle navigation buttons (Orqaga, Yopish)"""
    cid = call.message.chat.id
    data = call.data
    msg_id = call.message.message_id

    try:
        await bot.answer_callback_query(call.id)

        if data == "music:back":
            await bot.edit_message_text("📋 Asosiy menu", cid, msg_id, reply_markup=main_menu())
            user_state[cid] = None
            return

        if data == "music:close":
            await bot.delete_message(cid, msg_id)
            user_state[cid] = None
            return

    except Exception as e:
        logger.error(f"Navigation error: {e}")

async def download_youtube_audio(cid, track, url, msg_id):
    """FAST YouTube audio download with caching"""
    ydl_module, _ = lazy_load_modules()
    file_path = None
    
    try:
        # Check cache first
        cache_key = f"{track.get('name', '')}_{track.get('artist', '')}"
        cached_file = get_cached_audio(cache_key)
        if cached_file and os.path.exists(cached_file):
            await bot.edit_message_text("⚡ Keshdan yuborilmoqda...", cid, msg_id)
            with open(cached_file, "rb") as f:
                await bot.send_audio(
                    cid, f,
                    title=track.get('name', 'Music'),
                    performer=track.get('artist', 'Unknown'),
                    duration=track.get('duration', 0),
                    caption=f"🎵 {track.get('artist', 'Unknown')} - {track.get('name', 'Track')}\n⚡ Keshdan\n✅ @foyda1ii_bot",
                    reply_markup=main_menu()
                )
            await bot.delete_message(cid, msg_id)
            return

        await bot.edit_message_text(f"🎵 {track.get('artist', 'Unknown')} - {track.get('name', 'Unknown')}\n\n⏳ Yuklanmoqda...", cid, msg_id)
        
        # Fast download options
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(TEMP_DIR, f'{cid}_%(title)s.%(ext)s'),
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
            'quiet': True,
            'no_warnings': True,
            'max_filesize': 50 * 1024 * 1024,
        }
        
        info = None
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await run_in_thread(ydl.extract_info, url, download=True)
        except Exception as e:
            logger.warning(f"Download failed: {e}")
            await bot.edit_message_text(f"❌ Yuklab olishda xatolik", cid, msg_id, reply_markup=main_menu())
            return
        
        if not info:
            await bot.edit_message_text("❌ Ma'lumot olinmadi", cid, msg_id, reply_markup=main_menu())
            return
        
        # Find MP3 file
        downloaded_files = list(Path(TEMP_DIR).glob(f"{cid}_*.mp3"))
        if not downloaded_files:
            downloaded_files = list(Path(TEMP_DIR).glob(f"{cid}_*.*"))
        
        if not downloaded_files:
            await bot.edit_message_text("❌ Fayl topilmadi", cid, msg_id, reply_markup=main_menu())
            return
        
        file_path = str(downloaded_files[0])
        
        if os.path.getsize(file_path) > 50 * 1024 * 1024:
            await bot.edit_message_text("❌ Fayl juda katta", cid, msg_id, reply_markup=main_menu())
            return
        
        # Cache the file
        cache_audio(cache_key, file_path)
        
        await bot.edit_message_text("📤 Yuborilmoqda...", cid, msg_id)
        with open(file_path, "rb") as f:
            await bot.send_audio(
                cid, f,
                title=track.get('name', info.get('title', 'Music')),
                performer=track.get('artist', info.get('uploader', 'Unknown')),
                duration=track.get('duration', info.get('duration', 0)),
                caption=f"🎵 {track.get('artist', 'Unknown')} - {track.get('name', 'Track')}\n✅ @foyda1ii_bot",
                reply_markup=main_menu()
            )
        
        await bot.delete_message(cid, msg_id)
        safe_remove(file_path)
        
    except Exception as e:
        logger.error(f"Download error: {e}")
        await bot.edit_message_text(f"❌ Xatolik", cid, msg_id, reply_markup=main_menu())
        if file_path:
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
            safe_remove(input_path)
            return

        # Process based on state - IMMEDIATE RESPONSE
        if state == "mp3":
            output_path = os.path.join(TEMP_DIR, f"{cid}_output.mp3")
            # Send immediate status
            await bot.edit_message_text("⏳ Audio ajratilmoqda...", cid, msg.message_id)
            async with video_semaphore:
                try:
                    await handle_video_to_mp3(cid, input_path, output_path, msg.message_id)
                except Exception as e:
                    logger.error(f"MP3 error: {e}")
                    await bot.edit_message_text(f"❌ Xatolik: {str(e)[:100]}", cid, msg.message_id)

        elif state == "circle":
            output_path = os.path.join(TEMP_DIR, f"{cid}_circle.mp4")
            # Send immediate status
            await bot.edit_message_text("⏳ Yumaloq video yaratilmoqda...", cid, msg.message_id)
            # Add to queue for sequential processing
            await video_queue.put((cid, input_path, output_path, msg.message_id))

        user_state[cid] = None

    except Exception as e:
        logger.error(f"Video processing error for user {cid}: {e}")
        try:
            await bot.send_message(cid, f"❌ Xatolik: {str(e)[:100]}", reply_markup=main_menu())
        except Exception:
            pass
        # Immediate cleanup on error
        if input_path:
            safe_remove(input_path)
        if output_path:
            safe_remove(output_path)

async def run_ffmpeg_async(cmd):
    """Run ffmpeg command asynchronously using thread pool"""
    return await run_in_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=300)

async def handle_video_to_mp3(cid, input_path, output_path, msg_id):
    """TURBO SPEED: Extract audio from video to MP3"""
    try:
        await bot.edit_message_text("⚡ MP3 yaratilmoqda...", cid, msg_id)

        # TURBO SPEED: Single-pass fast MP3 extraction
        cmd = [
            "ffmpeg", "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-i", input_path,
            "-vn",                     # No video
            "-acodec", "libmp3lame",  # Fast MP3 codec
            "-q:a", "4",              # Quality 4 (fast, good quality)
            "-preset", "ultrafast",    # Turbo speed preset
            "-threads", "0",           # All CPU cores
            "-tune", "zerolatency",   # Low latency
            output_path
        ]

        result = await run_ffmpeg_async(cmd)

        if result.returncode != 0:
            logger.error(f"FFmpeg error: {result.stderr}")
            await bot.edit_message_text("❌ Konvertatsiyada xatolik.", cid, msg_id)
            return

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            await bot.edit_message_text("❌ Audio fayl yaratilmadi.", cid, msg_id)
            return

        # Send immediately
        await bot.edit_message_text("📤 Yuborilmoqda...", cid, msg_id)

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

        # Immediate cleanup
        safe_remove(input_path)
        safe_remove(output_path)

    except subprocess.TimeoutExpired:
        await bot.edit_message_text("❌ Vaqt tugadi. Video juda katta.", cid, msg_id)
        safe_remove(input_path)
        safe_remove(output_path)
    except Exception as e:
        logger.error(f"MP3 error: {e}")
        await bot.edit_message_text(f"❌ Xatolik: {str(e)[:100]}", cid, msg_id)
        safe_remove(input_path)
        safe_remove(output_path)

async def check_ffmpeg_installed():
    """Check if ffmpeg is installed on the system - async using thread pool"""
    try:
        result = await run_in_thread(
            subprocess.run,
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False

async def handle_circle_video(cid, input_path, output_path, msg_id):
    """MAX SPEED: Convert video to circle video note - MINIMAL PROCESSING"""
    try:
        # Check ffmpeg installed
        ffmpeg_installed = await check_ffmpeg_installed()
        if not ffmpeg_installed:
            await bot.edit_message_text("❌ FFmpeg o'rnatilmagan.", cid, msg_id)
            return

        # YASHIN TEZLIGI: Render server uchun maksimal tezlik
        await bot.edit_message_text("⚡ Video ishlanmoqda...", cid, msg_id)

        # EXACT ffmpeg parameters requested by user + pix_fmt for compatibility
        cmd = [
            "ffmpeg", "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-i", input_path,
            # Video codec and speed settings
            "-c:v", "libx264",
            "-preset", "ultrafast",    # Eng tez preset
            "-tune", "zerolatency",   # Minimal kechikish
            "-threads", "0",          # Barcha CPU yadrolaridan foydalanish
            "-crf", "28",             # Sifat va tezlik balansi
            "-s", "320x320",          # Aniq o'lcham (user talabi)
            "-pix_fmt", "yuv420p",    # Codec compatibility
            # Audio copy (tezroq)
            "-c:a", "copy",
            "-t", "60",               # Maksimum 60 soniya
            "-movflags", "+faststart",
            output_path
        ]

        result = await run_ffmpeg_async(cmd)

        # If copy audio failed, retry with AAC
        if result.returncode != 0:
            logger.warning("Audio copy failed, trying AAC encode...")
            cmd_audio = [
                "ffmpeg", "-y",
                "-hide_banner",
                "-loglevel", "error",
                "-i", input_path,
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-threads", "0",
                "-crf", "28",
                "-s", "320x320",
                "-pix_fmt", "yuv420p",   # Codec compatibility
                "-c:a", "aac",
                "-b:a", "96k",
                "-t", "60",
                "-movflags", "+faststart",
                output_path
            ]
            result = await run_ffmpeg_async(cmd_audio)

        if result.returncode != 0:
            logger.error(f"FFmpeg error: {result.stderr}")
            await bot.edit_message_text("❌ Video konvertatsiyada xatolik.", cid, msg_id)
            safe_remove(input_path)
            return

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            await bot.edit_message_text("❌ Video fayl yaratilmadi.", cid, msg_id)
            safe_remove(input_path)
            return

        # Send video note
        await bot.edit_message_text("📤 Yuborilmoqda...", cid, msg_id)

        with open(output_path, "rb") as f:
            await bot.send_video_note(cid, f, length=CIRCLE_SIZE, reply_markup=main_menu())

        await bot.delete_message(cid, msg_id)

        # Immediate cleanup
        safe_remove(input_path)
        safe_remove(output_path)

    except subprocess.TimeoutExpired:
        await bot.edit_message_text("❌ Vaqt tugadi. Video juda katta.", cid, msg_id)
        safe_remove(input_path)
        safe_remove(output_path)
    except Exception as e:
        logger.error(f"Circle video error: {e}")
        await bot.edit_message_text(f"❌ Xatolik: {str(e)[:100]}", cid, msg_id)
        safe_remove(input_path)
        safe_remove(output_path)

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
    """Main async function to start the bot"""
    try:
        # Validate BOT_TOKEN here instead of at module level for better error visibility
        if not BOT_TOKEN:
            print("💥 FATAL ERROR: BOT_TOKEN environment variable is required!")
            print("💥 Please set BOT_TOKEN in Render dashboard environment variables")
            raise ValueError("BOT_TOKEN environment variable is required!")
        
        # Validate BOT_TOKEN before creating bot
        if not BOT_TOKEN:
            print("DEBUG: TOKEN TOPILMADI!")
            raise ValueError("TOKEN TOPILMADI!")
        
        print(f"DEBUG: Token tekshirildi: {BOT_TOKEN[:10]}...")
        
        # Initialize bot with async mode
        from telebot.async_telebot import AsyncTeleBot
        bot = AsyncTeleBot(BOT_TOKEN)
        logger.info("🚀 Starting bot on Render...")
        logger.info(f"🤖 Bot token: {BOT_TOKEN[:10]}...")
        logger.info(f"📁 Temp dir: {TEMP_DIR}")
        logger.info("=" * 50)
        
        # CRITICAL: Purge webhook and pending updates immediately after bot creation
        print("[1/5] Webhook tozalanyapti...")
        try:
            await bot.remove_webhook(drop_pending_updates=True)
            print("✅ [1/5] Webhook tozalandi!")
            logger.info("✅ Webhook purged")
        except Exception as e:
            print(f"⚠️ [1/5] Webhook xato: {e}")
            logger.warning(f"⚠️ Webhook: {e}")
        
        # Start Flask in separate thread FIRST (before polling) - Render requirement
        print("[2/5] Flask server ishga tushirilmoqda...")
        try:
            import threading
            flask_thread = threading.Thread(target=run_flask_server, daemon=True)
            flask_thread.start()
            print("✅ [2/5] Flask thread ishga tushdi!")
            logger.info("✅ Flask thread started")
        except Exception as e:
            print(f"⚠️ [2/5] Flask xato: {e}")
            logger.warning(f"⚠️ Flask: {e}")
        
        # Test temp directory writable
        print("[3/5] Temp directory tekshirilmoqda...")
        try:
            test_file = os.path.join(TEMP_DIR, "startup_test.tmp")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
            print("✅ [3/5] Temp directory OK!")
            logger.info("✅ Temp directory OK")
        except Exception as e:
            print(f"⚠️ [3/5] Temp xato: {e}")
            logger.error(f"❌ Temp: {e}")
        
        print("[4/5] Background tasks ishga tushirilmoqda...")
        try:
            asyncio.create_task(auto_post_loop())
            logger.info("✅ Auto-post task started")
        except Exception as e:
            logger.error(f"❌ Auto-post: {e}")
            
        try:
            asyncio.create_task(video_queue_worker())
            logger.info("✅ Video queue worker started")
        except Exception as e:
            logger.error(f"❌ Video queue: {e}")
            
        try:
            asyncio.create_task(periodic_temp_cleanup())
            logger.info("✅ Cleanup task started")
        except Exception as e:
            logger.error(f"❌ Cleanup: {e}")
        print("✅ [4/5] Background tasks ishga tushdi!")
        
        print("✅ [5/5] BOT TELEGRAM BILAN BOG'LANDI!")
        logger.info("=" * 50)
        logger.info("🔥 BOT IS READY - STARTING POLLING")
        
        # Start bot polling - LAST STEP
        print("🔥 BOT POLLING BOSHLANDI")
        sys.stdout.flush()  # Force Render to show logs immediately
        while True:
            try:
                print("🔥 Polling ishlamoqda... xabar kutilmoqda")
                await bot.infinity_polling(skip_pending=True, timeout=60, request_timeout=60)
            except Exception as e:
                logger.error(f"❌ Polling error: {e}")
                print(f"⚠️ Polling xato: {e}")
                await asyncio.sleep(5)
                
    except Exception as e:
        logger.critical(f"💥 FATAL STARTUP ERROR: {e}")
        logger.critical(f"💥 Error type: {type(e).__name__}")
        import traceback
        logger.critical(f"💥 Traceback:\n{traceback.format_exc()}")
        raise

if __name__ == "__main__":
    # IMMEDIATE PRINT for Render visibility - before any imports might fail
    print("=" * 60)
    print("🔥 BOT STARTING - Safe Startup Wrapper")
    print("=" * 60)
    
    try:
        import traceback
        main()
        print("✅ Bot finished normally")
    except KeyboardInterrupt:
        print("👋 Bot stopped by user")
    except Exception as e:
        print("=" * 60)
        print(f"💥 UNHANDLED FATAL ERROR: {e}")
        print(f"💥 Error type: {type(e).__name__}")
        print(f"💥 Full traceback:")
        print(traceback.format_exc())
        print("=" * 60)
        # Exit with error code so Render knows it failed
        import sys
        sys.exit(1)
