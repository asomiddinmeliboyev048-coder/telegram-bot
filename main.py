from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Bot ishlayapti"

def run_web():
    app.run(host='0.0.0.0', port=10000)

Thread(target=run_web).start()
import telebot
from telebot import types
import os, subprocess, asyncio, yt_dlp, time
import edge_tts
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = telebot.TeleBot(BOT_TOKEN)

executor = ThreadPoolExecutor(max_workers=20)
user_busy = {}

def run_async(func, *args):
    executor.submit(func, *args)

CHANNEL = -1003877967882
OWNER_ID = 7171330738

user_state = {}
user_voice = {}
user_music_cache = {}

# ================= USERS =================
def save_user(uid):
    if not os.path.exists("users.txt"):
        open("users.txt","w").close()
    users = open("users.txt").read().splitlines()
    if str(uid) not in users:
        open("users.txt","a").write(str(uid)+"\n")

def get_users():
    if not os.path.exists("users.txt"):
        return []
    return open("users.txt").read().splitlines()

# ================= SUB =================
def check_sub(user_id):
    if user_id == OWNER_ID:
        return True
    try:
        m = bot.get_chat_member(CHANNEL, user_id)
        return m.status in ["member","creator","administrator"]
    except:
        return False

def sub_kb():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📢 Obuna", url="https://t.me/meliboyevdev"))
    kb.add(types.InlineKeyboardButton("✅ Tekshirish", callback_data="check"))
    return kb

# ================= MENU =================
def main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🎤 Text → Voice","🎬 Video → MP3")
    kb.add("🎧 Search Music","🔵 Circle Video")
    return kb

def voice_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("👨 Erkak ovoz","👩 Ayol ovoz")
    kb.add("🔙 Orqaga")
    return kb

def admin_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📢 Broadcast","📊 Statistika")
    kb.add("📣 Auto Post")
    kb.add("🔙 Orqaga")
    return kb

# ================= MUSIC =================
def download_music(cid, url):
    try:
        base = f"{cid}_{int(time.time())}"

        ydl_opts = {
            'format': 'bestaudio',
            'outtmpl': f'{base}.%(ext)s',
            'quiet': True,
            'noplaylist': True,

            # ✅ FULL FIX (WARNING YO‘Q)
            'js_runtimes': {'node': {'path': 'node'}},
            'remote_components': ['ejs:github'],

            'postprocessors': [{
                'key':'FFmpegExtractAudio',
                'preferredcodec':'mp3',
                'preferredquality':'96'
            }]
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title","Music")
            artist = info.get("uploader","Unknown")

            file = ydl.prepare_filename(info).rsplit(".",1)[0] + ".mp3"

        with open(file,"rb") as f:
            bot.send_audio(cid, f, title=f"🎵 {title}", performer=artist)

        os.remove(file)

    except Exception as e:
        bot.send_message(cid,f"❌ {e}")

# ================= VOICE =================
def generate_voice(cid, txt, voice):
    try:
        file = f"{cid}_{int(time.time())}.mp3"

        async def run():
            communicate = edge_tts.Communicate(text=txt, voice=voice)
            await communicate.save(file)

        asyncio.run(run())

        with open(file,"rb") as f:
            bot.send_voice(cid, f)

        os.remove(file)

    except Exception as e:
        bot.send_message(cid,f"❌ {e}")

# ================= START =================
@bot.message_handler(commands=['start'])
def start(m):
    save_user(m.chat.id)
    user_state[m.chat.id] = None

    if not check_sub(m.from_user.id):
        bot.send_message(m.chat.id,"❗ Obuna bo‘ling",reply_markup=sub_kb())
        return

    bot.send_message(m.chat.id,"🔥 Xush kelibsiz",reply_markup=main_menu())

# ================= ADMIN =================
@bot.message_handler(commands=['admin'])
def admin(m):
    if m.from_user.id == OWNER_ID:
        user_state[m.chat.id] = "admin"
        bot.send_message(m.chat.id,"⚙️ Admin panel",reply_markup=admin_menu())

AUTO_POST_TEXT = None

# ================= TEXT =================
@bot.message_handler(content_types=['text'])
def text(m):
    cid = m.chat.id
    txt = m.text
    state = user_state.get(cid)

    if not check_sub(m.from_user.id):
        bot.send_message(cid,"❗ Obuna",reply_markup=sub_kb())
        return

    # 🔙 ORQAGA
    if txt == "🔙 Orqaga":
        user_state[cid] = None
        bot.send_message(cid,"Menu",reply_markup=main_menu())
        return

    # ===== ADMIN ISOLATED =====
    if state == "admin":
        if txt == "📊 Statistika":
            bot.send_message(cid,f"👥 {len(get_users())}")
            return

        if txt == "📢 Broadcast":
            user_state[cid] = "broadcast"
            bot.send_message(cid,"Post yubor")
            return

        if txt == "📣 Auto Post":
            user_state[cid] = "autopost"
            bot.send_message(cid,"Post yubor")
            return

    if state == "broadcast":
        for u in get_users():
            try:
                bot.copy_message(u, cid, m.message_id)
            except:
                pass
        bot.send_message(cid,"✅ Yuborildi")
        user_state[cid] = "admin"
        return

    if state == "autopost":
        global AUTO_POST_TEXT
        AUTO_POST_TEXT = txt
        bot.send_message(cid,"✅ Saqlandi")
        user_state[cid] = "admin"
        return

    # ===== VOICE =====
    if txt == "🎤 Text → Voice":
        user_state[cid] = "voice"
        bot.send_message(cid,"Tanla",reply_markup=voice_menu())
        return

    if txt in ["👨 Erkak ovoz","👩 Ayol ovoz"]:
        user_voice[cid] = "male" if "Erkak" in txt else "female"
        user_state[cid] = "tts"
        bot.send_message(cid,"Matn yoz")
        return

    if state == "tts":
        voice = "uz-UZ-SardorNeural" if user_voice.get(cid)=="male" else "uz-UZ-MadinaNeural"
        run_async(generate_voice, cid, txt, voice)
        return

    # ===== MODES =====
    if txt == "🎬 Video → MP3":
        user_state[cid] = "mp3"
        bot.send_message(cid,"Video yubor")
        return

    if txt == "🔵 Circle Video":
        user_state[cid] = "circle"
        bot.send_message(cid,"Video yubor")
        return

    if txt == "🎧 Search Music":
        user_state[cid] = "music"
        bot.send_message(cid,"Qo‘shiq nomi")
        return

    if state == "music":
        try:
            msg = bot.send_message(cid,"🔍 Qidiryapman...")

            with yt_dlp.YoutubeDL({'quiet':True}) as ydl:
                info = ydl.extract_info(f"ytsearch1:{txt}", download=False)
                entry = info['entries'][0]

            url = f"https://youtube.com/watch?v={entry['id']}"
            user_music_cache[cid] = url

            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("⬇️ Yuklash", callback_data="dl_music"))

            bot.edit_message_text(entry['title'], cid, msg.message_id, reply_markup=kb)

        except Exception as e:
            bot.send_message(cid,f"❌ {e}")

# ================= CALLBACK =================
@bot.callback_query_handler(func=lambda c: True)
def callbacks(c):
    cid = c.message.chat.id

    if c.data == "check":
        if check_sub(c.from_user.id):
            bot.send_message(cid,"✅ OK",reply_markup=main_menu())

    elif c.data == "dl_music":
        url = user_music_cache.get(cid)
        if url:
            run_async(download_music, cid, url)

# ================= VIDEO =================
@bot.message_handler(content_types=['video'])
def video(m):
    cid = m.chat.id
    state = user_state.get(cid)

    file = bot.get_file(m.video.file_id)
    data = bot.download_file(file.file_path)

    inp = f"{cid}_{int(time.time())}.mp4"
    open(inp,"wb").write(data)

    try:
        if state == "mp3":
            out = inp.replace(".mp4",".mp3")

            with open(out,"rb") as f:
                bot.send_audio(cid, f, title="🎵 Video Audio", performer="Converted")

            os.remove(out)

        elif state == "circle":
            out = inp.replace(".mp4","_c.mp4")
            bot.send_video_note(cid, open(out,"rb"))
            os.remove(out)

    except Exception as e:
        bot.send_message(cid,f"❌ {e}")

    os.remove(inp)

# ================= RUN =================
while True:
    try:
        print("🔥 ISHLAYAPTI...")
        bot.infinity_polling(skip_pending=True)
    except Exception as e:
        print("XATO:", e)
        time.sleep(5)