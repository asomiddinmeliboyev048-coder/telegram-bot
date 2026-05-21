"""
migrate.py - Eski foydalanuvchi ID larini Firebase'ga yuklash
Faqat bir marta ishga tushiriladi.
"""

import time
import os
import sys

# database.py dan import
from database import firebase_initialized, firebase_save_user, firebase_get_user_count

# ============================================================
# ESKI FOYDALANUVCHILAR ID MASSIVI
# ============================================================
legacy_ids = [
# 1-fayl a'zolari
    8321578978, 942753678, 8360261809, 7512768284, 6868661227, 8323516201, 
    7196969437, 8689618337, 8632005007, 8551363999, 8145983898, 1024338788, 
    6685136442, 7405056168, 6091963464, 1960508754, 8445023329, 8299740346, 
    7267778765, 8528827957, 8586829934, 7292862722, 665407362, 8205021615, 
    7698561732, 7219814688, 7516003211, 8568561009, 5905504748, 6877118717, 
    7171330738, 8511132751, 6158973858,
    
    # 2-fayl a'zolari
    6130389200, 8040808452, 7676208648, 7878959269, 8018260855, 5327879038, 
    8036916783, 859066173, 5713082891, 7507154179, 7976133765, 1051448649, 
    8438646282, 8286347184, 8465929688, 5308511001, 7914586751, 7233071316, 
    8060416109, 734864473, 7572056616, 6280104193, 8661149011, 8189999676, 
    8364214289, 6327266007, 8171774847, 7036435276, 7493815069, 8488252914,
    
    # 3-fayl a'zolari
    8612678577, 7569021597, 8460907048, 8284440629, 6802303101, 7329543608, 
    7096838866, 6470788440, 7573284161, 6359869852, 7960884282, 6975253235, 
    1228442296, 8747317722, 8256161274, 6781057110, 8700433267, 7065778039, 
    6151702783, 8594420959, 2026176142, 8709883509, 1986930149, 8486965412, 
    8295657224, 7535655640, 8385188400, 8536382293, 8418746787, 7914516298, 
    7863553680, 8227213393, 8580211613, 5092064107, 8262425762, 8483978839, 
    8239168931, 8295989784, 8137683721, 1252702416, 6995506329, 8181111198, 
    7167533016, 5746861179, 7935832679, 6049678898, 1270454510, 8512006868, 
    8030496668, 8508586137, 5747213362, 5486665977, 5345534257, 7200425215, 
    1543257219, 8399587091, 6983208045, 7731118736, 430098321, 8359640442, 
    7966528160, 8437723134, 6741501415, 8277893731, 8546270738, 8485602169, 
    6887183462, 7081044500, 7991506474, 7784964349, 8308452323, 7942247483, 
    8178032388, 7907038297, 8001896395, 5988905263, 5447925243, 1764469411, 
    7689744698, 5910222182, 6288056924, 8027907966, 8058418828, 8298407403, 
    6601452911, 7904104944, 8376681932, 7697692266, 8355113011, 6675774105, 
    6368107043, 8675116492, 6572592251, 6620143299, 6364189416, 981094720, 
    7680412736, 1433291293, 8016838883, 5410029059, 815240658, 8021592424, 
    1974946603, 8589492788, 8522628266, 7509738126, 5688107244, 8592149749, 
    8532337171, 8630501558, 6003965253, 6721639364, 7927071961, 6185703505, 
    7742862140, 8209930443, 6079220173, 8224768804, 8728536002, 8150450045, 
    5018073874, 5499320786, 7802471410, 7910445568, 6453836200, 7806137282, 
    8615941579, 5999642304, 8523658834, 8648710822,
    
    # 4-fayl a'zolari
    7948607112, 7901308488, 5927796090, 8335470480, 7434318015, 8088800448, 
    8526343839, 7540892126, 7387632319, 6789427776, 7849673956, 8047378368, 
    8665483299, 7060847211, 8714021824, 7818356907, 7615927344, 6735980383, 
    5704215509, 7750527012, 8104986543, 6470337168, 6829563696, 8350792146, 
    6933247844, 8796826733, 8241757908, 8166081441, 8078608147, 8625832272, 
    6075526001, 5537675930, 7541050703, 8576169115, 6528209840, 8453523865, 
    8090277445, 8066137303, 8212630828, 8413096973, 8266064668, 6994663656, 
    8698225878, 7958789187, 906032235, 8026724525, 6643418936, 6561194346, 
    7527655228, 5215452067, 8184518280, 8783708336, 8661146846, 8239583077, 
    6487292306, 7148092847, 8550831414, 1234564726, 6430107632, 624114378, 
    8526105359, 7385969119, 8420053248, 7386421113, 6092350090
]


def migrate_legacy_users():
    """Barcha eski foydalanuvchi ID larini Firebase'ga yuklash"""

    if not firebase_initialized:
        print("❌ XATOLIK: Firebase ulanmagan!")
        print("   → serviceAccountKey.json fayli mavjudligini tekshiring")
        print("   → FIREBASE_DATABASE_URL to'g'ri ekanligini tekshiring")
        return False

    total      = len(legacy_ids)
    saved      = 0
    skipped    = 0
    errors     = 0

    print(f"\n{'='*55}")
    print(f"🚀 MIGRATSIYA BOSHLANDI: {total} ta ID Firebase'ga yuklanadi")
    print(f"{'='*55}\n")

    for idx, user_id in enumerate(legacy_ids, 1):
        # Noto'g'ri ID ni o'tkazib yuborish
        if not isinstance(user_id, int) or user_id <= 0:
            print(f"⚠️  [{idx:>4}/{total}] SKIP (noto'g'ri ID): {user_id}")
            skipped += 1
            continue

        try:
            result = firebase_save_user(user_id)

            if result:
                print(f"✅  [{idx:>4}/{total}] SAVED : {user_id}")
                saved += 1
            else:
                # Mavjud yoki last_active yangilandi
                print(f"⏭️  [{idx:>4}/{total}] EXISTS: {user_id}")
                skipped += 1

        except Exception as e:
            print(f"❌  [{idx:>4}/{total}] ERROR : {user_id} → {e}")
            errors += 1

        # Firebase API cheklovidan saqlanish uchun kichik pauza
        time.sleep(0.05)

        # Har 50 ta da progress ko'rsatish
        if idx % 50 == 0:
            print(f"\n📊 Progress: {idx}/{total} | Saqlandi: {saved} | Mavjud: {skipped} | Xato: {errors}\n")

    # Firebase'dagi umumiy sonni tekshirish
    final_count = firebase_get_user_count()

    print(f"\n{'='*55}")
    print(f"📊 MIGRATSIYA YAKUNLANDI:")
    print(f"   Jami ID      : {total}")
    print(f"   Yangi saqlandi: {saved}")
    print(f"   Mavjud edi   : {skipped}")
    print(f"   Xatolik      : {errors}")
    print(f"   Firebase jami: {final_count} ta user")
    print(f"{'='*55}\n")

    return True


if __name__ == "__main__":
    print("🔥 migrate.py ishga tushdi...")

    if not firebase_initialized:
        print("\n❌ Firebase ulanmagan. Tekshiruv:")
        print("   1. serviceAccountKey.json fayli mavjudmi?")
        print("   2. pip install firebase-admin")
        sys.exit(1)

    migrate_legacy_users()