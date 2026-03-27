from flask import Flask
from threading import Thread
import telebot
from telebot import types
import os, time, requests, base64, yt_dlp, asyncio
import edge_tts
from concurrent.futures import ThreadPoolExecutor

# ================= WEB =================
app = Flask('')

@app.route('/')
def home():
    return "Bot ishlayapti"

def run_web():
    app.run(host='0.0.0.0', port=10000)

Thread(target=run_web).start()

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
CHANNEL = int(os.getenv("CHANNEL_ID"))

bot = telebot.TeleBot(BOT_TOKEN)
executor = ThreadPoolExecutor(max_workers=20)

# ================= DATA =================
user_state = {}
user_voice = {}

# ================= MENU =================
def main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🎤 Text → Voice","🎬 Video → MP3")
    kb.add("🎧 Music","🔵 Circle Video")
    return kb

def voice_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("👨 Erkak","👩 Ayol")
    kb.add("🔙 Orqaga")
    return kb

def admin_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📢 Broadcast","📊 Stat")
    kb.add("🔙 Orqaga")
    return kb

# ================= SUB =================
def check_sub(uid):
    if uid == OWNER_ID:
        return True
    try:
        m = bot.get_chat_member(CHANNEL, uid)
        return m.status in ["member","administrator","creator"]
    except:
        return False

# ================= SPOTIFY =================
def get_token():
    cid = os.getenv("SPOTIFY_CLIENT_ID")
    secret = os.getenv("SPOTIFY_CLIENT_SECRET")

    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()

    headers = {"Authorization": f"Basic {auth}"}
    data = {"grant_type": "client_credentials"}

    res = requests.post("https://accounts.spotify.com/api/token", headers=headers, data=data)

    if res.status_code != 200:
        print(res.text)
        return None

    return res.json().get("access_token")

def search_music(query):
    token = get_token()
    if not token:
        return None

    headers = {"Authorization": f"Bearer {token}"}
    params = {"q": query, "type": "track", "limit": 1}

    res = requests.get("https://api.spotify.com/v1/search", headers=headers, params=params)

    if res.status_code != 200:
        print(res.text)
        return None

    data = res.json()

    if not data.get("tracks") or not data["tracks"]["items"]:
        return None

    t = data["tracks"]["items"][0]

    return {
        "title": t["name"],
        "artist": t["artists"][0]["name"],
        "image": t["album"]["images"][0]["url"]
    }

# ================= YOUTUBE MP3 =================
def download_mp3(cid, query):
    try:
        ydl_opts = {
            'format': 'bestaudio',
            'quiet': True,
            'noplaylist': True,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3'
            }]
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=True)
            file = ydl.prepare_filename(info['entries'][0]).rsplit(".",1)[0] + ".mp3"

        with open(file,"rb") as f:
            bot.send_audio(cid, f)

        os.remove(file)

    except Exception as e:
        bot.send_message(cid, f"❌ {e}")

# ================= VOICE =================
def make_voice(cid, text, voice):
    file = f"{cid}.mp3"

    async def run():
        com = edge_tts.Communicate(text=text, voice=voice)
        await com.save(file)

    asyncio.run(run())

    with open(file,"rb") as f:
        bot.send_voice(cid, f)

    os.remove(file)

# ================= START =================
@bot.message_handler(commands=['start'])
def start(m):
    if not check_sub(m.from_user.id):
        bot.send_message(m.chat.id,"❗ Kanalga obuna bo‘l")
        return

    bot.send_message(m.chat.id,"🔥 Xush kelibsiz",reply_markup=main_menu())

# ================= ADMIN =================
@bot.message_handler(commands=['admin'])
def admin(m):
    if m.from_user.id == OWNER_ID:
        user_state[m.chat.id] = "admin"
        bot.send_message(m.chat.id,"⚙️ Admin",reply_markup=admin_menu())

# ================= TEXT =================
@bot.message_handler(content_types=['text'])
def text(m):
    cid = m.chat.id
    txt = m.text
    state = user_state.get(cid)

    if not check_sub(m.from_user.id):
        bot.send_message(cid,"❗ Obuna")
        return

    if txt == "🔙 Orqaga":
        user_state[cid] = None
        bot.send_message(cid,"Menu",reply_markup=main_menu())
        return

    # ===== VOICE =====
    if txt == "🎤 Text → Voice":
        user_state[cid] = "voice"
        bot.send_message(cid,"Tanla",reply_markup=voice_menu())
        return

    if txt in ["👨 Erkak","👩 Ayol"]:
        user_voice[cid] = "male" if "Erkak" in txt else "female"
        user_state[cid] = "tts"
        bot.send_message(cid,"Matn yoz")
        return

    if state == "tts":
        voice = "uz-UZ-SardorNeural" if user_voice[cid]=="male" else "uz-UZ-MadinaNeural"
        executor.submit(make_voice, cid, txt, voice)
        return

    # ===== MUSIC =====
    if txt == "🎧 Music":
        user_state[cid] = "music"
        bot.send_message(cid,"Qo‘shiq nomi yoz")
        return

    if state == "music":
        data = search_music(txt)

        if not data:
            bot.send_message(cid,"❌ Topilmadi")
            return

        bot.send_photo(cid, data["image"], caption=f"🎵 {data['title']}\n👤 {data['artist']}")

        executor.submit(download_mp3, cid, f"{data['title']} {data['artist']}")
        return

    # ===== VIDEO MP3 =====
    if txt == "🎬 Video → MP3":
        user_state[cid] = "mp3"
        bot.send_message(cid,"Video yubor")
        return

    if txt == "🔵 Circle Video":
        user_state[cid] = "circle"
        bot.send_message(cid,"Video yubor")
        return

# ================= VIDEO =================
@bot.message_handler(content_types=['video'])
def video(m):
    cid = m.chat.id
    state = user_state.get(cid)

    file = bot.get_file(m.video.file_id)
    data = bot.download_file(file.file_path)

    inp = f"{cid}.mp4"
    open(inp,"wb").write(data)

    try:
        if state == "mp3":
            out = inp.replace(".mp4",".mp3")
            os.system(f"ffmpeg -i {inp} {out}")

            with open(out,"rb") as f:
                bot.send_audio(cid, f)

            os.remove(out)

        elif state == "circle":
            bot.send_video_note(cid, open(inp,"rb"))

    except Exception as e:
        bot.send_message(cid,f"❌ {e}")

    os.remove(inp)

# ================= RUN =================
print("🔥 BOT ISHLAYAPTI...")
bot.infinity_polling(skip_pending=True)
