import os, telebot, requests, yt_dlp, sqlite3, asyncio
from telebot import types
from flask import Flask
from threading import Thread
import edge_tts

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

user_state = {}
user_voice = {}

# ================= DATABASE =================
conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY)")
cursor.execute("CREATE TABLE IF NOT EXISTS likes(track TEXT, count INTEGER)")
conn.commit()

# ================= WEB =================
@app.route("/")
def home():
    cursor.execute("SELECT COUNT(*) FROM users")
    users = cursor.fetchone()[0]

    cursor.execute("SELECT * FROM likes ORDER BY count DESC LIMIT 5")
    top = cursor.fetchall()

    html = f"<h1>BOT LIVE</h1><p>Users: {users}</p>"
    for t in top:
        html += f"<p>{t[0]} ❤️ {t[1]}</p>"

    return html

def run_web():
    app.run(host="0.0.0.0", port=10000)

# ================= SPOTIFY =================
def get_token():
    res = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type":"client_credentials"},
        auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)
    )
    return res.json().get("access_token")

def search_spotify(q):
    token = get_token()
    headers = {"Authorization":f"Bearer {token}"}
    res = requests.get(
        f"https://api.spotify.com/v1/search?q={q}&type=track&limit=1",
        headers=headers
    ).json()

    if "tracks" not in res or not res["tracks"]["items"]:
        return None

    t = res["tracks"]["items"][0]

    return {
        "name": t["name"],
        "artist": t["artists"][0]["name"],
        "image": t["album"]["images"][0]["url"]
    }

# ================= DOWNLOAD =================
def download_audio(q, cid):
    try:
        ydl_opts = {
            'format':'bestaudio',
            'outtmpl':f'{cid}.%(ext)s',
            'quiet':True,
            'postprocessors':[{'key':'FFmpegExtractAudio','preferredcodec':'mp3'}]
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(f"ytsearch1:{q}", download=True)

        file = f"{cid}.mp3"
        bot.send_audio(cid, open(file,'rb'))
        os.remove(file)
    except:
        bot.send_message(cid,"❌ Yuklab bo‘lmadi")

# ================= TTS =================
async def tts(text, file, voice):
    com = edge_tts.Communicate(text=text, voice=voice)
    await com.save(file)

# ================= START =================
@bot.message_handler(commands=['start'])
def start(m):
    cid = m.chat.id

    cursor.execute("INSERT OR IGNORE INTO users VALUES(?)",(cid,))
    conn.commit()

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🎵 Music","🎤 Voice")
    kb.add("🎥 Video","📊 Stat")

    bot.send_message(cid,"Tanlang 👇",reply_markup=kb)

# ================= ADMIN =================
@bot.message_handler(commands=['admin'])
def admin(m):
    if m.chat.id != ADMIN_ID:
        return

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📢 Broadcast","📊 Stat")
    kb.add("🔙 Orqaga")

    user_state[m.chat.id] = "admin"
    bot.send_message(m.chat.id,"Admin panel",reply_markup=kb)

# ================= TEXT =================
@bot.message_handler(content_types=['text'])
def text(m):
    cid = m.chat.id
    txt = m.text
    state = user_state.get(cid)

    # ORQAGA
    if txt == "🔙 Orqaga":
        user_state[cid] = None
        start(m)
        return

    # STAT
    if txt == "📊 Stat":
        cursor.execute("SELECT COUNT(*) FROM users")
        bot.send_message(cid,f"👥 Users: {cursor.fetchone()[0]}")
        return

    # ADMIN BROADCAST
    if state == "admin" and txt == "📢 Broadcast":
        user_state[cid] = "broadcast"
        bot.send_message(cid,"Post yubor (text/rasm/video)")
        return

    if state == "broadcast":
        users = cursor.execute("SELECT id FROM users").fetchall()
        for u in users:
            try:
                bot.copy_message(u[0], cid, m.message_id)
            except:
                pass
        bot.send_message(cid,"✅ Yuborildi")
        user_state[cid] = "admin"
        return

    # VOICE
    if txt == "🎤 Voice":
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add("👨 Erkak","👩 Ayol")
        kb.add("🔙 Orqaga")

        user_state[cid] = "voice"
        bot.send_message(cid,"Tanlang",reply_markup=kb)
        return

    if txt in ["👨 Erkak","👩 Ayol"]:
        user_voice[cid] = "male" if "Erkak" in txt else "female"
        user_state[cid] = "tts"
        bot.send_message(cid,"Matn yoz")
        return

    if state == "tts":
        voice = "uz-UZ-SardorNeural" if user_voice[cid]=="male" else "uz-UZ-MadinaNeural"
        file = f"{cid}.mp3"
        asyncio.run(tts(txt,file,voice))
        bot.send_voice(cid,open(file,'rb'))
        os.remove(file)
        user_state[cid]=None
        return

    # MUSIC
    if txt == "🎵 Music":
        user_state[cid] = "music"
        bot.send_message(cid,"Qo‘shiq nomi")
        return

    if state == "music":
        sp = search_spotify(txt)
        if sp:
            bot.send_photo(cid,sp["image"],caption=f"{sp['name']} - {sp['artist']}")
        download_audio(txt,cid)
        user_state[cid]=None
        return

    # VIDEO MODE
    if txt == "🎥 Video":
        user_state[cid] = "video"
        bot.send_message(cid,"Video yubor")
        return

# ================= VIDEO =================
@bot.message_handler(content_types=['video'])
def video(m):
    cid = m.chat.id

    file = bot.get_file(m.video.file_id)
    data = bot.download_file(file.file_path)

    open("v.mp4","wb").write(data)

    bot.send_message(cid,"⏳ Processing...")

    os.system("ffmpeg -i v.mp4 -vf crop='min(in_w,in_h):min(in_w,in_h)',scale=240:240 c.mp4")
    os.system("ffmpeg -i v.mp4 -q:a 0 -map a a.mp3")

    bot.send_video_note(cid,open("c.mp4","rb"))
    bot.send_audio(cid,open("a.mp3","rb"))

    os.remove("v.mp4")
    os.remove("c.mp4")
    os.remove("a.mp3")

# ================= RUN =================
if __name__ == "__main__":
    Thread(target=run_web).start()
    print("🚀 ISHLAYAPTI...")
    bot.infinity_polling()
