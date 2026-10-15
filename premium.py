# ============================================================
# premium.py — Premium tizimi
#   * bepul tarif limitlari (50 so'z, kuniga 20 ta so'rov)
#   * Premium sotib olish oqimi (karta, "To'lov qildim", admin tasdiqlaydi)
#   * MP3 yuklab olish
#   * Premium tugashiga 1 kun qolganda eslatma
# ============================================================
import asyncio
import html
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from telebot import types

logger = logging.getLogger(__name__)

# ================= SOZLAMALAR =================
BUTTON_TEXT = "💎 Premium sotib olish"      # asosiy menudagi tugma
ADMIN_BUTTON_TEXT = "💎 Premiumlar"         # admin menyusidagi tugma

PREMIUM_DAYS = 30                           # premium muddati (1 oy)
FREE_MAX_WORDS = 50                         # bepul tarifda maksimal so'z soni
FREE_DAILY_LIMIT = 20                       # bepul tarifda kunlik so'rovlar
REMIND_BEFORE_SEC = 24 * 3600               # tugashidan 1 kun oldin eslatish

PRICE_TEXT = "3 000 so'm"
CARD_NUMBER = "9860 1606 3935 2782"
CARD_OWNER = "Meliboyev A"
ADMIN_USERNAME = "MatnOvozBotAdmin"

TZ = timezone(timedelta(hours=5))           # Toshkent vaqti (kun chegarasi shu bo'yicha)

DB_PATH = os.path.join(os.getenv("RENDER_DISK_PATH", os.getcwd()), "premium.db")

OWNER_ID = None
TEMP_DIR = None
_db_lock = threading.Lock()
_paid_cooldown = {}     # user_id -> oxirgi "To'lov qildim" vaqti
_decided = set()        # admin allaqachon qaror qilgan so'rovlar (ikki marta bosilmasligi uchun)


# ================= BAZA =================
@contextmanager
def _db():
    with _db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def _now():
    return int(time.time())


def _today():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def fmt_date(ts):
    return datetime.fromtimestamp(ts, TZ).strftime("%d.%m.%Y %H:%M")


# ----- Firebase nusxasi (Render'da fayl o'chib ketsa premium yo'qolmasligi uchun) -----
def _fb_ref():
    try:
        import firebase_admin
        from firebase_admin import db
        if not firebase_admin._apps:
            return None
        return db.reference("premium")
    except Exception:
        return None


def _fb_save(user_id, expires_at, notified):
    try:
        ref = _fb_ref()
        if ref:
            ref.child(str(user_id)).set({"expires_at": int(expires_at), "notified": int(notified)})
    except Exception as e:
        logger.warning(f"Firebase premium save xato: {e}")


def _fb_delete(user_id):
    try:
        ref = _fb_ref()
        if ref:
            ref.child(str(user_id)).delete()
    except Exception as e:
        logger.warning(f"Firebase premium delete xato: {e}")


def _restore_from_firebase():
    try:
        ref = _fb_ref()
        if not ref:
            return
        data = ref.get() or {}
        restored = 0
        with _db() as c:
            for uid, rec in data.items():
                try:
                    exp = int(rec.get("expires_at", 0))
                    notified = int(rec.get("notified", 0))
                    row = c.execute("SELECT expires_at FROM premium WHERE user_id=?", (int(uid),)).fetchone()
                    if exp > (row[0] if row else 0):
                        c.execute(
                            "INSERT OR REPLACE INTO premium (user_id, expires_at, notified) VALUES (?, ?, ?)",
                            (int(uid), exp, notified),
                        )
                        restored += 1
                except Exception:
                    continue
        if restored:
            logger.info(f"Firebase'dan {restored} ta premium tiklandi")
    except Exception as e:
        logger.warning(f"Firebase premium restore xato: {e}")


def init_db():
    with _db() as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS premium ("
            "user_id INTEGER PRIMARY KEY, expires_at INTEGER NOT NULL, notified INTEGER NOT NULL DEFAULT 0)"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS usage ("
            "user_id INTEGER, day TEXT, count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (user_id, day))"
        )
    _restore_from_firebase()


# ================= PREMIUM HOLATI =================
def get_expiry(user_id):
    with _db() as c:
        row = c.execute("SELECT expires_at FROM premium WHERE user_id=?", (user_id,)).fetchone()
    return row[0] if row else 0


def is_premium(user_id):
    """Admin har doim premium (cheklovsiz). Boshqalar — muddati tugamagan bo'lsa."""
    if OWNER_ID is not None and user_id == OWNER_ID:
        return True
    return get_expiry(user_id) > _now()


def grant(user_id, days=PREMIUM_DAYS):
    """Premium berish. Agar hali faol bo'lsa, muddat ustiga qo'shiladi."""
    base = max(get_expiry(user_id), _now())
    expires_at = base + days * 86400
    with _db() as c:
        c.execute(
            "INSERT OR REPLACE INTO premium (user_id, expires_at, notified) VALUES (?, ?, 0)",
            (user_id, expires_at),
        )
    _fb_save(user_id, expires_at, 0)
    return expires_at


def revoke(user_id):
    with _db() as c:
        c.execute("DELETE FROM premium WHERE user_id=?", (user_id,))
    _fb_delete(user_id)


def list_active(limit=40):
    with _db() as c:
        rows = c.execute(
            "SELECT user_id, expires_at FROM premium WHERE expires_at > ? ORDER BY expires_at ASC LIMIT ?",
            (_now(), limit),
        ).fetchall()
    return rows


# ================= KUNLIK LIMIT =================
def get_usage(user_id):
    with _db() as c:
        row = c.execute("SELECT count FROM usage WHERE user_id=? AND day=?", (user_id, _today())).fetchone()
    return row[0] if row else 0


def add_usage(user_id):
    """Muvaffaqiyatli bajarilgan har bir so'rovdan keyin chaqiriladi."""
    with _db() as c:
        c.execute(
            "INSERT INTO usage (user_id, day, count) VALUES (?, ?, 1) "
            "ON CONFLICT(user_id, day) DO UPDATE SET count = count + 1",
            (user_id, _today()),
        )
        # eski kunlarni tozalash
        yesterday = (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
        c.execute("DELETE FROM usage WHERE day < ?", (yesterday,))


def check_free_limits(user_id, text):
    """Bepul tarif uchun tekshiruv. (ruxsat, sabab) qaytaradi. sabab: 'words' yoki 'daily'."""
    if len(text.split()) > FREE_MAX_WORDS:
        return False, "words"
    if get_usage(user_id) >= FREE_DAILY_LIMIT:
        return False, "daily"
    return True, None


# ================= ESLATMALAR (DB) =================
def pop_due_notifications():
    """Eslatma yuborilishi kerak bo'lganlarni qaytaradi va ularni belgilab qo'yadi."""
    now = _now()
    with _db() as c:
        soon = c.execute(
            "SELECT user_id, expires_at FROM premium WHERE notified=0 AND expires_at > ? AND expires_at <= ?",
            (now, now + REMIND_BEFORE_SEC),
        ).fetchall()
        expired = c.execute(
            "SELECT user_id, expires_at FROM premium WHERE notified<2 AND expires_at <= ?", (now,)
        ).fetchall()
        c.execute(
            "UPDATE premium SET notified=1 WHERE notified=0 AND expires_at > ? AND expires_at <= ?",
            (now, now + REMIND_BEFORE_SEC),
        )
        c.execute("UPDATE premium SET notified=2 WHERE notified<2 AND expires_at <= ?", (now,))
    soon_ids = {u for u, _ in soon}
    expired = [(u, e) for u, e in expired]
    soon = [(u, e) for u, e in soon if u not in {x for x, _ in expired}]
    for u, e in soon:
        _fb_save(u, e, 1)
    for u, e in expired:
        _fb_save(u, e, 2)
    return soon, expired


# ================= MATNLAR =================
def benefits_text():
    return (
        "💎 <b>PREMIUM afzalliklari</b>\n\n"
        f"✅ {FREE_MAX_WORDS} tadan ko'p so'zli matnni ovozga aylantirish "
        f"(bepul tarifda faqat {FREE_MAX_WORDS} so'zgacha)\n"
        f"✅ Kunlik limit yo'q (bepul tarifda kuniga {FREE_DAILY_LIMIT} ta so'rov)\n"
        "✅ Tayyor ovozni MP3 fayl qilib yuklab olish\n"
        "✅ Reklamasiz: hech qanday reklama va havola chiqmaydi\n"
        f"✅ Amal qilish muddati: {PREMIUM_DAYS} kun (1 oy)\n\n"
        f"💰 Narxi: <b>{PRICE_TEXT}</b> / 1 oy"
    )


def payment_text():
    return (
        "💳 <b>Premium sotib olish</b>\n\n"
        f"Ushbu kartaga <b>{PRICE_TEXT}</b> yuboring:\n\n"
        f"<code>{CARD_NUMBER}</code>\n"
        f"👤 Karta egasi: <b>{CARD_OWNER}</b>\n\n"
        f"📸 To'lov chekini skrin qilib @{ADMIN_USERNAME} ga yuboring. "
        "Admin 20 daqiqa ichida premiumingizni tasdiqlaydi va siz premiumdan "
        "1 oy davomida foydalanishingiz mumkin bo'ladi.\n\n"
        "Skrinni yuborganingizdan so'ng pastdagi «✅ To'lov qildim» tugmasini bosing."
    )


def buy_kb():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("💳 Sotib olish", callback_data="prem_buy"))
    return kb


def renew_kb():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔄 Premiumni uzaytirish", callback_data="prem_buy"))
    return kb


def pay_kb():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ To'lov qildim", callback_data="prem_paid"))
    kb.add(types.InlineKeyboardButton(f"📩 @{ADMIN_USERNAME} ga skrin yuborish", url=f"https://t.me/{ADMIN_USERNAME}"))
    return kb


async def send_limit_message(bot, chat_id, reason, words=0):
    """Bepul limit tugaganda/oshganda chiqadigan xabar: sabab + to'liq afzalliklar + sotib olish tugmasi."""
    if reason == "words":
        head = (
            f"⚠️ Siz <b>{words}</b> ta so'z yubordingiz. Bepul tarifda matn "
            f"{FREE_MAX_WORDS} ta so'zdan oshmasligi kerak.\n\n"
            "Uzun matnni ovozga aylantirish uchun Premium sotib oling 👇"
        )
    else:
        head = (
            f"⏳ Bugungi bepul limit tugadi ({FREE_DAILY_LIMIT}/{FREE_DAILY_LIMIT}).\n\n"
            "Cheksiz foydalanish uchun Premium sotib oling 👇"
        )
    await bot.send_message(chat_id, f"{head}\n\n{benefits_text()}", parse_mode="HTML", reply_markup=buy_kb())


# ================= HANDLERLAR =================
def setup(bot, owner_id, temp_dir):
    """Barcha premium handlerlarni ro'yxatdan o'tkazadi.
    MUHIM: main.py dagi umumiy text_handler dan OLDIN chaqirilishi kerak."""
    global OWNER_ID, TEMP_DIR
    OWNER_ID = owner_id
    TEMP_DIR = temp_dir

    # ---------- Foydalanuvchi: "💎 Premium sotib olish" ----------
    @bot.message_handler(func=lambda m: m.text == BUTTON_TEXT)
    async def premium_menu(m):
        cid = m.chat.id
        uid = m.from_user.id
        try:
            if uid == OWNER_ID:
                await bot.send_message(cid, "👑 Siz adminsiz: barcha imkoniyatlar cheklovsiz va reklamasiz.")
                return
            expiry = await asyncio.to_thread(get_expiry, uid)
            if expiry > _now():
                days_left = max(1, (expiry - _now() + 86399) // 86400)
                await bot.send_message(
                    cid,
                    "✅ <b>Sizda Premium faol</b>\n\n"
                    f"📅 Tugash sanasi: <b>{fmt_date(expiry)}</b>\n"
                    f"⏳ Qolgan muddat: <b>{days_left}</b> kun\n\n"
                    "Uzaytirmoqchi bo'lsangiz, qo'shimcha 1 oy mavjud muddatga qo'shiladi.",
                    parse_mode="HTML",
                    reply_markup=renew_kb(),
                )
            else:
                used = await asyncio.to_thread(get_usage, uid)
                await bot.send_message(
                    cid,
                    f"{benefits_text()}\n\n📊 Bugungi bepul so'rovlaringiz: {used}/{FREE_DAILY_LIMIT}",
                    parse_mode="HTML",
                    reply_markup=buy_kb(),
                )
        except Exception as e:
            logger.error(f"Premium menu xato: {e}")

    @bot.callback_query_handler(func=lambda c: c.data == "prem_buy")
    async def cb_buy(call):
        try:
            await bot.answer_callback_query(call.id)
            await bot.send_message(call.message.chat.id, payment_text(), parse_mode="HTML", reply_markup=pay_kb())
        except Exception as e:
            logger.error(f"prem_buy xato: {e}")

    # ---------- Foydalanuvchi: "✅ To'lov qildim" -> adminga so'rov ----------
    @bot.callback_query_handler(func=lambda c: c.data == "prem_paid")
    async def cb_paid(call):
        user = call.from_user
        try:
            now = time.time()
            if now - _paid_cooldown.get(user.id, 0) < 120:
                await bot.answer_callback_query(
                    call.id, "⏳ So'rovingiz adminga yuborilgan. Iltimos, tasdiqlanishini kuting.", show_alert=True
                )
                return
            _paid_cooldown[user.id] = now

            name = html.escape(user.first_name or "Foydalanuvchi")
            uname = html.escape(f"@{user.username}") if user.username else "—"
            admin_text = (
                "💎 <b>Yangi Premium so'rovi</b>\n\n"
                f"👤 Ism: <a href=\"tg://user?id={user.id}\">{name}</a>\n"
                f"🔗 Username: {uname}\n"
                f"🆔 ID: <code>{user.id}</code>\n\n"
                f"Skrinshotni @{ADMIN_USERNAME} da tekshiring va tasdiqlang 👇"
            )
            kb = types.InlineKeyboardMarkup()
            kb.row(
                types.InlineKeyboardButton("✅ 1 oyga tasdiqlash", callback_data=f"pa_{user.id}"),
                types.InlineKeyboardButton("❌ Rad etish", callback_data=f"pr_{user.id}"),
            )
            try:
                await bot.send_message(OWNER_ID, admin_text, parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                logger.error(f"Adminga so'rov yuborilmadi: {e}")
                _paid_cooldown.pop(user.id, None)
                await bot.answer_callback_query(
                    call.id, f"❌ Xatolik. Iltimos, @{ADMIN_USERNAME} ga to'g'ridan-to'g'ri yozing.", show_alert=True
                )
                return

            await bot.answer_callback_query(call.id, "✅ So'rovingiz adminga yuborildi!")
            await bot.send_message(
                call.message.chat.id,
                "✅ So'rovingiz adminga yuborildi.\n\n"
                f"📸 Agar hali yubormagan bo'lsangiz, to'lov chekini @{ADMIN_USERNAME} ga yuboring. "
                "Premium 20 daqiqa ichida faollashtiriladi.",
            )
        except Exception as e:
            logger.error(f"prem_paid xato: {e}")

    # ---------- Admin: tasdiqlash / rad etish tugmalari ----------
    @bot.callback_query_handler(func=lambda c: c.data.startswith(("pa_", "pr_")))
    async def cb_admin_decision(call):
        try:
            if call.from_user.id != OWNER_ID:
                await bot.answer_callback_query(call.id, "❌ Bu tugma faqat admin uchun.", show_alert=True)
                return
            action, _, raw = call.data.partition("_")
            try:
                target = int(raw)
            except ValueError:
                await bot.answer_callback_query(call.id, "❌ Noto'g'ri ID")
                return

            key = (call.message.chat.id, call.message.message_id)
            if key in _decided:
                await bot.answer_callback_query(call.id, "Bu so'rov allaqachon ko'rib chiqilgan.")
                return
            _decided.add(key)

            if action == "pa":
                expires_at = await asyncio.to_thread(grant, target)
                status = f"\n\n✅ <b>Tasdiqlandi</b> — {fmt_date(expires_at)} gacha"
                user_msg = (
                    "🎉 <b>Premiumingiz faollashtirildi!</b>\n\n"
                    f"📅 Amal qilish muddati: <b>{fmt_date(expires_at)}</b> gacha\n\n"
                    f"{benefits_text()}"
                )
                await bot.answer_callback_query(call.id, "✅ Premium faollashtirildi")
            else:
                status = "\n\n❌ <b>Rad etildi</b>"
                user_msg = (
                    "❌ To'lovingiz tasdiqlanmadi.\n\n"
                    f"Iltimos, chekni @{ADMIN_USERNAME} ga qayta yuboring yoki u bilan bog'laning."
                )
                await bot.answer_callback_query(call.id, "❌ Rad etildi")

            try:
                await bot.send_message(target, user_msg, parse_mode="HTML")
            except Exception as e:
                status += "\n⚠️ Foydalanuvchiga xabar yuborilmadi (botni bloklagan bo'lishi mumkin)"
                logger.warning(f"Premium xabari yuborilmadi {target}: {e}")

            try:
                await bot.edit_message_text(
                    (call.message.html_text or "") + status,
                    call.message.chat.id,
                    call.message.message_id,
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.debug(f"Admin xabarini tahrirlashda xato: {e}")
        except Exception as e:
            logger.error(f"Admin qarori xato: {e}")

    # ---------- Admin buyruqlari ----------
    @bot.message_handler(commands=["premium"])
    async def cmd_premium(m):
        """/premium <user_id> [kun]  — qo'lda premium berish. Oddiy foydalanuvchiga premium menyusini ko'rsatadi."""
        if m.from_user.id != OWNER_ID:
            await premium_menu(m)
            return
        try:
            parts = (m.text or "").split()
            if len(parts) < 2 or not parts[1].isdigit():
                await bot.send_message(
                    m.chat.id,
                    "Foydalanish: <code>/premium USER_ID [kun]</code>\nMasalan: <code>/premium 123456789 30</code>",
                    parse_mode="HTML",
                )
                return
            target = int(parts[1])
            days = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else PREMIUM_DAYS
            expires_at = await asyncio.to_thread(grant, target, days)
            await bot.send_message(m.chat.id, f"✅ {target} ga premium berildi: {fmt_date(expires_at)} gacha")
            try:
                await bot.send_message(
                    target,
                    f"🎉 Premiumingiz faollashtirildi!\n📅 {fmt_date(expires_at)} gacha amal qiladi.",
                )
            except Exception:
                pass
        except Exception as e:
            logger.error(f"/premium xato: {e}")
            await bot.send_message(m.chat.id, f"❌ Xatolik: {str(e)[:100]}")

    @bot.message_handler(commands=["unpremium"])
    async def cmd_unpremium(m):
        """/unpremium <user_id> — premiumni bekor qilish."""
        if m.from_user.id != OWNER_ID:
            return
        parts = (m.text or "").split()
        if len(parts) < 2 or not parts[1].isdigit():
            await bot.send_message(m.chat.id, "Foydalanish: /unpremium USER_ID")
            return
        await asyncio.to_thread(revoke, int(parts[1]))
        await bot.send_message(m.chat.id, f"✅ {parts[1]} ning premiumi bekor qilindi.")

    @bot.message_handler(func=lambda m: m.text == ADMIN_BUTTON_TEXT and m.from_user.id == OWNER_ID)
    async def admin_list(m):
        try:
            rows = await asyncio.to_thread(list_active)
            if not rows:
                await bot.send_message(m.chat.id, "💎 Hozircha faol premium foydalanuvchilar yo'q.")
                return
            lines = [f"💎 <b>Faol premium foydalanuvchilar: {len(rows)}</b>\n"]
            for uid, exp in rows:
                lines.append(f"• <a href=\"tg://user?id={uid}\">{uid}</a> — {fmt_date(exp)} gacha")
            lines.append("\nQo'lda berish: <code>/premium ID [kun]</code>\nBekor qilish: <code>/unpremium ID</code>")
            await bot.send_message(m.chat.id, "\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Premium ro'yxati xato: {e}")

    # ---------- MP3 yuklab olish tugmasi ----------
    @bot.callback_query_handler(func=lambda c: c.data.startswith("dl_"))
    async def cb_download(call):
        cid = call.message.chat.id
        tag = call.data[3:]
        try:
            if not re.fullmatch(r"[0-9a-f]{8}", tag):
                await bot.answer_callback_query(call.id)
                return
            if not await asyncio.to_thread(is_premium, call.from_user.id):
                await bot.answer_callback_query(
                    call.id,
                    "💎 Ovozni MP3 qilib yuklab olish faqat Premium foydalanuvchilar uchun.\n\n"
                    "«💎 Premium sotib olish» tugmasini bosing.",
                    show_alert=True,
                )
                return
            path = os.path.join(TEMP_DIR, f"dl_{cid}_{tag}.mp3")
            if not os.path.exists(path):
                await bot.answer_callback_query(
                    call.id,
                    "⌛ Fayl muddati tugagan (10 daqiqa). Matnni qaytadan ovozga aylantiring.",
                    show_alert=True,
                )
                return
            await bot.answer_callback_query(call.id, "📥 Yuborilmoqda...")
            with open(path, "rb") as f:
                await bot.send_audio(cid, f, title="Ovoz", performer="Matn → Ovoz")
        except Exception as e:
            logger.error(f"Yuklab olish xato: {e}")


# ================= ESLATMA SIKLI =================
async def reminder_loop(bot):
    """Har 30 daqiqada tekshiradi: tugashiga 1 kun qolganlarga eslatma, tugaganlarga xabar."""
    await asyncio.sleep(20)
    while True:
        try:
            soon, expired = await asyncio.to_thread(pop_due_notifications)
            for uid, exp in soon:
                if uid == OWNER_ID:
                    continue
                try:
                    await bot.send_message(
                        uid,
                        "⏰ <b>Premiumingiz tugashiga 1 kundan kam qoldi!</b>\n\n"
                        f"📅 Tugash vaqti: <b>{fmt_date(exp)}</b>\n\n"
                        "Uzluksiz foydalanish uchun premiumni hoziroq uzaytiring. "
                        "Qo'shimcha 1 oy mavjud muddatga qo'shiladi 👇",
                        parse_mode="HTML",
                        reply_markup=renew_kb(),
                    )
                except Exception as e:
                    logger.info(f"Eslatma yuborilmadi {uid}: {e}")
                await asyncio.sleep(0.1)
            for uid, exp in expired:
                if uid == OWNER_ID:
                    continue
                try:
                    await bot.send_message(
                        uid,
                        "⌛ <b>Premium muddatingiz tugadi.</b>\n\n"
                        f"Siz bepul tarifga qaytdingiz: {FREE_MAX_WORDS} so'zgacha, kuniga {FREE_DAILY_LIMIT} ta so'rov.\n"
                        "Qayta faollashtirish uchun 👇",
                        parse_mode="HTML",
                        reply_markup=renew_kb(),
                    )
                except Exception as e:
                    logger.info(f"Tugash xabari yuborilmadi {uid}: {e}")
                await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Eslatma sikli xato: {e}")
        await asyncio.sleep(1800)
