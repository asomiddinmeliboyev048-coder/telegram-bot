"""
SQLite ma'lumotlar bazasi - Foydalanuvchilar bazasi uchun
Render.com muhiti uchun moslangan - /tmp/users.db da saqlanadi
"""

import sqlite3
import os
from datetime import datetime

# SQLite bazaning joylashuvi - Render uchun /tmp (vaqtinchalik, lekin ishlaydi)
# Agar persistents disk bo'lsa, uni o'zgartirish mumkin
DATABASE_PATH = '/tmp/users.db'


def get_connection():
    """Bazaga ulanish yaratish"""
    return sqlite3.connect(DATABASE_PATH)


def init_db():
    """Ma'lumotlar bazasini yaratish (agar mavjud bo'lmasa)"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Foydalanuvchilar jadvalini yaratish
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Indeks yaratish - tezlik uchun
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_user_id ON users(user_id)
    ''')
    
    conn.commit()
    conn.close()
    print(f"✅ SQLite ma'lumotlar bazasi tayyor! ({DATABASE_PATH})")


def add_user(user_id, username=None, first_name=None, last_name=None):
    """
    Foydalanuvchini bazaga qo'shish
    INSERT OR IGNORE - agar mavjud bo'lsa, qayta qo'shmaydi
    """
    try:
        print(f"📝 add_user chaqirildi: user_id={user_id}")
        conn = get_connection()
        cursor = conn.cursor()
        
        # INSERT OR IGNORE - duplikat ID larni oldini oladi
        cursor.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
        ''', (user_id, username, first_name, last_name))
        
        # Agar foydalanuvchi allaqachon mavjud bo'lsa, faqat last_active ni yangilaymiz
        if cursor.rowcount == 0:
            cursor.execute('''
                UPDATE users 
                SET last_active = CURRENT_TIMESTAMP,
                    username = COALESCE(?, username),
                    first_name = COALESCE(?, first_name),
                    last_name = COALESCE(?, last_name)
                WHERE user_id = ?
            ''', (username, first_name, last_name, user_id))
            print(f"✅ Foydalanuvchi yangilandi: {user_id}")
        else:
            print(f"✅ Yangi foydalanuvchi qo'shildi: {user_id}")
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"❌ add_user xatosi: {e}")
        import traceback
        traceback.print_exc()
        return False


def get_all_users():
    """Barcha foydalanuvchilarning ID larini olish"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT user_id FROM users ORDER BY joined_date DESC')
        users = cursor.fetchall()
        
        conn.close()
        # [(123,), (456,)] -> [123, 456]
        return [user[0] for user in users]
    except Exception as e:
        print(f"❌ get_all_users xatosi: {e}")
        return []


def get_users_count():
    """Foydalanuvchilar sonini olish"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM users')
        count = cursor.fetchone()[0]
        
        conn.close()
        print(f"📊 Foydalanuvchilar soni: {count}")
        return count
    except Exception as e:
        print(f"❌ get_users_count xatosi: {e}")
        return 0


def export_users_to_txt(filename='users_export.txt'):
    """
    Barcha foydalanuvchi ID larini .txt faylga eksport qilish
    Telega.io uchun formatda: har bir ID alohida qatorda
    """
    try:
        users = get_all_users()
        
        if not users:
            return False, "Bazada foydalanuvchilar yo'q"
        
        # /tmp da saqlash (Render uchun)
        filepath = os.path.join('/tmp', filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            for user_id in users:
                f.write(f"{user_id}\n")
        
        print(f"✅ Eksport tayyor: {filepath} ({len(users)} ta foydalanuvchi)")
        return True, filepath
    except Exception as e:
        print(f"❌ export_users_to_txt xatosi: {e}")
        return False, str(e)


def get_user_stats():
    """Batafsil statistikani olish"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Jami foydalanuvchilar
        cursor.execute('SELECT COUNT(*) FROM users')
        total = cursor.fetchone()[0]
        
        # Bugun qo'shilganlar
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT COUNT(*) FROM users 
            WHERE date(joined_date) = date('now')
        ''')
        today_count = cursor.fetchone()[0]
        
        # Bu hafta qo'shilganlar
        cursor.execute('''
            SELECT COUNT(*) FROM users 
            WHERE joined_date >= datetime('now', '-7 days')
        ''')
        week_count = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total': total,
            'today': today_count,
            'week': week_count
        }
    except Exception as e:
        print(f"❌ get_user_stats xatosi: {e}")
        return {'total': 0, 'today': 0, 'week': 0}


# Baza birinchi marta yuklanganda avtomatik yaratish
print("🚀 SQLite database initialization...")
init_db()
