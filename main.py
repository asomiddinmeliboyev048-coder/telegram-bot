# ================= WEB =================
from flask import Flask, request
from threading import Thread
import requests, base64, time, os, subprocess, asyncio, yt_dlp, zipfile
import telebot
from telebot import types
import edge_tts
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")

bot = telebot.TeleBot(BOT_TOKEN)
executor = ThreadPoolExecutor(max_workers=50)

app = Flask(__name__)

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

# ================= FFMPEG =================
def setup_ffmpeg():
    if not os.path.exists("ffmpeg"):
        url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        r = requests.get(url)
        open("ffmpeg.zip","wb").write(r.content)

        with zipfile.ZipFile("ffmpeg.zip",'r') as z:
            z.extractall("ffmpeg")

    for root, dirs, files in os.walk("ffmpeg"):
        if "ffmpeg.exe" in files or "ffmpeg" in files:
            os.environ["PATH"] += os.pathsep + root
            break

setup_ffmpeg()

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

    r = requests.get(f"https://api.spotify.com/v1/search?q={query}&type=track&limit=1", headers=headers)
    data = r.json()

    if not data["tracks"]["items"]:
        return None

    track = data["tracks"]["items"][0]
    return {
        "name": track["name"],
        "artist": track["artists"][0]["name"],
        "duration": int(track["duration_ms"]/1000),
        "image": track["album"]["images"][0]["url"],
        "url": track["external_urls"]["spotify"]
    }

# ================= ADMIN PANEL =================
@app.route("/")
def home():
    return "Bot ishlayapti 🚀"

@app.route("/admin")
def admin_panel():
    token = request.args.get("token")
    if token != ADMIN_TOKEN:
        return "❌ Access denied"

    users = get_users()
    return f"""
    <h1>Dashboard</h1>
    <p>Users: {len(users)}</p>
    <form action="/broadcast" method="post">
    <input name="msg">
    <input name="token" value="{ADMIN_TOKEN}" hidden>
    <button>Send</button>
    </form>
    """

@app.route("/broadcast", methods=["POST"])
def broadcast():
    if request.form.get("token") != ADMIN_TOKEN:
        return "❌ error"

    msg = request.form.get("msg")

    for u in get_users():
        try:
            bot.send_message(u, msg)
        except:
            pass

    return "✅ sent"

def run_web():
    app.run(host='0.0.0.0', port=10000)

Thread(target=run_web).start()

# ================= FUNCTIONS =================
def run_async(func,*args):
    executor.submit(func,*args)

def tts(cid,text,voice):
    file=f"{cid}.mp3"

    async def run():
        t= edge_tts.Communicate(text=text,voice=voice)
        await t.save(file)

    asyncio.run(run())
    bot.send_voice(cid,open(file,"rb"))
    os.remove(file)

def convert_mp3(inp,out):
    subprocess.call(f'ffmpeg -y -i "{inp}" "{out}"',shell=True)

def convert_circle(inp,out):
    subprocess.call(f'ffmpeg -y -i "{inp}" -vf scale=240:240 "{out}"',shell=True)

# ================= BOT =================
@bot.message_handler(commands=['start'])
def start(m):
    save_user(m.chat.id)
    bot.send_message(m.chat.id,"🔥 Botga xush kelibsiz")

@bot.message_handler(content_types=['text'])
def text(m):
    cid=m.chat.id
    txt=m.text

    if txt=="🎤":
        bot.send_message(cid,"Matn yoz")
        return

    if txt=="🎧":
        res=search_spotify(txt)

        if not res:
            bot.send_message(cid,"Topilmadi")
            return

        msg=f"""
🎵 {res['name']}
👤 {res['artist']}
⏱ {res['duration']} sec
"""

        bot.send_photo(cid,res['image'],caption=msg)

    else:
        run_async(tts,cid,txt,"uz-UZ-SardorNeural")

@bot.message_handler(content_types=['video'])
def video(m):
    cid=m.chat.id

    file=bot.get_file(m.video.file_id)
    data=bot.download_file(file.file_path)

    inp=f"{cid}.mp4"
    open(inp,"wb").write(data)

    out=f"{cid}.mp3"
    convert_mp3(inp,out)

    bot.send_audio(cid,open(out,"rb"))

    circ=f"{cid}_c.mp4"
    convert_circle(inp,circ)

    bot.send_video_note(cid,open(circ,"rb"))

    os.remove(inp)
    os.remove(out)
    os.remove(circ)

print("🚀 ISHLAYAPTI")
bot.infinity_polling()
