# ================= IMMEDIATE DEBUG =================
print("DEBUG: Kod o'qilmoqda...")

# ================= IMPORTS =================
import asyncio
import os
import subprocess
import logging
import tempfile
import time
import sys
from pathlib import Path
from functools import wraps

from dotenv import load_dotenv
from telebot import types
from telebot.async_telebot import AsyncTeleBot

# Lazy imports - loaded only when needed
yt_dlp = None
edge_tts = None

# ================= FAST STARTUP =================
load_dotenv()
print("DEBUG: Environment yuklandi...")

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")

CHANNEL = -1003877967882
OWNER_ID = 7171330738
CIRCLE_SIZE = 320

# Use Render disk or system temp
TEMP_DIR = os.getenv("RENDER_DISK_PATH", tempfile.gettempdir())
logger.info(f"Using temp dir: {TEMP_DIR}")

# ================= BOT INSTANCE (GLOBAL) =================
# Bot faqat bir marta, global darajada yaratiladi
bot = AsyncTeleBot(BOT_TOKEN) if BOT_TOKEN else None

# ================= SEMAPHORES =================
video_semaphore = asyncio.Semaphore(2)
music_semaphore = asyncio.Semaphore(5)
circle_semaphore = asyncio.Semaphore(1)

# Video processing queue
video_queue = asyncio.Queue(maxsize=10)

# ================= STATE =================
user_state = {}
user_voice = {}
active_tasks = {}

# ================= LAZY LOAD =================
def lazy_load_modules():
    global yt_dlp, edge_tts
    if yt_dlp is None:
        import yt_dlp as yd
        yt_dlp = yd
    if edge_tts is None:
        import edge_tts as et
        edge_tts = et
    return yt_dlp, edge_tts

# ================= ASYNC HELPER =================
async def run_in_thread(func, *args, **kwargs):
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    except AttributeError:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

# ================= CLEANUP =================
async def periodic_temp_cleanup():
    while True:
        try:
            await asyncio.sleep(300)
            cleaned = 0
            total_size = 0
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
            if cleaned > 0:
                logger.info(f"Cleaned {cleaned} old temp files")
            if total_size > 100 * 1024 * 1024:
                logger.warning(f"Temp dir large: {total_size / 1024 / 1024:.1f}MB")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

async def background_cleanup(file_path, delay=5):
    await asyncio.sleep(delay)
    safe_remove(file_path)

def cleanup_temp_files(cid):
    patterns = [f"{cid}.*", f"{cid}_*", f"music_{cid}_*"]
    for pattern in patterns:
        for file in Path(TEMP_DIR).glob(pattern):
            try:
                file.unlink()
            except Exception:
                pass

def safe_remove(filepath):
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
    except Exception:
        pass

# ================= FLASK HEALTH CHECK =================
def run_flask_server():
    try:
        from flask import Flask
        from threading import Thread
        app = Flask(__name__)

        @app.route('/')
        def home():
            return "Bot is running!"

        port = int(os.environ.get('PORT', 10000))

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

# ================= SUBSCRIPTION CHECK =================
async def check_subscription(user_id):
    if user_id == OWNER_ID:
        return True
    try:
        member = await bot.get_chat_member(CHANNEL, user_id)
        return member.status in ["member", "creator", "administrator"]
    except Exception as e:
        logger.error(f"Subscription check error: {e}")
        return False

def require_subscription(handler):
    @wraps(handler)
    async def wrapper(message, *args, **kwargs):
        user_id = message.from_user.id
        cid = message.chat.id
        is_subscribed = await check_subscription(user_id)
        if not is_subscribed:
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("📢 Obuna bo'lish", url="https://t.me/meliboyevdev"))
            kb.add(types.InlineKeyboardButton("✅ Tekshirish", callback_data="check"))
            await bot.send_message(cid, "❗️ Kanalga a'zo bo'ling:", reply_markup=kb)
            return
        return await handler(message, *args, **kwargs)
    return wrapper

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

def admin_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📢 Broadcast", "📊 Statistika")
    kb.add("📣 Auto Post")
    kb.add("🔙 Orqaga")
    return kb

# ================= SUBSCRIPTION CALLBACK =================
@bot.callback_query_handler(func=lambda c: c.data == "check")
async def check_callback(call):
    user_id = call.from_user.id
    cid = call.message.chat.id
    is_subscribed = await check_subscription(user_id)
    if is_subscribed:
        await bot.edit_message_text("✅ Obuna tasdiqlandi!", cid, call.message.message_id)
        await bot.send_message(cid, "🔥 BOTGA XUSH KELIBSIZ", reply_markup=main_menu())
    else:
        await bot.answer_callback_query(call.id, "❌ Avval kanalga obuna bo'ling!", show_alert=True)

# ================= START =================
@bot.message_handler(commands=['start'])
async def start(m):
    try:
        print(f"DEBUG: /start received from {m.chat.id}")
        cid = m.chat.id
        user_id = m.from_user.id
        save_user(user_id)

        is_subscribed = await check_subscription(user_id)
        if not is_subscribed:
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("📢 Obuna bo'lish", url="https://t.me/meliboyevdev"))
            kb.add(types.InlineKeyboardButton("✅ Tekshirish", callback_data="check"))
            await bot.send_message(cid, "❗️ Kanalga a'zo bo'ling:", reply_markup=kb)
            return

        await bot.send_message(cid, "🔥 BOTGA XUSH KELIBSIZ!", reply_markup=main_menu())
        print(f"DEBUG: /start reply sent to {m.chat.id}")
    except Exception as e:
        print(f"ERROR in start handler: {e}")
        logger.error(f"Start error: {e}")

# ================= ADMIN =================
@bot.message_handler(commands=['admin'])
async def admin(m):
    try:
        if m.from_user.id == OWNER_ID:
            await bot.send_message(m.chat.id, "⚙️ Admin panel", reply_markup=admin_menu())
    except Exception as e:
        logger.error(f"Admin error: {e}")

AUTO_POST_TEXT = None

# ================= TEXT HANDLER =================
@bot.message_handler(content_types=['text'])
async def text_handler(m):
    cid = m.chat.id
    txt = m.text
    logger.info(f"📩 [MESSAGE] User: {cid}, Text: {txt[:100]}")
    user_id = m.from_user.id
    state = user_state.get(cid)

    is_subscribed = await check_subscription(user_id)
    if not is_subscribed:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📢 Obuna bo'lish", url="https://t.me/meliboyevdev"))
        kb.add(types.InlineKeyboardButton("✅ Tekshirish", callback_data="check"))
        await bot.send_message(cid, "❗️ Kanalga a'zo bo'ling:", reply_markup=kb)
        return

    try:
        # Orqaga tugmalari
        if txt in ["⬅️ Orqaga", "🔙 Orqaga"]:
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
            await bot.send_message(cid, "🎵 Qo'shiq nomi yoki ijrochi yozing:\n(Masalan: 'Eminem Lose Yourself')")
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

# ================= TTS =================
async def handle_tts(m):
    lazy_load_modules()
    cid = m.chat.id
    txt = m.text
    voice_type = user_voice.get(cid, "female")
    input_path = None
    output_path = None
    MAX_CAPTION = 900

    try:
        if voice_type == "funny":
            voice = "uz-UZ-MadinaNeural"
        elif voice_type == "venom":
            voice = "uz-UZ-SardorNeural"
        else:
            voice = "uz-UZ-SardorNeural" if voice_type == "male" else "uz-UZ-MadinaNeural"

        input_path = os.path.join(TEMP_DIR, f"{cid}_tts_input.mp3")
        output_path = os.path.join(TEMP_DIR, f"{cid}_tts_output.mp3")
        caption_text = txt if len(txt) <= 100 else txt[:100] + "..."

        msg = await bot.send_message(cid, "🎙️ Ovoz yaratilmoqda...")
        communicate = edge_tts.Communicate(text=txt, voice=voice)
        await communicate.save(input_path)

        if not os.path.exists(input_path) or os.path.getsize(input_path) < 100:
            await bot.edit_message_text("❌ Audio yaratilmadi. Iltimos, qisqaroq matn yuboring.", cid, msg.message_id)
            return

        if voice_type == "venom":
            await bot.edit_message_text("🎭 Venom ovoz effekti qo'llanilmoqda...", cid, msg.message_id)
            cmd_venom = [
                "ffmpeg", "-y", "-i", input_path,
                "-af", "bass=g=12,asetrate=44100*0.5,atempo=2.0,aecho=0.8:0.88:60:0.4",
                "-ar", "44100", "-ac", "1", output_path
            ]
            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, lambda: subprocess.run(cmd_venom, capture_output=True, text=True, timeout=30)
                )
                file_to_send = output_path if (result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0) else input_path
            except Exception as e:
                logger.error(f"Venom voice error: {e}")
                file_to_send = input_path

        elif voice_type == "funny":
            await bot.edit_message_text("🤡 Kulgili ovoz effekti qo'llanilmoqda...", cid, msg.message_id)
            cmd_rubberband = ["ffmpeg", "-y", "-i", input_path, "-af", "rubberband=pitch=1.5", "-ar", "44100", "-ac", "1", output_path]
            cmd_asetrate = ["ffmpeg", "-y", "-i", input_path, "-af", "asetrate=44100*1.4,atempo=1/1.4", "-ar", "44100", "-ac", "1", output_path]
            file_to_send = input_path
            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, lambda: subprocess.run(cmd_rubberband, capture_output=True, text=True, timeout=30))
                if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    file_to_send = output_path
                else:
                    result2 = await loop.run_in_executor(None, lambda: subprocess.run(cmd_asetrate, capture_output=True, text=True, timeout=30))
                    if result2.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                        file_to_send = output_path
            except Exception as e:
                logger.error(f"Funny voice error: {e}")
        else:
            file_to_send = input_path

        await bot.edit_message_text("📤 Yuborilmoqda...", cid, msg.message_id)
        voice_emoji = "🤡" if voice_type == "funny" else "🎭" if voice_type == "venom" else "🎙️"
        caption = f"{voice_emoji} {caption_text}\n✅ @foyda1ii_bot"
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

# ================= BROADCAST =================
async def handle_broadcast(m):
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
                await asyncio.sleep(0.05)
            except Exception as e:
                failed += 1
                logger.error(f"Broadcast failed for {u}: {e}")
        await bot.send_message(cid, f"✅ Yuborildi: {success}\n❌ Xatolik: {failed}")
        user_state[cid] = None
    except Exception as e:
        logger.error(f"Broadcast error: {e}")
        await bot.send_message(cid, f"❌ Xatolik: {str(e)[:100]}", reply_markup=main_menu())

# ================= AUTO POST =================
async def handle_autopost(m):
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

# ================= MUSIC =================
_search_cache = {}
_CACHE_TTL = 300
_audio_cache = {}
_AUDIO_CACHE_TTL = 600
_audio_cache_lock = asyncio.Lock()

def get_cached_search(query):
    if query in _search_cache:
        result, timestamp = _search_cache[query]
        if time.time() - timestamp < _CACHE_TTL:
            return result
        del _search_cache[query]
    return None

def cache_search(query, results):
    _search_cache[query] = (results, time.time())

async def get_cached_audio_file(url):
    async with _audio_cache_lock:
        if url in _audio_cache:
            file_path, timestamp = _audio_cache[url]
            if time.time() - timestamp < _AUDIO_CACHE_TTL:
                if os.path.exists(file_path):
                    return file_path
            del _audio_cache[url]
    return None

async def cache_audio_file(url, file_path):
    async with _audio_cache_lock:
        _audio_cache[url] = (file_path, time.time())

def format_duration(seconds):
    if not seconds:
        return "0:00"
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes}:{secs:02d}"

async def update_download_progress(cid, msg_id, progress_data, stop_event):
    last_percent = -1
    while not stop_event.is_set():
        try:
            percent = progress_data.get('percent', 0)
            if percent != last_percent and percent < 100:
                last_percent = percent
                status_emoji = "⬇️" if percent < 50 else "🎵"
                try:
                    await bot.edit_message_text(f"{status_emoji} Yuklanmoqda: {percent}%", cid, msg_id)
                except Exception:
                    pass
            await asyncio.sleep(2)
        except Exception:
            break

async def search_music_ultrafast(query, limit=10):
    ydl_module, _ = lazy_load_modules()
    cached = get_cached_search(query)
    if cached:
        return cached
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'default_search': 'ytsearch10',
            'extract_flat': True,
            'skip_download': True,
        }
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
                        timeout=12
                    )
                if result and 'entries' in result and result['entries']:
                    tracks = []
                    seen = set()
                    for entry in result['entries'][:limit]:
                        if not entry:
                            continue
                        vid_id = entry.get('id')
                        if vid_id in seen:
                            continue
                        seen.add(vid_id)
                        title = entry.get('title', 'Unknown Track')
                        uploader = entry.get('uploader', entry.get('channel', 'Unknown'))
                        duration = entry.get('duration', 0)
                        if duration and duration < 30:
                            continue
                        tracks.append({
                            'id': vid_id,
                            'name': title,
                            'artist': uploader,
                            'duration': int(duration) if duration else 0,
                            'url': entry.get('webpage_url', entry.get('url', '')),
                            'thumbnail': entry.get('thumbnail', ''),
                        })
                    if tracks:
                        cache_search(query, tracks)
                        return tracks
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.warning(f"Search variant failed: {e}")
                continue
        return None
    except Exception as e:
        logger.error(f"Music search error: {e}")
        return None

async def handle_music_search(m):
    cid = m.chat.id
    query = m.text.strip()
    search_msg = None
    try:
        user_state[cid] = None
        search_msg = await bot.send_message(cid, "🔍 YouTube'dan qidirilmoqda...")
        tracks = await search_music_ultrafast(query, limit=10)
        if not tracks or len(tracks) == 0:
            await bot.edit_message_text("❌ Topilmadi", cid, search_msg.message_id)
            return

        result_text = f"🎵 *Topilgan natijalar:* `{query}`\n\n*Quyidagi qo'shiqlardan birini tanlang:*\n\n"
        for i, track in enumerate(tracks[:10], 1):
            artist = str(track.get('artist', 'Unknown'))
            name = str(track.get('name', 'Unknown'))
            duration = format_duration(track.get('duration', 0))
            result_text += f"{i}. {artist} - {name} ({duration})\n"

        keyboard_rows = []
        track_count = min(len(tracks), 10)
        if track_count >= 1:
            row1 = [types.InlineKeyboardButton(str(i), callback_data=f"yt_{tracks[i-1]['id']}") for i in range(1, min(6, track_count + 1))]
            keyboard_rows.append(row1)
        if track_count >= 6:
            row2 = [types.InlineKeyboardButton(str(i), callback_data=f"yt_{tracks[i-1]['id']}") for i in range(6, track_count + 1)]
            keyboard_rows.append(row2)

        markup = types.InlineKeyboardMarkup(keyboard_rows)
        user_state[str(cid) + '_tracks'] = {t['id']: t for t in tracks[:10]}

        await bot.edit_message_text(result_text, cid, search_msg.message_id, parse_mode='Markdown', reply_markup=markup)
    except Exception as e:
        logger.error(f"Music search error: {e}")
        try:
            if search_msg:
                await bot.edit_message_text(f"❌ Xatolik: {str(e)[:100]}", cid, search_msg.message_id, reply_markup=main_menu())
            else:
                await bot.send_message(cid, f"❌ Xatolik: {str(e)[:100]}", reply_markup=main_menu())
        except Exception:
            pass

@bot.callback_query_handler(func=lambda c: c.data.startswith("yt_"))
async def youtube_download_handler(call):
    """Tezkor yuklash: qidiruvsiz, to'g'ridan-to'g'ri YouTube ID orqali"""
    cid = call.message.chat.id
    data = call.data
    msg_id = call.message.message_id
    try:
        await bot.answer_callback_query(call.id)
        youtube_id = data[3:]  # Extract YouTube ID from "yt_VIDEO_ID"
        if not youtube_id or len(youtube_id) < 5:
            await bot.edit_message_text("❌ Noto'g'ri video ID.", cid, msg_id, reply_markup=main_menu())
            return

        # Track ma'lumotlarini user_state dan olish
        tracks_dict = user_state.get(str(cid) + '_tracks', {})
        track = tracks_dict.get(youtube_id, {'name': 'Track', 'artist': 'YouTube'})

        # Tezkor yuklash: qidiruvsiz, to'g'ridan-to'g'ri yuklash
        try:
            await bot.edit_message_text(
                f"🎵 {track.get('artist', 'Unknown')} - {track.get('name', 'Unknown')}\n\n⏳ Yuklanmoqda...",
                cid, msg_id
            )
        except Exception:
            pass

        # To'g'ridan-to'g'ri URL yaratish va yuklash (qidiruvsiz)
        url = f"https://www.youtube.com/watch?v={youtube_id}"
        await download_youtube_audio_fast(cid, youtube_id, url, msg_id, track)
    except Exception as e:
        logger.error(f"YouTube download error: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        try:
            await bot.edit_message_text(f"❌ Xatolik: {str(e)[:200]}", cid, msg_id, reply_markup=main_menu())
        except Exception:
            pass

async def download_youtube_audio_fast(cid, youtube_id, url, msg_id, track=None):
    """Tezkor yuklash: To'g'ridan-to'g'ri YouTube ID orqali, qidiruvsiz"""
    lazy_load_modules()
    file_path = None
    try:
        # Check cache first
        cache_key = youtube_id
        cached_file = await get_cached_audio_file(cache_key)
        if cached_file and os.path.exists(cached_file):
            await bot.edit_message_text("⚡ Keshdan yuborilmoqda...", cid, msg_id)
            with open(cached_file, "rb") as f:
                await bot.send_audio(
                    cid, f,
                    title=track.get('name', 'Music') if track else 'Music',
                    performer=track.get('artist', 'YouTube') if track else 'YouTube',
                    caption=f"🎵 YouTube Music\n⚡ Keshdan\n✅ @foyda1ii_bot",
                    reply_markup=main_menu()
                )
            await bot.delete_message(cid, msg_id)
            return

        # YouTube blokidan o'tish uchun headers qo'shish
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://www.google.com/',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        
        # Optimized yt_dlp settings for fast download with YouTube bypass
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(TEMP_DIR, f'{cid}_%(title)s.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,  # Faqat bitta video
            'max_filesize': 40 * 1024 * 1024,  # 40MB limit
            'socket_timeout': 30,
            'retries': 3,
            'source_address': '0.0.0.0',  # Bypass YouTube blocking
            'headers': headers,  # User-agent va referer
            'geo_bypass': True,  # Geo cheklovlarni chetlab o'tish
            'geo_bypass_country': 'US',
        }

        # Download with 60 second timeout
        info = None
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.wait_for(
                    run_in_thread(ydl.extract_info, url, download=True),
                    timeout=60
                )
        except asyncio.TimeoutError:
            error_msg = "❌ Yuklash vaqti tugadi (60 soniya)"
            logger.warning("Download timeout after 60 seconds")
            await bot.send_message(cid, error_msg, reply_markup=main_menu())
            await bot.delete_message(cid, msg_id)
            return
        except Exception as e:
            error_detail = str(e)
            error_msg = f"❌ Yuklab olishda xatolik:\n<code>{error_detail[:400]}</code>"
            logger.warning(f"Fast download failed: {e}")
            await bot.send_message(cid, error_msg, parse_mode='HTML', reply_markup=main_menu())
            await bot.delete_message(cid, msg_id)
            return

        if not info:
            await bot.edit_message_text("❌ Ma'lumot olinmadi", cid, msg_id, reply_markup=main_menu())
            return

        # Find downloaded file
        downloaded_files = list(Path(TEMP_DIR).glob(f"{cid}_*.mp3"))
        if not downloaded_files:
            downloaded_files = list(Path(TEMP_DIR).glob(f"{cid}_*.*"))
        if not downloaded_files:
            await bot.edit_message_text("❌ Fayl topilmadi", cid, msg_id, reply_markup=main_menu())
            return

        file_path = str(downloaded_files[0])
        file_size = os.path.getsize(file_path)
        if file_size == 0:
            await bot.send_message(cid, "❌ Fayl bo'sh (0 bytes)", reply_markup=main_menu())
            await bot.delete_message(cid, msg_id)
            safe_remove(file_path)
            return
        if file_size > 40 * 1024 * 1024:
            await bot.send_message(cid, "❌ Fayl juda katta (>40MB)", reply_markup=main_menu())
            await bot.delete_message(cid, msg_id)
            safe_remove(file_path)
            return

        # Cache and send
        await cache_audio_file(cache_key, file_path)
        await bot.edit_message_text("📤 Yuborilmoqda...", cid, msg_id)

        title = info.get('title', track.get('name', 'Music') if track else 'Music')
        uploader = info.get('uploader', track.get('artist', 'YouTube') if track else 'YouTube')
        duration = info.get('duration', track.get('duration', 0) if track else 0)

        with open(file_path, "rb") as f:
            await bot.send_audio(
                cid, f,
                title=title,
                performer=uploader,
                duration=duration,
                caption=f"🎵 {uploader} - {title}\n✅ @foyda1ii_bot",
                reply_markup=main_menu()
            )
        await bot.delete_message(cid, msg_id)
        safe_remove(file_path)

    except Exception as e:
        import traceback
        error_detail = str(e)
        error_trace = traceback.format_exc()
        error_msg = f"❌ Yuklash xatosi:\n<code>{error_detail[:400]}</code>"
        logger.error(f"Fast download error: {e}")
        logger.error(f"Traceback: {error_trace}")
        try:
            await bot.send_message(cid, error_msg, parse_mode='HTML', reply_markup=main_menu())
            await bot.delete_message(cid, msg_id)
        except Exception as inner_e:
            logger.error(f"Failed to send error message: {inner_e}")
        # Temp papkasini tozalash
        try:
            for f in Path(TEMP_DIR).glob(f"{cid}_*"):
                safe_remove(str(f))
        except Exception:
            pass

async def download_youtube_audio(cid, track, url, msg_id):
    """Original function - delegate to fast version"""
    youtube_id = track.get('id', '')
    if youtube_id:
        await download_youtube_audio_fast(cid, youtube_id, url, msg_id, track)
    else:
        # Fallback to direct URL
        await download_youtube_audio_fast(cid, 'unknown', url, msg_id, track)

# ================= VIDEO QUEUE WORKER =================
async def video_queue_worker():
    while True:
        try:
            task = await video_queue.get()
            cid, input_path, output_path, msg_id = task
            async with circle_semaphore:
                try:
                    await handle_circle_video(cid, input_path, output_path, msg_id)
                except Exception as e:
                    logger.error(f"Queue processing error for {cid}: {e}")
                finally:
                    video_queue.task_done()
        except Exception as e:
            logger.error(f"Queue worker error: {e}")
            await asyncio.sleep(1)

# ================= VIDEO HANDLER =================
@bot.message_handler(content_types=['video'])
async def video_handler(m):
    cid = m.chat.id
    state = user_state.get(cid)
    if state not in ["mp3", "circle"]:
        return

    input_path = None
    output_path = None
    try:
        msg = await bot.send_message(cid, "⏳ Video yuklanmoqda...")
        file_info = await bot.get_file(m.video.file_id)
        downloaded_file = await bot.download_file(file_info.file_path)
        input_path = os.path.join(TEMP_DIR, f"{cid}_input.mp4")
        with open(input_path, "wb") as f:
            f.write(downloaded_file)

        file_size = os.path.getsize(input_path)
        if file_size > 100 * 1024 * 1024:
            await bot.edit_message_text("❌ Video hajmi juda katta (>100MB).", cid, msg.message_id)
            safe_remove(input_path)
            return

        if state == "mp3":
            output_path = os.path.join(TEMP_DIR, f"{cid}_output.mp3")
            await bot.edit_message_text("⏳ Audio ajratilmoqda...", cid, msg.message_id)
            async with video_semaphore:
                try:
                    await handle_video_to_mp3(cid, input_path, output_path, msg.message_id)
                except Exception as e:
                    logger.error(f"MP3 error: {e}")
                    await bot.edit_message_text(f"❌ Xatolik: {str(e)[:100]}", cid, msg.message_id)

        elif state == "circle":
            output_path = os.path.join(TEMP_DIR, f"{cid}_circle.mp4")
            await bot.edit_message_text("⏳ Yumaloq video yaratilmoqda...", cid, msg.message_id)
            await video_queue.put((cid, input_path, output_path, msg.message_id))

        user_state[cid] = None
    except Exception as e:
        logger.error(f"Video processing error for user {cid}: {e}")
        try:
            await bot.send_message(cid, f"❌ Xatolik: {str(e)[:100]}", reply_markup=main_menu())
        except Exception:
            pass
        if input_path:
            safe_remove(input_path)
        if output_path:
            safe_remove(output_path)

async def run_ffmpeg_async(cmd):
    return await run_in_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=300)

async def handle_video_to_mp3(cid, input_path, output_path, msg_id):
    try:
        await bot.edit_message_text("⚡ MP3 yaratilmoqda...", cid, msg_id)
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", input_path, "-vn", "-acodec", "libmp3lame",
            "-q:a", "4", "-threads", "0", output_path
        ]
        result = await run_ffmpeg_async(cmd)
        if result.returncode != 0:
            logger.error(f"FFmpeg error: {result.stderr}")
            await bot.edit_message_text("❌ Konvertatsiyada xatolik.", cid, msg_id)
            return
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            await bot.edit_message_text("❌ Audio fayl yaratilmadi.", cid, msg_id)
            return
        await bot.edit_message_text("📤 Yuborilmoqda...", cid, msg_id)
        with open(output_path, "rb") as f:
            await bot.send_audio(cid, f, title="Video Audio", performer="@foyda1ii_bot",
                                 caption="✅ Video dan MP3\n✅ @foyda1ii_bot", reply_markup=main_menu())
        await bot.delete_message(cid, msg_id)
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
    try:
        result = await run_in_thread(subprocess.run, ["ffmpeg", "-version"], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False

async def handle_circle_video(cid, input_path, output_path, msg_id):
    try:
        ffmpeg_installed = await check_ffmpeg_installed()
        if not ffmpeg_installed:
            await bot.edit_message_text("❌ FFmpeg o'rnatilmagan.", cid, msg_id)
            return
        await bot.edit_message_text("⚡ Video ishlanmoqda...", cid, msg_id)
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", input_path,
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-threads", "0", "-crf", "28", "-s", "320x320", "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-t", "60", "-movflags", "+faststart", output_path
        ]
        result = await run_ffmpeg_async(cmd)
        if result.returncode != 0:
            cmd_audio = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", input_path,
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-threads", "0", "-crf", "28", "-s", "320x320", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "96k", "-t", "60", "-movflags", "+faststart", output_path
            ]
            result = await run_ffmpeg_async(cmd_audio)

        if result.returncode != 0:
            await bot.edit_message_text("❌ Video konvertatsiyada xatolik.", cid, msg_id)
            safe_remove(input_path)
            return
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            await bot.edit_message_text("❌ Video fayl yaratilmadi.", cid, msg_id)
            safe_remove(input_path)
            return

        await bot.edit_message_text("📤 Yuborilmoqda...", cid, msg_id)
        with open(output_path, "rb") as f:
            await bot.send_video_note(cid, f, length=CIRCLE_SIZE, reply_markup=main_menu())
        await bot.delete_message(cid, msg_id)
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

# ================= AUTO POST LOOP =================
async def auto_post_loop():
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

# ================= MAIN =================
async def main():
    if not BOT_TOKEN:
        print("💥 FATAL ERROR: BOT_TOKEN environment variable is required!")
        raise ValueError("BOT_TOKEN environment variable is required!")

    print(f"DEBUG: Token tekshirildi: {BOT_TOKEN[:10]}...")
    logger.info("🚀 Starting bot on Render...")
    logger.info(f"📁 Temp dir: {TEMP_DIR}")

    # 1. Remove webhook
    print("[1/4] Webhook tozalanyapti...")
    try:
        await bot.remove_webhook(drop_pending_updates=True)
        print("✅ [1/4] Webhook tozalandi!")
    except Exception as e:
        print(f"⚠️ [1/4] Webhook xato: {e}")

    # 2. Flask server
    print("[2/4] Flask server ishga tushirilmoqda...")
    run_flask_server()
    print("✅ [2/4] Flask ishga tushdi!")

    # 3. Temp dir check
    print("[3/4] Temp directory tekshirilmoqda...")
    try:
        test_file = os.path.join(TEMP_DIR, "startup_test.tmp")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        print("✅ [3/4] Temp directory OK!")
    except Exception as e:
        print(f"⚠️ [3/4] Temp xato: {e}")

    # 4. Background tasks
    print("[4/4] Background tasks ishga tushirilmoqda...")
    asyncio.create_task(auto_post_loop())
    asyncio.create_task(video_queue_worker())
    asyncio.create_task(periodic_temp_cleanup())
    print("✅ [4/4] Background tasks tayyor!")

    print("🔥 BOT POLLING BOSHLANDI")
    sys.stdout.flush()

    while True:
        try:
            await bot.infinity_polling(skip_pending=True, timeout=60, request_timeout=60)
        except Exception as e:
            logger.error(f"❌ Polling error: {e}")
            print(f"⚠️ Polling xato: {e}, 5 soniyadan keyin qayta uriniladi...")
            await asyncio.sleep(5)

# ================= ENTRY POINT =================
if __name__ == "__main__":
    print("=" * 60)
    print("🔥 BOT STARTING")
    print("=" * 60)
    try:
        asyncio.run(main())  # ← TO'G'RI USUL
    except KeyboardInterrupt:
        print("👋 Bot stopped by user")
    except Exception as e:
        import traceback
        print(f"💥 FATAL: {e}")
        print(traceback.format_exc())
        sys.exit(1)
