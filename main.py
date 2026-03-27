# ================= WEB KEEP ALIVE =================
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Bot ishlayapti"

def run_web():
    app.run(host='0.0.0.0', port=10000)

Thread(target=run_web).start()

# ================= IMPORT =================
import telebot
from telebot import types
import os, yt_dlp, time, subprocess, requests, base64, asyncio
import edge_tts
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

bot = telebot.TeleBot(BOT_TOKEN)

# ================= CONFIG =================
OWNER_ID = 7171330738
CHANNEL = -1003877967882

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
    kb.add("🔙 Orqaga")
    return kb

def voice_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("👨 Erkak ovoz","👩 Ayol ovoz")
    kb.add("🔙 Orqaga")
    return kb

def admin_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📢 Broadcast","📊 Statistika")
    kb.add("🔙 Orqaga")
    return kb

# ================= SPOTIFY =================
def get_spotify_token():
    auth = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}
    data = {"grant_type": "client_credentials"}
    r = requests.post("https://accounts.spotify.com/api/token", headers=headers, data=data)
    return r.json().get("access_token")

def search_spotify(query):
    token = get_spotify_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://api.spotify.com/v1/search?q={query}&type=track&limit=1"
    r = requests.get(url, headers=headers).json()

    track = r["tracks"]["items"][0]
    return {
        "title": track["name"],
        "artist": track["artists"][0]["name"],
        "image": track["album"]["images"][0]["url"],
        "duration": track["duration_ms"] // 1000
    }

# ================= VOICE =================
async def tts(text, file, voice):
    communicate = edge_tts.Communicate(text=text, voice=voice)
    await communicate.save(file)

# ================= START =================
@bot.message_handler(commands=['start'])
def start(m):
    cid = m.chat.id
    save_user(cid)
    user_state[cid] = None

    if not check_sub(m.from_user.id):
        bot.send_message(cid,"❗ Obuna bo‘ling",reply_markup=sub_kb())
        return

    bot.send_message(cid,"🔥 Xush kelibsiz",reply_markup=main_menu())

# ================= ADMIN =================
@bot.message_handler(commands=['admin'])
def admin(m):
    if m.from_user.id == OWNER_ID:
        user_state[m.chat.id] = "admin"
        bot.send_message(m.chat.id,"⚙️ Admin panel",reply_markup=admin_menu())

# ================= TEXT =================
@bot.message_handler(content_types=['text'])
def text(m):
    cid = m.chat.id
    txt = m.text
    state = user_state.get(cid)

    if txt == "🔙 Orqaga":
        user_state[cid] = None
        bot.send_message(cid,"Menu",reply_markup=main_menu())
        return

    # VOICE MENU
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
        file = f"{cid}.mp3"
        asyncio.run(tts(txt, file, voice))

        bot.send_voice(cid, open(file,"rb"))
        os.remove(file)

        user_state[cid] = None
        bot.send_message(cid,"✅ Tayyor",reply_markup=main_menu())
        return

    # MUSIC
    if txt == "🎧 Search Music":
        user_state[cid] = "music"
        bot.send_message(cid,"Qo‘shiq nomi")
        return

    if state == "music":
        data = search_spotify(txt)

        msg = f"🎵 {data['title']}\n👤 {data['artist']}\n⏱ {data['duration']} sec"
        bot.send_photo(cid, data['image'], caption=msg)

        with yt_dlp.YoutubeDL({'quiet':True}) as ydl:
            info = ydl.extract_info(f"ytsearch1:{txt}", download=False)
            url = f"https://youtube.com/watch?v={info['entries'][0]['id']}"

        user_music_cache[cid] = url

        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("⬇️ Yuklash", callback_data="dl"))
        bot.send_message(cid,"Yuklash:",reply_markup=kb)

        user_state[cid] = None
        return

    # VIDEO TO MP3
    if txt == "🎬 Video → MP3":
        user_state[cid] = "mp3"
        bot.send_message(cid,"Video yubor")
        return

    # CIRCLE
    if txt == "🔵 Circle Video":
        user_state[cid] = "circle"
        bot.send_message(cid,"Video yubor")
        return

    # ADMIN
    if state == "admin":
        if txt == "📊 Statistika":
            bot.send_message(cid,f"👥 {len(get_users())}")
        elif txt == "📢 Broadcast":
            user_state[cid] = "broadcast"
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

# ================= CALLBACK =================
@bot.callback_query_handler(func=lambda c: True)
def cb(c):
    cid = c.message.chat.id

    if c.data == "dl":
        url = user_music_cache.get(cid)

        file = f"{cid}.mp3"
        ydl_opts = {
            'format':'bestaudio',
            'outtmpl': file,
            'postprocessors':[{'key':'FFmpegExtractAudio','preferredcodec':'mp3'}]
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        bot.send_audio(cid, open(file,"rb"))
        os.remove(file)

# ================= VIDEO =================
@bot.message_handler(content_types=['video'])
def video(m):
    cid = m.chat.id
    state = user_state.get(cid)

    file = bot.get_file(m.video.file_id)
    data = bot.download_file(file.file_path)

    inp = f"{cid}.mp4"
    open(inp,"wb").write(data)

    if state == "mp3":
        out = f"{cid}.mp3"
        subprocess.run(["ffmpeg","-i",inp,out])
        bot.send_audio(cid, open(out,"rb"))
        os.remove(out)

    elif state == "circle":
        bot.send_video_note(cid, open(inp,"rb"))

    os.remove(inp)
    user_state[cid] = None

# ================= RUN =================
print("🔥 BOT ISHLADI")
bot.infinity_polling()
