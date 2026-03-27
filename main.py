import telebot
from telebot import types
import os, asyncio, yt_dlp, time, threading
import edge_tts
import imageio_ffmpeg as ffmpeg
import subprocess

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

CHANNEL = -1003877967882
OWNER_ID = int(os.getenv("ADMIN_ID"))

user_state = {}
user_voice = {}

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

# ================= START =================
@bot.message_handler(commands=['start'])
def start(m):
    save_user(m.chat.id)
    user_state[m.chat.id] = None
    bot.send_message(m.chat.id,"🔥 BOTGA XUSH KELIBSIZ",reply_markup=main_menu())

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

    # ORQAGA
    if txt == "🔙 Orqaga":
        user_state[cid] = None
        bot.send_message(cid,"Menu",reply_markup=main_menu())
        return

    # ADMIN
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

    # VOICE
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
        try:
            voice = "uz-UZ-SardorNeural" if user_voice.get(cid)=="male" else "uz-UZ-MadinaNeural"
            file = f"{cid}.mp3"

            async def run():
                communicate = edge_tts.Communicate(text=txt, voice=voice)
                await communicate.save(file)

            asyncio.run(run())

            bot.send_voice(cid, open(file,"rb"))
            os.remove(file)

        except Exception as e:
            bot.send_message(cid,f"❌ {e}")
        return

    # MODES
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

    # MUSIC
    if state == "music":
        try:
            bot.send_message(cid,"🔍 Qidirilmoqda...")

            ydl_opts = {
                'format': 'bestaudio',
                'outtmpl': f'{cid}.%(ext)s',
                'quiet': True,
                'noplaylist': True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"ytsearch1:{txt}", download=True)
                file = ydl.prepare_filename(info['entries'][0])

            bot.send_audio(cid, open(file,"rb"))
            os.remove(file)

        except Exception as e:
            bot.send_message(cid,f"❌ Topilmadi")
        finally:
            user_state[cid] = None

# ================= VIDEO =================
@bot.message_handler(content_types=['video'])
def video(m):
    cid = m.chat.id
    state = user_state.get(cid)

    file = bot.get_file(m.video.file_id)
    data = bot.download_file(file.file_path)

    inp = f"{cid}.mp4"
    open(inp,"wb").write(data)

    ffmpeg_path = ffmpeg.get_ffmpeg_exe()

    try:
        if state == "mp3":
            out = f"{cid}.mp3"
            subprocess.run([ffmpeg_path,"-y","-i",inp,out])
            bot.send_audio(cid, open(out,"rb"))
            os.remove(out)

        elif state == "circle":
            out = f"{cid}_c.mp4"
            subprocess.run([
                ffmpeg_path,"-y","-i",inp,
                "-vf","crop='min(in_w,in_h)':'min(in_w,in_h)',scale=240:240",
                out
            ])
            bot.send_video_note(cid, open(out,"rb"))
            os.remove(out)

    except Exception as e:
        bot.send_message(cid,f"❌ {e}")

    os.remove(inp)
    user_state[cid] = None

# ================= RUN =================
while True:
    try:
        print("🔥 FINAL BOT ISHLAYAPTI")
        bot.infinity_polling(skip_pending=True)
    except Exception as e:
        print("XATO:", e)
        time.sleep(5)
