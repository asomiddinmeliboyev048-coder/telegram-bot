import os
import telebot
import requests
import sqlite3
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from gtts import gTTS
import yt_dlp
import imageio_ffmpeg as ffmpeg

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

bot = telebot.TeleBot(BOT_TOKEN)

# DATABASE
conn = sqlite3.connect("users.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER)")
conn.commit()

def add_user(user_id):
    cursor.execute("INSERT OR IGNORE INTO users (id) VALUES (?)", (user_id,))
    conn.commit()

def get_users():
    cursor.execute("SELECT id FROM users")
    return cursor.fetchall()

# MENU
def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("🎵 Music", "🎤 Text to Voice")
    markup.add("🎬 Video → Circle", "📊 Stats")
    return markup

# START
@bot.message_handler(commands=['start'])
def start(msg):
    add_user(msg.chat.id)
    bot.send_message(msg.chat.id, "🚀 Bot ishga tushdi!", reply_markup=main_menu())

# STATS
@bot.message_handler(func=lambda m: m.text == "📊 Stats")
def stats(msg):
    if msg.chat.id == ADMIN_ID:
        users = len(get_users())
        bot.send_message(msg.chat.id, f"👥 Users: {users}")
    else:
        bot.send_message(msg.chat.id, "❌ Admin emas")

# MUSIC SEARCH
@bot.message_handler(func=lambda m: m.text == "🎵 Music")
def ask_music(msg):
    bot.send_message(msg.chat.id, "🎧 Musiqa nomini yoz:")

@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_text(msg):
    text = msg.text

    if text.startswith("🎤"):
        return

    bot.send_message(msg.chat.id, "🔍 Qidirilmoqda...")

    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'extractaudio': True,
        'audioformat': 'mp3',
        'outtmpl': 'song.%(ext)s'
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch:{text}", download=True)
            title = info['entries'][0]['title']

        with open("song.mp3", "rb") as audio:
            bot.send_audio(msg.chat.id, audio, title=title)

        os.remove("song.mp3")

    except Exception as e:
        bot.send_message(msg.chat.id, "❌ Musiqa topilmadi")

# TEXT TO VOICE
@bot.message_handler(func=lambda m: m.text == "🎤 Text to Voice")
def tts(msg):
    bot.send_message(msg.chat.id, "✍️ Matn yoz:")

@bot.message_handler(content_types=['text'])
def voice(msg):
    if msg.text.startswith("🎵") or msg.text.startswith("🎬"):
        return

    try:
        tts = gTTS(msg.text, lang='en')
        tts.save("voice.mp3")

        with open("voice.mp3", "rb") as v:
            bot.send_voice(msg.chat.id, v)

        os.remove("voice.mp3")
    except:
        pass

# VIDEO → CIRCLE
@bot.message_handler(content_types=['video'])
def video(msg):
    try:
        file_info = bot.get_file(msg.video.file_id)
        downloaded = bot.download_file(file_info.file_path)

        with open("video.mp4", "wb") as f:
            f.write(downloaded)

        bot.send_video_note(msg.chat.id, open("video.mp4", "rb"))

        os.remove("video.mp4")
    except:
        bot.send_message(msg.chat.id, "❌ Video error")

# ADMIN BROADCAST
@bot.message_handler(commands=['send'])
def broadcast(msg):
    if msg.chat.id != ADMIN_ID:
        return

    bot.send_message(msg.chat.id, "📢 Xabar yoz:")

    bot.register_next_step_handler(msg, send_all)

def send_all(msg):
    users = get_users()
    for u in users:
        try:
            bot.send_message(u[0], msg.text)
        except:
            pass

# RUN
print("✅ BOT ISHLAYAPTI...")
bot.infinity_polling()
