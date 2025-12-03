import os
import datetime
import requests
import logging
import random
import time
import sys
import json
import asyncio
from urllib.parse import urlparse

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# ==================== إعدادات Logging ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== الثوابت (تُقرأ من متغيرات البيئة) ====================
TOKEN = os.environ.get("TOKEN")
OWNER_ID_STR = os.environ.get("OWNER_ID") 
WEBHOOK_URL = os.environ.get("WEBHOOK_URL") 
PORT = int(os.environ.get('PORT', '10000'))
PAYMENT_QR_CODE_CONTENT = os.environ.get("PAYMENT_CODE", "f03c73ecadf2eda455d7be0732207d68") 
QR_CODE_IMAGE_FILE_ID = os.environ.get("QR_FILE_ID", "AgACAgQAAxkBAAMeaStcosjM_zUZZajf9YbiBqvP2V8AAicMaxs7hlhRo_6zeTTibMABAAMCAAN4AAM2BA") 
DATABASE_URL = os.environ.get('DATABASE_URL')

# ==================== روابط APIs ====================
SYRIAN_CITIES = {
    "دمشق": "Damascus", "حلب": "Aleppo", "حمص": "Homs", "حماة": "Hama", 
    "اللاذقية": "Latakia", "طرطوس": "Tartus", "دير الزور": "Deir Ez-Zor", 
    "الرقة": "Raqqa", "الحسكة": "Al-Hasakah", "درعا": "Daraa", 
    "السويداء": "As-Suwayda", "القنيطرة": "Quneitra", "إدلب": "Idlib", 
    "ريف دمشق": "Rif Dimashq"
}

BASE_PRAYER_API = "https://api.aladhan.com/v1/timingsByCity?city={city_en}&country=Syria&method=4"
BASE_WEATHER_API = "https://wttr.in/{city_en}_Syria?format=%C+%t+%w+%h"

# ==================== قوائم الأذكار ====================
AZKAR_SABAH_LIST = [
    "📌 <b>أذكار الصباح:</b>\n\nاللهم بك أصبحنا، وبك أمسينا، وبك نحيا، وبك نموت، وإليك النشور. (مرة واحدة)",
    "📌 <b>أذكار الصباح:</b>\n\nأَصْبَحْنَا وَأَصْبَحَ الْمُلْكُ لِلَّهِ وَالْحَمْدُ لِلَّهِ، لَا إِلَهَ إِلَّا اللَّهُ وَحْدَهُ لَا شَرِيكَ لَهُ، لَهُ الْمُلْكُ وَلَهُ الْحَمْدُ وَهُوَ عَلَى كُلِّ شَيْءٍ قَدِيرٌ. (مرة واحدة)",
    "📌 <b>أذكار الصباح:</b>\n\nيَا حَيُّ يَا قَيُّومُ بِرَحْمَتِكَ أَسْتَغِيثُ أَصْلِحْ لِي شَأْنِي كُلَّهُ وَلَا تَكِلْنِي إِلَى نَفسِي طَرْفَةَ عَيْنٍ. (مرة واحدة)"
]

AZKAR_DHUHR_LIST = [
    "📌 <b>أذكار الظهر:</b>\n\nسبحان الله وبحمده (100 مرة)",
    "📌 <b>أذكار الظهر:</b>\n\nلا إله إلا الله وحده لا شريك له، له الملك وله الحمد وهو على كل شيء قدير (10 مرات)",
    "📌 <b>أذكار الظهر:</b>\n\nأستغفر الله العظيم الذي لا إله إلا هو الحي القيوم وأتوب إليه (100 مرة)"
]

AZKAR_MASAA_LIST = [
    "📌 <b>أذكار المساء:</b>\n\nاللهم بك أمسينا، وبك أصبحنا، وبك نحيا، وبك نموت، وإليك المصير. (مرة واحدة)",
    "📌 <b>أذكار المساء:</b>\n\nأَمْسَيْنَا وَأَمْسَى الْمُلْكُ لِلَّهِ وَالْحَمْدُ لِلَّهِ، لَا إِلَهَ إِلَّا اللَّهُ وَحْدَهُ لَا شَرِيكَ لَهُ، لَهُ الْمُلْكُ وَلَهُ الْحَمْدُ وَهُوَ عَلَى كُلِّ شَيْءٍ قَدِيرٌ. (مرة واحدة)",
    "📌 <b>أذكار المساء:</b>\n\nأعوذ بكلمات الله التامات من شر ما خلق. (ثلاث مرات)"
]

# ==================== دوال قاعدة البيانات (asyncpg) ====================
async def get_db_connection():
    """إنشاء اتصال بقاعدة البيانات"""
    try:
        if DATABASE_URL:
            import asyncpg
            result = urlparse(DATABASE_URL)
            conn = await asyncpg.connect(
                user=result.username,
                password=result.password,
                host=result.hostname,
                port=result.port,
                database=result.path[1:]
            )
            logger.info("✅ تم الاتصال بـ PostgreSQL باستخدام asyncpg")
            return conn
        else:
            import sqlite3
            conn = sqlite3.connect("subscribers.db")
            logger.info("✅ تم الاتصال بـ SQLite")
            return conn
    except Exception as e:
        logger.error(f"❌ فشل الاتصال بقاعدة البيانات: {e}")
        raise

async def setup_db():
    """إنشاء الجدول إذا لم يكن موجوداً"""
    try:
        if DATABASE_URL:
            conn = await get_db_connection()
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    is_premium INTEGER DEFAULT 0,
                    end_date TEXT,
                    city_url TEXT DEFAULT NULL,
                    order_id TEXT DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.close()
        else:
            import sqlite3
            conn = sqlite3.connect("subscribers.db")
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    is_premium INTEGER DEFAULT 0,
                    end_date TEXT,
                    city_url TEXT DEFAULT NULL,
                    order_id TEXT DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            conn.close()
        logger.info("✅ تم إنشاء/تحقق من جدول users")
    except Exception as e:
        logger.error(f"❌ فشل في إعداد قاعدة البيانات: {e}")

async def get_premium_users():
    """الحصول على جميع المستخدمين المميزين"""
    try:
        if DATABASE_URL:
            conn = await get_db_connection()
            users = await conn.fetch("SELECT user_id, city_url FROM users WHERE is_premium = 1")
            await conn.close()
            return [(row['user_id'], row['city_url']) for row in users]
        else:
            import sqlite3
            conn = sqlite3.connect("subscribers.db")
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, city_url FROM users WHERE is_premium = 1")
            users = cursor.fetchall()
            conn.close()
            return users
    except Exception as e:
        logger.error(f"❌ فشل في جلب المستخدمين المميزين: {e}")
        return []

async def get_user_counts():
    """الحصول على إحصائيات المستخدمين"""
    try:
        if DATABASE_URL:
            conn = await get_db_connection()
            total = await conn.fetchval("SELECT COUNT(user_id) FROM users")
            premium = await conn.fetchval("SELECT COUNT(user_id) FROM users WHERE is_premium = 1")
            await conn.close()
            return total or 0, premium or 0
        else:
            import sqlite3
            conn = sqlite3.connect("subscribers.db")
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(user_id) FROM users")
            total_users = cursor.fetchone()[0] or 0
            cursor.execute("SELECT COUNT(user_id) FROM users WHERE is_premium = 1")
            premium_users = cursor.fetchone()[0] or 0
            conn.close()
            return total_users, premium_users
    except Exception as e:
        logger.error(f"❌ فشل في جلب إحصائيات المستخدمين: {e}")
        return 0, 0

async def save_user_city(user_id, city_url):
    """حفظ مدينة المستخدم"""
    try:
        if DATABASE_URL:
            conn = await get_db_connection()
            await conn.execute("""
                INSERT INTO users (user_id, city_url, is_premium) 
                VALUES ($1, $2, 0)
                ON CONFLICT (user_id) 
                DO UPDATE SET city_url = EXCLUDED.city_url
            """, user_id, city_url)
            await conn.close()
        else:
            import sqlite3
            conn = sqlite3.connect("subscribers.db")
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO users (user_id, city_url, is_premium) 
                VALUES (?, ?, 0)
            """, (user_id, city_url))
            conn.commit()
            conn.close()
        logger.info(f"✅ تم حفظ مدينة للمستخدم {user_id}")
        return True
    except Exception as e:
        logger.error(f"❌ فشل في حفظ مدينة للمستخدم {user_id}: {e}")
        return False

async def update_user_order(user_id, order_id):
    """تحديث رقم طلب المستخدم"""
    try:
        if DATABASE_URL:
            conn = await get_db_connection()
            await conn.execute("UPDATE users SET order_id = $1 WHERE user_id = $2", order_id, user_id)
            await conn.close()
        else:
            import sqlite3
            conn = sqlite3.connect("subscribers.db")
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET order_id = ? WHERE user_id = ?", (order_id, user_id))
            conn.commit()
            conn.close()
        return True
    except Exception as e:
        logger.error(f"❌ فشل في تحديث طلب المستخدم {user_id}: {e}")
        return False

async def activate_premium(user_id, order_id):
    """تفعيل الاشتراك المميز للمستخدم"""
    try:
        today_str = (datetime.date.today() + datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        if DATABASE_URL:
            conn = await get_db_connection()
            result = await conn.execute("""
                UPDATE users SET is_premium = 1, end_date = $1, order_id = NULL 
                WHERE user_id = $2 AND order_id = $3
            """, today_str, user_id, order_id)
            await conn.close()
            return "UPDATE" in str(result)
        else:
            import sqlite3
            conn = sqlite3.connect("subscribers.db")
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users SET is_premium = 1, end_date = ?, order_id = NULL 
                WHERE user_id = ? AND order_id = ?
            """, (today_str, user_id, order_id))
            conn.commit()
            success = cursor.rowcount > 0
            conn.close()
            return success
    except Exception as e:
        logger.error(f"❌ فشل في تفعيل الاشتراك للمستخدم {user_id}: {e}")
        return False

async def check_expiry_and_update():
    """فحص وإنهاء الاشتراكات المنتهية"""
    try:
        current_date_str = datetime.date.today().strftime("%Y-%m-%d")
        if DATABASE_URL:
            conn = await get_db_connection()
            result = await conn.execute("""
                UPDATE users SET is_premium = 0 
                WHERE end_date <= $1 AND is_premium = 1
            """, current_date_str)
            await conn.close()
            logger.info(f"✅ تم إنهاء اشتراكات منتهية: {result}")
        else:
            import sqlite3
            conn = sqlite3.connect("subscribers.db")
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users SET is_premium = 0 
                WHERE end_date <= ? AND is_premium = 1
            """, (current_date_str,))
            updated_rows = cursor.rowcount
            conn.commit()
            conn.close()
            logger.info(f"✅ تم إنهاء اشتراك {updated_rows} مستخدمين بتاريخ: {current_date_str}")
    except Exception as e:
        logger.error(f"❌ فشل تحديث الاشتراكات المنتهية: {e}")

async def get_user_by_order(order_id):
    """الحصول على المستخدم بواسطة رقم الطلب"""
    try:
        if DATABASE_URL:
            conn = await get_db_connection()
            result = await conn.fetchval("SELECT user_id FROM users WHERE order_id = $1", order_id)
            await conn.close()
            return result
        else:
            import sqlite3
            conn = sqlite3.connect("subscribers.db")
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users WHERE order_id = ?", (order_id,))
            result = cursor.fetchone()
            conn.close()
            return result[0] if result else None
    except Exception as e:
        logger.error(f"❌ فشل في جلب المستخدم بواسطة الطلب {order_id}: {e}")
        return None

async def get_user_city(user_id):
    """الحصول على مدينة المستخدم"""
    try:
        if DATABASE_URL:
            conn = await get_db_connection()
            result = await conn.fetchval("SELECT city_url FROM users WHERE user_id = $1", user_id)
            await conn.close()
            return result
        else:
            import sqlite3
            conn = sqlite3.connect("subscribers.db")
            cursor = conn.cursor()
            cursor.execute("SELECT city_url FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            conn.close()
            return result[0] if result else None
    except Exception as e:
        logger.error(f"❌ فشل في جلب مدينة المستخدم {user_id}: {e}")
        return None

def get_city_ar_from_url(url):
    """الحصول على اسم المدينة بالعربية من الـ URL"""
    if not url:
        return "مدينتك المختارة"
    for ar_name, en_name in SYRIAN_CITIES.items():
        if en_name in url:
            return ar_name
    return "مدينتك المختارة"

def get_city_en_from_url(url):
    """الحصول على اسم المدينة بالإنجليزية من الـ URL"""
    if not url:
        return "Damascus"
    for ar_name, en_name in SYRIAN_CITIES.items():
        if en_name in url:
            return en_name
    return "Damascus"

def generate_order_id(user_id):
    """إنشاء رقم طلب فريد"""
    return f"{int(time.time())}-{str(user_id)[-4:]}"

def get_weather_data(city_en):
    """جلب بيانات الطقس"""
    try:
        url = BASE_WEATHER_API.format(city_en=city_en)
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        weather_data = response.text.strip()
        
        # تحويل البيانات إلى تنسيق جميل
        parts = weather_data.split()
        if len(parts) >= 4:
            condition = parts[0]
            temperature = parts[1]
            wind = parts[2]
            humidity = parts[3]
            
            city_ar = get_city_ar_from_url(BASE_PRAYER_API.format(city_en=city_en))
            
            weather_report = (
                f"🌤️ <b>حالة الطقس في {city_ar}</b>\n\n"
                f"☁️ <b>الحالة:</b> {condition}\n"
                f"🌡️ <b>درجة الحرارة:</b> {temperature}\n"
                f"💨 <b>سرعة الرياح:</b> {wind}\n"
                f"💧 <b>الرطوبة:</b> {humidity}\n\n"
                f"<i>معلومات الطقس مقدمة من wttr.in</i>"
            )
            return weather_report
        else:
            return f"🌤️ <b>حالة الطقس:</b>\n\n{weather_data}"
    except Exception as e:
        logger.error(f"❌ فشل جلب بيانات الطقس لـ {city_en}: {e}")
        return f"❌ تعذر جلب بيانات الطقس للمحافظة المحددة"

# ==================== معالجات الأوامر ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    keyboard = []
    for city_ar, city_en in SYRIAN_CITIES.items():
        keyboard.append([InlineKeyboardButton(city_ar, callback_data=f"CITY_CHOICE_{city_en}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    context.user_data['waiting_for_city'] = True
    await update.message.reply_text(
        "👋 مرحباً بك في بوت الإشعارات المتميزة! 🕌\n\n"
        "لضمان دقة مواقيت الصلاة حسب منطقتك، <b>يرجى اختيار محافظتك أولاً</b>:\n"
        "<i>(هذه الخطوة مجانية ولا تفعل الاشتراك بعد)</i>",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def city_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if query.data.startswith("CITY_CHOICE_"):
        city_en = query.data.replace("CITY_CHOICE_", "")
        final_prayer_url = BASE_PRAYER_API.format(city_en=city_en)
        city_ar = get_city_ar_from_url(final_prayer_url)
        
        if await save_user_city(user_id, final_prayer_url):
            subscribe_keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("💰 تفعيل الاشتراك الآن", callback_data="ACTIVATE_ORDER")
            ]])
            await query.edit_message_text(
                f"🎉 <b>اختيارك لمحافظة {city_ar} تم بنجاح!</b> 🎉\n\n"
                f"<b>مميزات الاشتراك:</b>\n"
                f"🕋 مواقيت صلاة دقيقة لإشعاراتك\n"
                f"☀️ تقارير الطقس اليومية\n"
                f"📿 أذكار الصباح والمساء\n"
                f"🎰 سحب أسبوعي على 1000$\n\n"
                f"<b>💰 قيمة الاشتراك:</b> 1$ (أسبوع كامل)\n"
                f"<b>💳 طريقة الدفع:</b> شام كاش\n\n"
                f"اضغط على الزر أدناه لبدء عملية الدفع:",
                reply_markup=subscribe_keyboard,
                parse_mode='HTML'
            )
    
    elif query.data == "ACTIVATE_ORDER":
        await process_payment_request(update, context)

async def process_payment_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    city_url = await get_user_city(user_id)
    if not city_url:
        await query.edit_message_text("❌ لم يتم اختيار المحافظة بعد. يرجى البدء من جديد عبر /start.", parse_mode='HTML')
        return
    
    new_order_id = generate_order_id(user_id)
    
    if await update_user_order(user_id, new_order_id):
        city_ar = get_city_ar_from_url(city_url)
        user = query.from_user
        username_info = f"@{user.username} ({user.full_name})" if user.username else user.full_name
        
        owner_notification = (
            f"🔔 <b>طلب دفع جديد!</b>\n"
            f"🧑‍💻 <b>المستخدم:</b> {username_info}\n"
            f"📝 <b>رقم الطلب:</b> <code>{new_order_id}</code>\n"
            f"🗺️ <b>المحافظة:</b> {city_ar}\n"
            f"🔗 <b>رابط التأكيد:</b> <code>/as {new_order_id}</code>"
        )
        
        try:
            await context.bot.send_message(chat_id=int(OWNER_ID_STR), text=owner_notification, parse_mode='HTML')
        except Exception as e:
            logger.error(f"❌ فشل إرسال إشعار للمالك: {e}")
        
        final_message = (
            f"✅ <b>طلب الخدمة رقم: {new_order_id}</b>\n\n"
            f"<b>💰 قيمة الاشتراك:</b> 1$\n"
            f"<b>💳 طريقة الدفع:</b> شام كاش\n\n"
            f"<b>كود الدفع:</b>\n<code>{PAYMENT_QR_CODE_CONTENT}</code>\n\n"
            f"1. قم بالدفع عبر رمز QR أو نسخ الكود\n"
            f"2. أرسل إيصال الدفع للمالك\n"
            f"3. ستتم المفعلة فوراً"
        )
        
        await query.edit_message_text(final_message, parse_mode='HTML')
        
        if QR_CODE_IMAGE_FILE_ID:
            try:
                await context.bot.send_photo(chat_id=user_id, photo=QR_CODE_IMAGE_FILE_ID, caption="رمز QR للدفع عبر شام كاش")
            except Exception as e:
                logger.error(f"❌ فشل إرسال صورة QR: {e}")

async def confirm_payment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != int(OWNER_ID_STR):
        await update.message.reply_text("❌ هذا الأمر للمالك فقط.", parse_mode='HTML')
        return
    if not context.args:
        await update.message.reply_text("⚠️ يرجى تحديد رقم الطلب: <code>/as &lt;رقم_الطلب&gt;</code>", parse_mode='HTML')
        return
    order_id = context.args[0]
    user_id = await get_user_by_order(order_id)
    if not user_id:
        await update.message.reply_text(f"❌ لم يتم العثور على طلب: {order_id}", parse_mode='HTML')
        return
    if await activate_premium(user_id, order_id):
        try:
            await context.bot.send_message(chat_id=user_id, text=f"✅ <b>تم تفعيل اشتراكك بنجاح!</b>\nطلب رقم: {order_id}\nستصلك الإشعارات تلقائياً.", parse_mode='HTML')
        except Exception as e:
            logger.error(f"❌ فشل إرسال رسالة للمستخدم {user_id}: {e}")
        await update.message.reply_text(f"✅ تم تفعيل الاشتراك للمستخدم {user_id}", parse_mode='HTML')
    else:
        await update.message.reply_text(f"❌ فشل في تفعيل الاشتراك.", parse_mode='HTML')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != int(OWNER_ID_STR):
        await update.message.reply_text("❌ هذا الأمر للمالك فقط.", parse_mode='HTML')
        return
    total_users, premium_users = await get_user_counts()
    report = (
        f"📊 <b>إحصائيات المشتركين</b>\n\n"
        f"👤 <b>إجمالي المستخدمين:</b> {total_users}\n"
        f"⭐️ <b>المشتركين المميزين:</b> {premium_users}\n"
        f"📅 <b>التاريخ:</b> {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await update.message.reply_text(report, parse_mode='HTML')

async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    city_url = await get_user_city(user_id)
    if not city_url:
        await update.message.reply_text("❌ يرجى اختيار المحافظة أولاً عبر /start")
        return
    city_en = get_city_en_from_url(city_url)
    weather_report = get_weather_data(city_en)
    await update.message.reply_text(weather_report, parse_mode='HTML')

async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != int(OWNER_ID_STR):
        await update.message.reply_text("❌ هذا الأمر للمالك فقط.", parse_mode='HTML')
        return
    report_lines = []
    report_lines.append("🏥 <b>تقرير صحة البوت</b>")
    report_lines.append("=" * 40)
    total_users, premium_users = await get_user_counts()
    report_lines.append(f"🗄️ <b>قاعدة البيانات:</b> ✅ تعمل")
    report_lines.append(f"  • المستخدمين: {total_users}")
    report_lines.append(f"  • المميزين: {premium_users}")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_lines.append(f"🕐 <b>الوقت الحالي:</b> {now}")
    await update.message.reply_text("\n".join(report_lines), parse_mode='HTML')

async def get_file_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != int(OWNER_ID_STR):
        await update.message.reply_text("❌ هذا الأمر للمالك فقط.", parse_mode='HTML')
        return
    photo_file_id = None
    if update.message.reply_to_message and update.message.reply_to_message.photo:
        photo_file_id = update.message.reply_to_message.photo[-1].file_id
    elif update.message.photo:
        photo_file_id = update.message.photo[-1].file_id
    if not photo_file_id:
        await update.message.reply_text("⚠️ <b>لم يتم العثور على صورة.</b>", parse_mode='HTML')
        return
    await update.message.reply_text(f"✅ <b>File ID:</b>\n<code>{photo_file_id}</code>", parse_mode='HTML')

# ==================== دوال الجدولة ====================
async def send_single_prayer_notification(application: Application, user_id: int, prayer_name: str, city_name_ar: str):
    try:
        await application.bot.send_message(chat_id=user_id, text=f"🕋 <b>الله أكبر، الله أكبر.</b> حان الآن وقت صلاة <b>{prayer_name}</b> في محافظة <b>{city_name_ar}</b>.", parse_mode='HTML')
        logger.info(f"✅ إشعار صلاة {prayer_name} للمستخدم {user_id}")
    except Exception as e:
        logger.warning(f"⚠️ فشل إرسال إشعار صلاة للمستخدم {user_id}: {e}")

async def send_static_content(application: Application, content_list: list, content_type: str):
    if not content_list:
        return
    users = await get_premium_users()
    if not users:
        return
    message = random.choice(content_list)
    for user_id, _ in users:
        try:
            await application.bot.send_message(chat_id=user_id, text=message, parse_mode='HTML')
            await asyncio.sleep(0.5)
        except Exception:
            pass

async def send_weather_reports(application: Application):
    users = await get_premium_users()
    if not users:
        return
    for user_id, city_url in users:
        if not city_url:
            continue
        try:
            city_en = get_city_en_from_url(city_url)
            weather_report = get_weather_data(city_en)
            await application.bot.send_message(chat_id=user_id, text=weather_report, parse_mode='HTML')
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"❌ فشل إرسال تقرير الطقس للمستخدم {user_id}: {e}")

async def schedule_daily_prayer_notifications(application: Application):
    logger.info("🔄 جدولة إشعارات الصلاة اليومية")
    current_date = datetime.datetime.now().date()
    users_data = await get_premium_users()
    if not users_data:
        return
    
    PRAYER_FIELDS = {"الفجر": 'Fajr', "الظهر": 'Dhuhr', "العصر": 'Asr', "المغرب": 'Maghrib', "العشاء": 'Isha'}
    scheduler = application.bot_data.get('scheduler')
    
    for user_id, city_url in users_data:
        if not city_url:
            continue
        try:
            response = requests.get(city_url, timeout=10)
            times_data = response.json().get('data', {}).get('timings')
            if not times_data:
                continue
            city_name_ar = get_city_ar_from_url(city_url)
            for prayer_name_ar, prayer_key_en in PRAYER_FIELDS.items():
                time_str = times_data.get(prayer_key_en)
                if time_str:
                    try:
                        hour, minute = map(int, time_str.split(':'))
                        run_datetime = datetime.datetime(current_date.year, current_date.month, current_date.day, hour, minute, 0)
                        if run_datetime > datetime.datetime.now():
                            job_id = f"prayer_{user_id}_{prayer_key_en}_{current_date.strftime('%Y%m%d')}"
                            scheduler.add_job(send_single_prayer_notification, 'date', run_date=run_datetime, args=[application, user_id, prayer_name_ar, city_name_ar], id=job_id)
                    except:
                        pass
        except Exception as e:
            logger.error(f"❌ خطأ في جدولة الصلوات للمستخدم {user_id}: {e}")

async def schedule_daily_tasks(application: Application):
    scheduler = application.bot_data.get('scheduler')
    scheduler.add_job(lambda: send_static_content(application, AZKAR_SABAH_LIST, "أذكار الصباح"), 'cron', hour=6, minute=30, timezone='Asia/Damascus', id='azkar_sabah_daily')
    scheduler.add_job(send_weather_reports, 'cron', hour=8, minute=0, args=[application], timezone='Asia/Damascus', id='weather_reports_daily')
    scheduler.add_job(lambda: send_static_content(application, AZKAR_DHUHR_LIST, "أذكار الظهر"), 'cron', hour=13, minute=0, timezone='Asia/Damascus', id='azkar_dhuhr_daily')
    logger.info("✅ تم جدولة المهام اليومية")

async def post_init_callback(application: Application):
    logger.info("🚀 بدء تهيئة البوت")
    scheduler = AsyncIOScheduler(timezone='Asia/Damascus')
    application.bot_data['scheduler'] = scheduler
    scheduler.add_job(check_expiry_and_update, 'cron', hour=0, minute=5, timezone='Asia/Damascus', id='check_expiry_daily')
    scheduler.add_job(schedule_daily_prayer_notifications, 'cron', hour=1, minute=0, args=[application], timezone='Asia/Damascus', id='schedule_prayers_daily')
    scheduler.add_job(schedule_daily_tasks, 'date', run_date=datetime.datetime.now() + datetime.timedelta(seconds=10), args=[application], id='schedule_tasks_initial')
    scheduler.start()
    application.bot_data['scheduler_started'] = True
    logger.info("✅ تم بدء تشغيل Scheduler")

# ==================== الدالة الرئيسية ====================
def main():
    if not TOKEN or not OWNER_ID_STR or not WEBHOOK_URL:
        logger.error("❌ يجب تحديد متغيرات البيئة: TOKEN, OWNER_ID, WEBHOOK_URL")
        sys.exit(1)
    try:
        OWNER_ID = int(OWNER_ID_STR)
    except ValueError:
        logger.error("❌ OWNER_ID يجب أن يكون رقماً")
        sys.exit(1)
    
    # تشغيل إعداد قاعدة البيانات بشكل متزامن
    import asyncio
    asyncio.run(setup_db())
    
    application = Application.builder().token(TOKEN).post_init(post_init_callback).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("health", health_command))
    application.add_handler(CommandHandler("weather", weather_command))
    application.add_handler(CommandHandler("as", confirm_payment_command))
    application.add_handler(CommandHandler("getfileid", get_file_id_command))
    application.add_handler(CallbackQueryHandler(city_callback_handler))
    
    logger.info(f"🚀 بدء البوت على المنفذ {PORT}...")
    try:
        application.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f"{WEBHOOK_URL}/{TOKEN}", drop_pending_updates=True)
    except Exception as e:
        logger.error(f"❌ فشل في تشغيل البوت: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
