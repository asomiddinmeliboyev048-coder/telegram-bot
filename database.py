"""
SQLite + Firebase Realtime Database Integration
Foydalanuvchilar bazasi - Render.com muhiti uchun moslangan
SQLite: /tmp/users.db da saqlanadi (local, vaqtinchalik)
Firebase: Cloud Realtime Database (doimiy saqlash)
"""

import sqlite3
import os
import time
from datetime import datetime

# ================== FIREBASE SETUP ==================
try:
    import firebase_admin
    from firebase_admin import credentials, db as firebase_db
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False
    print("⚠️ firebase-admin o'rnatilmagan. SQLite-only mode.")

firebase_app = None
firebase_initialized = False

def initialize_firebase():
    """Firebase Realtime Database initialization"""
    global firebase_app, firebase_initialized

    if not FIREBASE_AVAILABLE:
        print("⚠️ Firebase SDK mavjud emas. SQLite-only mode.")
        return False

    # Agar allaqachon boshlangan bo'lsa
    if firebase_initialized and firebase_app:
        return True

    try:
        KEY_PATH = os.getenv("FIREBASE_KEY_PATH", "./serviceAccountKey.json")
        DB_URL   = os.getenv(
            "FIREBASE_DATABASE_URL",
            "https://matnovozbot-eb09c-default-rtdb.europe-west1.firebasedatabase.app"
        )

        if not os.path.exists(KEY_PATH):
            print(f"⚠️ serviceAccountKey.json topilmadi: {KEY_PATH}. SQLite-only mode.")
            return False

        # Agar allaqachon initialize qilingan bo'lsa (reload holati)
        try:
            firebase_app = firebase_admin.get_app()
        except ValueError:
            cred = credentials.Certificate(KEY_PATH)
            firebase_app = firebase_admin.initialize_app(cred, {'databaseURL': DB_URL})

        firebase_initialized = True
        print(f"✅ Firebase ulandi! DB: {DB_URL}")
        return True

    except Exception as e:
        print(f"⚠️ Firebase ulanish xatosi: {e}. SQLite-only mode.")
        firebase_initialized = False
        return False


# ================== FIREBASE FUNCTIONS ==================

def firebase_user_exists(user_id):
    """Firebase'da user mavjudligini tekshirish"""
    try:
        if not FIREBASE_AVAILABLE or not firebase_initialized:
            return False
        ref = firebase_db.reference(f'users/{user_id}')
        return ref.get() is not None
    except Exception as e:
        print(f"⚠️ firebase_user_exists xato: {e}")
        return False


def firebase_save_user(user_id, username=None, first_name=None, last_name=None):
    """
    Firebase'ga foydalanuvchi saqlash.
    Yangi bo'lsa saqlaydi, mavjud bo'lsa last_active yangilanadi.
    """
    try:
        if not FIREBASE_AVAILABLE or not firebase_initialized:
            return False

        ref = firebase_db.reference(f'users/{user_id}')
        existing = ref.get()
        now = int(time.time())

        if existing is None:
            # Yangi foydalanuvchi
            user_data = {
                'user_id':    user_id,
                'username':   username   or 'N/A',
                'first_name': first_name or 'N/A',
                'last_name':  last_name  or 'N/A',
                'joined_date': now,
                'last_active': now,
            }
            ref.set(user_data)
            print(f"✅ Firebase: yangi user saqlandi ID={user_id}")
            return True
        else:
            # Mavjud foydalanuvchi - faqat last_active yangilanadi
            ref.update({'last_active': now})
            return False

    except Exception as e:
        print(f"⚠️ firebase_save_user xato: {e}")
        return False


def firebase_get_user_count():
    """Firebase'dagi jami foydalanuvchilar soni"""
    try:
        if not FIREBASE_AVAILABLE or not firebase_initialized:
            return 0
        ref = firebase_db.reference('users')
        all_users = ref.get()
        if all_users is None:
            return 0
        count = len(all_users)
        print(f"📊 Firebase user count: {count}")
        return count
    except Exception as e:
        print(f"⚠️ firebase_get_user_count xato: {e}")
        return 0


def firebase_get_all_user_ids():
    """Firebase'dan barcha user ID larni olish (export uchun)"""
    try:
        if not FIREBASE_AVAILABLE or not firebase_initialized:
            return []
        ref = firebase_db.reference('users')
        all_users = ref.get()
        if all_users is None:
            return []
        return [int(uid) for uid in all_users.keys()]
    except Exception as e:
        print(f"⚠️ firebase_get_all_user_ids xato: {e}")
        return []


# ================== SQLITE CONFIGURATION ==================
DATABASE_PATH = '/tmp/users.db' if os.name != 'nt' else 'users.db'


def get_connection():
    """SQLite ulanish"""
    return sqlite3.connect(DATABASE_PATH)


def ensure_table_exists():
    """Jadval mavjudligini tekshirish"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if not cursor.fetchone():
            print("🔄 Users jadvali yaratilmoqda...")
            cursor.execute('''
                CREATE TABLE users (
                    user_id    INTEGER PRIMARY KEY,
                    username   TEXT,
                    first_name TEXT,
                    last_name  TEXT,
                    joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('CREATE INDEX idx_user_id ON users(user_id)')
            conn.commit()
            print("✅ Users jadvali yaratildi!")
        conn.close()
        return True
    except Exception as e:
        print(f"❌ ensure_table_exists xato: {e}")
        return False


def init_db():
    """SQLite bazani yaratish"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id    INTEGER PRIMARY KEY,
            username   TEXT,
            first_name TEXT,
            last_name  TEXT,
            joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON users(user_id)')
    conn.commit()
    conn.close()
    print(f"✅ SQLite tayyor! ({DATABASE_PATH})")


def add_user(user_id, username=None, first_name=None, last_name=None):
    """
    Foydalanuvchini SQLite'ga qo'shish + Firebase'ga ham saqlash.
    INSERT OR IGNORE - mavjud bo'lsa qayta qo'shmaydi.
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            conn = get_connection()
            cursor = conn.cursor()

            cursor.execute('''
                INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
                VALUES (?, ?, ?, ?)
            ''', (user_id, username, first_name, last_name))

            is_new = cursor.rowcount > 0

            if not is_new:
                cursor.execute('''
                    UPDATE users
                    SET last_active = CURRENT_TIMESTAMP,
                        username   = COALESCE(?, username),
                        first_name = COALESCE(?, first_name),
                        last_name  = COALESCE(?, last_name)
                    WHERE user_id = ?
                ''', (username, first_name, last_name, user_id))
            else:
                print(f"✅ YANGI USER (SQLite): ID={user_id}, @{username or 'N/A'}")

            conn.commit()
            conn.close()

            # Firebase'ga ham saqlash (parallel)
            firebase_save_user(user_id, username, first_name, last_name)

            return True

        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < max_retries - 1:
                print(f"⚠️ Baza band, {attempt+1}. urinish...")
                time.sleep(1)
                continue
            print(f"❌ add_user SQLite xato: {e}")
            return False
        except Exception as e:
            print(f"❌ add_user xato: {e}")
            return False
    return False


def get_all_users():
    """SQLite'dan barcha user ID larni olish"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM users ORDER BY joined_date DESC')
        rows = cursor.fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception as e:
        print(f"❌ get_all_users xato: {e}")
        return []


def get_users_count():
    """SQLite'dagi user soni"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users')
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        print(f"❌ get_users_count xato: {e}")
        return 0


def export_users_to_txt():
    """
    Firebase'dan barcha user ID larni .txt ga eksport qilish.
    Firebase - doimiy ma'lumot bazasi, SQLite - vaqtinchalik.
    """
    export_path = '/tmp/users_export.txt'

    try:
        print("🔄 export_users_to_txt: Firebase'dan ID lar olinmoqda...")

        # Avval Firebase'dan olishga urinish (asosiy manba)
        firebase_ids = firebase_get_all_user_ids()

        if firebase_ids:
            user_ids = firebase_ids
            source = "Firebase"
            print(f"✅ Firebase'dan {len(user_ids)} ta user olindi")
        else:
            # Firebase bo'sh yoki ulanmagan - SQLite'dan olish
            print("⚠️ Firebase bo'sh, SQLite'dan olinmoqda...")
            ensure_table_exists()
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM users ORDER BY joined_date DESC')
            rows = cursor.fetchall()
            conn.close()
            user_ids = [r[0] for r in rows]
            source = "SQLite"
            print(f"✅ SQLite'dan {len(user_ids)} ta user olindi")

        if not user_ids:
            return False, "Bazada foydalanuvchilar yo'q"

        # Eski faylni o'chirish
        if os.path.exists(export_path):
            os.remove(export_path)

        # Faylga yozish
        with open(export_path, 'w', encoding='utf-8') as f:
            f.write(f"# Telega.io format - Foydalanuvchilar ID ro'yxati\n")
            f.write(f"# Manba: {source}\n")
            f.write(f"# Jami: {len(user_ids)} ta foydalanuvchi\n")
            f.write(f"# Sana: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
            f.write("#=====================================\n\n")
            for uid in user_ids:
                f.write(f"{uid}\n")

        if os.path.exists(export_path):
            size = os.path.getsize(export_path)
            print(f"✅ Eksport fayli: {export_path} ({size} bytes, {len(user_ids)} user, manba: {source})")
            return True, export_path
        else:
            return False, "Fayl yaratilmadi"

    except Exception as e:
        import traceback
        print(f"❌ export_users_to_txt xato: {traceback.format_exc()}")
        return False, str(e)


def get_user_stats():
    """Statistika: jami, bugun, hafta"""
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*) FROM users')
        total = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM users WHERE date(joined_date) = date('now')")
        today = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM users WHERE joined_date >= datetime('now', '-7 days')")
        week = cursor.fetchone()[0]

        conn.close()
        return {'total': total, 'today': today, 'week': week}
    except Exception as e:
        print(f"❌ get_user_stats xato: {e}")
        return {'total': 0, 'today': 0, 'week': 0}


# ================== INITIALIZATION ==================
print("🚀 Database initialization boshlandi...")

# SQLite
try:
    if not ensure_table_exists():
        init_db()
    print("✅ SQLite tayyor!")
except Exception as e:
    print(f"❌ SQLite init xato: {e}")

# Firebase
print("🔥 Firebase ulanmoqda...")
initialize_firebase()
if firebase_initialized:
    print("✅ Firebase tayyor!")
else:
    print("⚠️ Firebase ishlamaydi, faqat SQLite ishlaydi")