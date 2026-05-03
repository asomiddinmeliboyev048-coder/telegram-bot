"""
MongoDB Atlas ma'lumotlar bazasi - Foydalanuvchilar bazasi uchun
Render.com muhiti uchun moslangan - doimiy saqlash
"""

import os
from datetime import datetime, timezone
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError

# MongoDB Atlas ulanish URI (xavfsiz saqlash uchun .env dan olish tavsiya etiladi)
MONGO_URI = os.getenv('MONGO_URI', 'mongodb+srv://asomiddinmeliboyev048_db_user:QsEe0c7kAg5JwzHX@cluster0.kjtun.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0')
DB_NAME = 'matnovoz_bot'

# Global client (bir marta ulanish)
_client = None
_db = None


def get_db():
    """MongoDB bazasiga ulanish (singleton pattern)"""
    global _client, _db
    if _client is None:
        try:
            _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
            _db = _client[DB_NAME]
            # Unique index yaratish - duplikat ID larni oldini olish uchun
            _db.users.create_index([('user_id', ASCENDING)], unique=True)
            print("✅ MongoDB Atlas ga ulanish muvaffaqiyatli!")
        except Exception as e:
            print(f"❌ MongoDB ulanish xatosi: {e}")
            raise
    return _db


def add_user(user_id, username=None, first_name=None, last_name=None):
    """
    Foydalanuvchini MongoDB ga qo'shish
    Agar mavjud bo'lsa, faqat last_active yangilanadi
    """
    try:
        db = get_db()
        users = db.users
        
        now = datetime.now(timezone.utc)
        
        # upsert - agar mavjud bo'lsa update, yo'q bo'lsa insert
        result = users.update_one(
            {'user_id': user_id},
            {
                '$setOnInsert': {
                    'user_id': user_id,
                    'joined_date': now,
                    'username': username,
                    'first_name': first_name,
                    'last_name': last_name
                },
                '$set': {
                    'last_active': now
                }
            },
            upsert=True
        )
        
        if result.upserted_id:
            print(f"✅ Yangi foydalanuvchi qo'shildi: {user_id}")
        else:
            print(f"✅ Foydalanuvchi yangilandi: {user_id}")
        
        return True
    except Exception as e:
        print(f"❌ add_user xatosi: {e}")
        return False


def get_all_users():
    """Barcha foydalanuvchilarning ID larini olish"""
    try:
        db = get_db()
        users = db.users
        
        # joined_date bo'yicha saralash (eng yangilar birinchi)
        cursor = users.find({}, {'user_id': 1}).sort('joined_date', -1)
        return [doc['user_id'] for doc in cursor]
    except Exception as e:
        print(f"❌ get_all_users xatosi: {e}")
        return []


def get_users_count():
    """Foydalanuvchilar sonini olish"""
    try:
        db = get_db()
        return db.users.count_documents({})
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
        
        base_dir = os.path.dirname(os.path.abspath(__file__))
        filepath = os.path.join(base_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            for user_id in users:
                f.write(f"{user_id}\n")
        
        return True, filepath
    except Exception as e:
        return False, str(e)


def get_user_stats():
    """Batafsil statistikani olish"""
    try:
        db = get_db()
        users = db.users
        
        # Jami foydalanuvchilar
        total = users.count_documents({})
        
        # Bugun qo'shilganlar (UTC vaqt)
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_count = users.count_documents({'joined_date': {'$gte': today_start}})
        
        # Bu hafta qo'shilganlar (so'nggi 7 kun)
        from datetime import timedelta
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        week_count = users.count_documents({'joined_date': {'$gte': week_ago}})
        
        return {
            'total': total,
            'today': today_count,
            'week': week_count
        }
    except Exception as e:
        print(f"❌ get_user_stats xatosi: {e}")
        return {'total': 0, 'today': 0, 'week': 0}


# Dastur ishga tushganda ulanishni tekshirish
try:
    get_db()
except Exception as e:
    print(f"⚠️ Database initialization error: {e}")
