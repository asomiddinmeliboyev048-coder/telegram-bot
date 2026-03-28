import telebot
from telebot import types
import os, subprocess, asyncio, time, threading
import yt_dlp
import edge_tts
from flask import Flask

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

CHANNEL = -1003877967882
OWNER_ID = 7171330738

user_state = {}
user_voice = {}

# ================= FLASK (RENDER FIX) =================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web).start()
# =====================================================

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

# ================= SUB CHECK =================
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
    kb.add(types.InlineKeyboardButton("📢 Obuna bo‘lish", url="https://t.me/meliboyevdev"))
    kb.add(types.InlineKeyboardButton("✅ Tekshirish", callback_data="check"))
    return kb

# ================= MENUS =================
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

# ================= START =================
@bot.message_handler(commands=['start'])
def start(m):
    save_user(m.chat.id)
    user_state[m.chat.id] = None

    if not check_sub(m.from_user.id):
        bot.send_message(m.chat.id,"❗️ Kanalga a’zo bo‘ling",reply_markup=sub_kb())
        return

    bot.send_message(m.chat.id,"🔥 BOTGA XUSH KELIBSIZ",reply_markup=main_menu())

# ================= CALLBACK =================
@bot.callback_query_handler(func=lambda c: c.data=="check")
def check(c):
    if check_sub(c.from_user.id):
        bot.send_message(c.message.chat.id,"✅ OK",reply_markup=main_menu())
    else:
        bot.answer_callback_query(c.id,"❌ Obuna bo‘ling",show_alert=True)

# ================= ADMIN =================
@bot.message_handler(commands=['admin'])
def admin(m):
    if m.from_user.id == OWNER_ID:
        bot.send_message(m.chat.id,"⚙️ Admin panel",reply_markup=admin_menu())

AUTO_POST_TEXT = None

# ================= TEXT =================
@bot.message_handler(content_types=['text'])
def text(m):
    cid = m.chat.id
    txt = m.text
    state = user_state.get(cid)

    if not check_sub(m.from_user.id):
        bot.send_message(cid,"❗️ Obuna bo‘ling",reply_markup=sub_kb())
        return

    if txt == "🔙 Orqaga":
        user_state[cid] = None
        bot.send_message(cid,"Menu",reply_markup=main_menu())
        return

    if txt == "🎤 Text → Voice":
        user_state[cid] = "choose_voice"
        bot.send_message(cid,"Ovoz tanlang",reply_markup=voice_menu())
        return

    if txt in ["👨 Erkak ovoz","👩 Ayol ovoz"]:
        user_voice[cid] = "male" if "Erkak" in txt else "female"
        user_state[cid] = "tts"
        bot.send_message(cid,"✍️ Matn yubor")
        return

    if state == "tts":
        try:
            voice = "uz-UZ-SardorNeural" if user_voice.get(cid)=="male" else "uz-UZ-MadinaNeural"
            file = f"{cid}.mp3"

            async def generate():
                communicate = edge_tts.Communicate(text=txt, voice=voice)
                await communicate.save(file)

            asyncio.run(generate())

            bot.send_voice(cid, open(file,"rb"))
            os.remove(file)

        except Exception as e:
            bot.send_message(cid,f"❌ {e}")
        return

    if txt == "📊 Statistika" and m.from_user.id == OWNER_ID:
        bot.send_message(cid,f"👥 Userlar: {len(get_users())}")
        return

    if txt == "📢 Broadcast" and m.from_user.id == OWNER_ID:
        user_state[cid] = "broadcast"
        bot.send_message(cid,"Post yubor")
        return

    if txt == "📣 Auto Post" and m.from_user.id == OWNER_ID:
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
        user_state[cid] = None
        return

    if state == "autopost":
        global AUTO_POST_TEXT
        AUTO_POST_TEXT = m.text
        try:
            bot.copy_message(CHANNEL, cid, m.message_id)
        except Exception as e:
            bot.send_message(cid,f"❌ Kanal xato: {e}")
        bot.send_message(cid,"✅ Saqlandi")
        user_state[cid] = None
        return

    if txt == "🎬 Video → MP3":
        user_state[cid] = "mp3"
        bot.send_message(cid,"Video yubor")
        return

    if txt == "🎧 Search Music":
        user_state[cid] = "music"
        bot.send_message(cid,"Qo‘shiq nomi yoz")
        return

    if txt == "🔵 Circle Video":
        user_state[cid] = "circle"
        bot.send_message(cid,"Video yubor")
        return

    if state == "music":
        try:
            bot.send_message(cid,"🔍 Qidirilmoqda...")

            ydl_opts = {
                'format': 'bestaudio',
                'outtmpl': f'{cid}.%(ext)s',
                'quiet': True,
                'noplaylist': True,
                'postprocessors':[{'key':'FFmpegExtractAudio','preferredcodec':'mp3'}]
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"scsearch1:{txt}", download=True)
                entry = info['entries'][0]
                title = entry['title']
                artist = entry.get('uploader',"Unknown")

            file = f"{cid}.mp3"
            bot.send_audio(cid,open(file,"rb"),title=f"{artist} - {title}")
            os.remove(file)

        except Exception as e:
            bot.send_message(cid,f"❌ Music error: {e}")

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
            out = f"{cid}.mp3"
            subprocess.run(["ffmpeg","-y","-i",inp,out])
            bot.send_audio(cid, open(out,"rb"))
            os.remove(out)

        elif state == "circle":
            out = f"{cid}_circle.mp4"
            subprocess.run([
                "ffmpeg","-y","-i",inp,
                "-vf","crop='min(in_w,in_h)':'min(in_w,in_h)',scale=240:240",
                "-c:v","libx264","-c:a","aac",out
            ])
            bot.send_video_note(cid, open(out,"rb"))
            os.remove(out)

    except Exception as e:
        bot.send_message(cid,f"❌ {e}")

    os.remove(inp)

# ================= AUTO POST =================
def auto_post_loop():
    while True:
        if AUTO_POST_TEXT:
            for u in get_users():
                try:
                    bot.send_message(u, AUTO_POST_TEXT)
                except:
                    pass
        time.sleep(3600)

threading.Thread(target=auto_post_loop).start()

# ================= RUN =================
while True:
    try:
        print("🔥 BOT ISHLAYAPTI...")
        bot.infinity_polling(skip_pending=True)
    except Exception as e:
        print("XATO:", e)
        time.sleep(5)
