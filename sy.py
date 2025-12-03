import os
import datetime
import requests
import logging
import random
import time
import sys
import json
from urllib.parse import urlparse

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.base import JobLookupError

# ==================== إعدادات Logging ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== الثوابت (تُقرأ من متغيرات البيئة) ====================

# الرمز المميز الخاص بالبوت (مطلوب)
TOKEN = os.environ.get("TOKEN")
# رقم معرف المالك (مطلوب)
OWNER_ID_STR = os.environ.get("OWNER_ID") 
# رابط Render العام للخدمة (مطلوب للـ Webhooks)
WEBHOOK_URL = os.environ.get("WEBHOOK_URL") 
# يتم توفير PORT تلقائياً بواسطة Render
PORT = int(os.environ.get('PORT', '10000'))

# --- كود الدفع لخدمة الشام كاش ---
PAYMENT_QR_CODE_CONTENT = os.environ.get("PAYMENT_CODE", "f03c73ecadf2eda455d7be0732207d68") 

# 🚨 File ID لصورة QR كود 🚨
QR_CODE_IMAGE_FILE_ID = os.environ.get("QR_FILE_ID", "AgACAgQAAxkBAAMeaStcosjM_zUZZajf9YbiBqvP2V8AAicMaxs7hlhRo_6zeTTibMABAAMCAAN4AAM2BA") 

# رابط قاعدة البيانات - يستخدم PostgreSQL على Render
DATABASE_URL = os.environ.get('DATABASE_URL')

# الثوابت المتبقية لا تحتاج إلى تعديل
SYRIAN_CITIES = {
    "دمشق": "Damascus", "حلب": "Aleppo", "حمص": "Homs", "حماة": "Hama", 
    "اللاذقية": "Latakia", "طرطوس": "Tartus", "دير الزور": "Deir Ez-Zor", 
    "الرقة": "Raqqa", "الحسكة": "Al-Hasakah", "درعا": "Daraa", 
    "السويداء": "As-Suwayda", "القنيطرة": "Quneitra", "إدلب": "Idlib", 
    "ريف دمشق": "Rif Dimashq"
}
BASE_PRAYER_API = "https://api.aladhan.com/v1/timingsByCity?city={city_en}&country=Syria&method=4"

AZKAR_SABAH_LIST = [
    "📌 <b>أذكار الصباح:</b>\n\nاللهم بك أصبحنا، وبك أمسينا، وبك نحيا، وبك نموت، وإليك النشور. (مرة واحدة)",
    "📌 <b>أذكار الصباح:</b>\n\nأَصْبَحْنَا وَأَصْبَحَ الْمُلْكُ لِلَّهِ وَالْحَمْدُ لِلَّهِ، لَا إِلَهَ إِلَّا اللَّهُ وَحْدَهُ لَا شَرِيكَ لَهُ، لَهُ الْمُلْكُ وَلَهُ الْحَمْدُ وَهُوَ عَلَى كُلِّ شَيْءٍ قَدِيرٌ. (مرة واحدة)",
    "📌 <b>أذكار الصباح:</b>\n\nيَا حَيُّ يَا قَيُّومُ بِرَحْمَتِكَ أَسْتَغِيثُ أَصْلِحْ لِي شَأْنِي كُلَّهُ وَلَا تَكِلْنِي إِلَى نَفْسِي طَرْفَةَ عَيْنٍ. (مرة واحدة)"
]

AZKAR_MASAA_LIST = [
    "📌 <b>أذكار المساء:</b>\n\nاللهم بك أمسينا، وبك أصبحنا، وبك نحيا، وبك نموت، وإليك المصير. (مرة واحدة)",
    "📌 <b>أذكار المساء:</b>\n\nأَمْسَيْنَا وَأَمْسَى الْمُلْكُ لِلَّهِ وَالْحَمْدُ لِلَّهِ، لَا إِلَهَ إِلَّا اللَّهُ وَحْدَهُ لَا شَرِيكَ لَهُ، لَهُ الْمُلْكُ وَلَهُ الْحَمْدُ وَهُوَ عَلَى كُلِّ شَيْءٍ قَدِيرٌ. (مرة واحدة)",
    "📌 <b>أذكار المساء:</b>\n\nأعوذ بكلمات الله التامات من شر ما خلق. (ثلاث مرات)"
]

# ==================== دوال قاعدة البيانات (PostgreSQL/SQLite) ====================

def get_db_connection():
    """إنشاء اتصال بقاعدة البيانات (يدعم PostgreSQL و SQLite)"""
    try:
        if DATABASE_URL:
            # استخدام PostgreSQL (لـ Render)
            import psycopg2
            result = urlparse(DATABASE_URL)
            conn = psycopg2.connect(
                database=result.path[1:],
                user=result.username,
                password=result.password,
                host=result.hostname,
                port=result.port
            )
            logger.info("✅ تم الاتصال بـ PostgreSQL")
        else:
            # استخدام SQLite (للتطوير المحلي)
            import sqlite3
            conn = sqlite3.connect("subscribers.db")
            logger.info("✅ تم الاتصال بـ SQLite (تطوير محلي)")
        return conn
    except Exception as e:
        logger.error(f"❌ فشل الاتصال بقاعدة البيانات: {e}")
        raise

def setup_db():
    """إنشاء الجدول إذا لم يكن موجوداً"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # SQL مختلفة لـ PostgreSQL و SQLite
        if DATABASE_URL:
            # PostgreSQL
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    is_premium INTEGER DEFAULT 0,
                    end_date TEXT,
                    city_url TEXT DEFAULT NULL,
                    order_id TEXT DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            # SQLite
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
        logger.info("✅ تم إنشاء/تحقق من جدول users")
        
    except Exception as e:
        logger.error(f"❌ فشل في إعداد قاعدة البيانات: {e}")
        raise
    finally:
        if conn:
            conn.close()

def get_premium_users():
    """الحصول على جميع المستخدمين المميزين"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, city_url FROM users WHERE is_premium = 1")
        users = cursor.fetchall()
        return users
    except Exception as e:
        logger.error(f"❌ فشل في جلب المستخدمين المميزين: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_user_counts():
    """الحصول على إحصائيات المستخدمين"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(user_id) FROM users")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(user_id) FROM users WHERE is_premium = 1")
        premium_users = cursor.fetchone()[0]
        return total_users, premium_users
    except Exception as e:
        logger.error(f"❌ فشل في جلب إحصائيات المستخدمين: {e}")
        return 0, 0
    finally:
        if conn:
            conn.close()

def save_user_city(user_id, city_url):
    """حفظ مدينة المستخدم"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if DATABASE_URL:
            # PostgreSQL
            cursor.execute("""
                INSERT INTO users (user_id, city_url, is_premium) 
                VALUES (%s, %s, 0)
                ON CONFLICT (user_id) 
                DO UPDATE SET city_url = EXCLUDED.city_url
            """, (user_id, city_url))
        else:
            # SQLite
            cursor.execute("""
                INSERT OR REPLACE INTO users (user_id, city_url, is_premium) 
                VALUES (?, ?, 0)
            """, (user_id, city_url))
        
        conn.commit()
        logger.info(f"✅ تم حفظ مدينة للمستخدم {user_id}")
        return True
    except Exception as e:
        logger.error(f"❌ فشل في حفظ مدينة للمستخدم {user_id}: {e}")
        return False
    finally:
        if conn:
            conn.close()

def update_user_order(user_id, order_id):
    """تحديث رقم طلب المستخدم"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if DATABASE_URL:
            cursor.execute("UPDATE users SET order_id = %s WHERE user_id = %s", (order_id, user_id))
        else:
            cursor.execute("UPDATE users SET order_id = ? WHERE user_id = ?", (order_id, user_id))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"❌ فشل في تحديث طلب المستخدم {user_id}: {e}")
        return False
    finally:
        if conn:
            conn.close()

def activate_premium(user_id, order_id):
    """تفعيل الاشتراك المميز للمستخدم"""
    conn = None
    try:
        today_str = (datetime.date.today() + datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if DATABASE_URL:
            cursor.execute("""
                UPDATE users SET is_premium = 1, end_date = %s, order_id = NULL 
                WHERE user_id = %s AND order_id = %s
            """, (today_str, user_id, order_id))
        else:
            cursor.execute("""
                UPDATE users SET is_premium = 1, end_date = ?, order_id = NULL 
                WHERE user_id = ? AND order_id = ?
            """, (today_str, user_id, order_id))
        
        conn.commit()
        success = cursor.rowcount > 0
        if success:
            logger.info(f"✅ تم تفعيل الاشتراك للمستخدم {user_id}")
        return success
    except Exception as e:
        logger.error(f"❌ فشل في تفعيل الاشتراك للمستخدم {user_id}: {e}")
        return False
    finally:
        if conn:
            conn.close()

def check_expiry_and_update():
    """فحص وإنهاء الاشتراكات المنتهية"""
    conn = None
    try:
        current_date_str = datetime.date.today().strftime("%Y-%m-%d")
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if DATABASE_URL:
            cursor.execute("""
                UPDATE users SET is_premium = 0 
                WHERE end_date <= %s AND is_premium = 1
            """, (current_date_str,))
        else:
            cursor.execute("""
                UPDATE users SET is_premium = 0 
                WHERE end_date <= ? AND is_premium = 1
            """, (current_date_str,))
        
        updated_rows = cursor.rowcount
        conn.commit()
        logger.info(f"✅ تم إنهاء اشتراك {updated_rows} مستخدمين بتاريخ: {current_date_str}")
    except Exception as e:
        logger.error(f"❌ فشل تحديث الاشتراكات المنتهية: {e}")
    finally:
        if conn:
            conn.close()

def get_user_by_order(order_id):
    """الحصول على المستخدم بواسطة رقم الطلب"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if DATABASE_URL:
            cursor.execute("SELECT user_id FROM users WHERE order_id = %s", (order_id,))
        else:
            cursor.execute("SELECT user_id FROM users WHERE order_id = ?", (order_id,))
        
        result = cursor.fetchone()
        return result[0] if result else None
    except Exception as e:
        logger.error(f"❌ فشل في جلب المستخدم بواسطة الطلب {order_id}: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_user_city(user_id):
    """الحصول على مدينة المستخدم"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if DATABASE_URL:
            cursor.execute("SELECT city_url FROM users WHERE user_id = %s", (user_id,))
        else:
            cursor.execute("SELECT city_url FROM users WHERE user_id = ?", (user_id,))
        
        result = cursor.fetchone()
        return result[0] if result else None
    except Exception as e:
        logger.error(f"❌ فشل في جلب مدينة المستخدم {user_id}: {e}")
        return None
    finally:
        if conn:
            conn.close()

def get_city_ar_from_url(url):
    """الحصول على اسم المدينة بالعربية من الـ URL"""
    if not url:
        return "مدينتك المختارة"
    
    for ar_name, en_name in SYRIAN_CITIES.items():
        if en_name in url:
            return ar_name
    return "مدينتك المختارة"

def generate_order_id(user_id):
    """إنشاء رقم طلب فريد"""
    return f"{int(time.time())}-{str(user_id)[-4:]}"

def get_prayer_api_status():
    """فحص اتصال API الأذان"""
    test_url = BASE_PRAYER_API.format(city_en="Damascus")
    
    try:
        response = requests.get(test_url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data and data.get('data') and data.get('data').get('timings'):
            return True, "✅ API يعمل بشكل صحيح"
        else:
            return False, f"⚠️ API يعمل لكن البيانات غير صالحة"
    except requests.exceptions.RequestException as e:
        return False, f"❌ فشل الاتصال بالـ API: {e}"
    except Exception as e:
        return False, f"❌ خطأ غير متوقع: {e}"

# ==================== معالجات الأوامر والأزرار ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أمر /start"""
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
    """معالجة اختيار المدينة"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if query.data.startswith("CITY_CHOICE_"):
        city_en = query.data.replace("CITY_CHOICE_", "")
        final_prayer_url = BASE_PRAYER_API.format(city_en=city_en)
        city_ar = get_city_ar_from_url(final_prayer_url)
        
        # حفظ مدينة المستخدم
        if save_user_city(user_id, final_prayer_url):
            subscribe_keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("💰 تفعيل الاشتراك الآن", callback_data="ACTIVATE_ORDER")
            ]])
            
            await query.edit_message_text(
                f"🎉 <b>اختيارك لمحافظة {city_ar} تم بنجاح!</b> 🎉\n\n"
                f"الآن أنت جاهز للانطلاق نحو خدمة الإشعارات الدينية المتميزة.\n\n"
                f"🕋 <b>دقة لا مثيل لها:</b> مواقيت صلاة وإشعارات مُخصصة بالثانية.\n"
                f"✨ <b>إثراء روحي يومي:</b> استلام الأذكار والأوراد الصباحية والمسائية تلقائياً.\n\n"
                f"--- <b>فرصة العمر: السحب الأسبوعي!</b> ---\n"
                f"كل عملية شراء لهذه الخدمة تدخل اسمك <b>مباشرةً في السحب الأسبوعي</b> للفوز بـ <b>جوائز نقدية تصل قيمتها إلى 1000$!</b>\n\n"
                f"--- <b>لتفعيل الخدمة والمتابعة</b> ---\n"
                f"<b>💰 قيمة الاشتراك:</b> 1$ USD.\n"
                f"<b>💳 طريقة الدفع:</b> شام كاش (Sham Cash).\n\n"
                f"اضغط على الزر أدناه للحصول على <b>رقم طلبك</b> وبدء عملية الدفع:",
                reply_markup=subscribe_keyboard,
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text(
                "❌ حدث خطأ في حفظ اختيارك. يرجى المحاولة مرة أخرى.",
                parse_mode='HTML'
            )
    
    elif query.data == "ACTIVATE_ORDER":
        await process_payment_request(update, context)

async def process_payment_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة طلب الدفع"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # التحقق من وجود المدينة
    city_url = get_user_city(user_id)
    if not city_url:
        await query.edit_message_text(
            "❌ لم يتم اختيار المحافظة بعد. يرجى البدء من جديد عبر /start.",
            parse_mode='HTML'
        )
        return
    
    # إنشاء رقم طلب
    new_order_id = generate_order_id(user_id)
    
    # تحديث قاعدة البيانات
    if update_user_order(user_id, new_order_id):
        city_ar = get_city_ar_from_url(city_url)
        
        # إرسال إشعار للمالك
        user = query.from_user
        username_info = f"@{user.username} ({user.full_name})" if user.username else user.full_name
        
        owner_notification = (
            f"🔔 <b>طلب دفع جديد!</b>\n"
            f"----------------------------------\n"
            f"🧑‍💻 <b>المستخدم:</b> {username_info} (ID: <code>{user_id}</code>)\n"
            f"📝 <b>رقم الطلب:</b> <code>{new_order_id}</code>\n"
            f"🗺️ <b>المحافظة:</b> {city_ar}\n"
            f"🔗 <b>رابط تأكيد الدفع:</b> <code>/as {new_order_id}</code>\n"
            f"----------------------------------\n"
            f"يرجى مراجعة إيصال الدفع."
        )
        
        try:
            await context.bot.send_message(
                chat_id=int(OWNER_ID_STR),
                text=owner_notification,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"❌ فشل إرسال إشعار للمالك: {e}")
        
        # إرسال التعليمات للمستخدم
        final_message = (
            f"✅ <b>خطوتك الأخيرة لتفعيل الخدمة!</b>\n\n"
            f"--- <b>طلب الخدمة رقم: {new_order_id}</b> ---\n\n"
            f"<b>💰 قيمة الاشتراك:</b> 1$ (USD).\n"
            f"<b>💳 طريقة الدفع:</b> شام كاش (Sham Cash).\n\n"
            f"<b>1. قم بالدفع:</b>\n"
            f"لاستكمال الدفع، يرجى مسح رمز QR الذي سيتم إرساله أدناه أو نسخ الكود:\n"
            f"<b>كود الدفع:</b>\n"
            f"<code>{PAYMENT_QR_CODE_CONTENT}</code>\n\n"
            f"<b>2. إرسال الإيصال:</b>\n"
            f"أرسل صورة <b>إيصال الدفع</b> إلى المالك ليقوم بالتأكيد والتفعيل فوراً.\n"
            f"<b>⚠️ هام:</b> لا تحتاج لإرسال رقم الطلب يدوياً للمالك."
        )
        
        await query.edit_message_text(final_message, parse_mode='HTML')
        
        # إرسال صورة QR Code
        if QR_CODE_IMAGE_FILE_ID:
            try:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=QR_CODE_IMAGE_FILE_ID,
                    caption="هذا هو رمز QR الخاص بالدفع. يرجى مسحه ضوئياً لإكمال عملية الدفع عبر شام كاش.",
                )
            except Exception as e:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"⚠️ <b>خطأ في إرسال صورة QR</b>: يرجى نسخ الكود أعلاه مباشرةً:\n<code>{PAYMENT_QR_CODE_CONTENT}</code>",
                    parse_mode='HTML'
                )
                logger.error(f"❌ فشل إرسال صورة QR للمستخدم {user_id}: {e}")
    else:
        await query.edit_message_text(
            "❌ حدث خطأ في إنشاء رقم الطلب. يرجى المحاولة مرة أخرى.",
            parse_mode='HTML'
        )

async def confirm_payment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تأكيد الدفع من قبل المالك"""
    if update.effective_user.id != int(OWNER_ID_STR):
        await update.message.reply_text("❌ هذا الأمر للمالك فقط.", parse_mode='HTML')
        return
    
    if not context.args:
        await update.message.reply_text(
            "⚠️ يرجى تحديد رقم الطلب:\n<code>/as &lt;رقم_الطلب&gt;</code>",
            parse_mode='HTML'
        )
        return
    
    order_id = context.args[0]
    
    # الحصول على المستخدم من رقم الطلب
    user_id = get_user_by_order(order_id)
    
    if not user_id:
        await update.message.reply_text(
            f"❌ لم يتم العثور على أي مستخدم مرتبط برقم الطلب: {order_id}",
            parse_mode='HTML'
        )
        return
    
    # تفعيل الاشتراك
    if activate_premium(user_id, order_id):
        # إرسال رسالة للمستخدم
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"✅ <b>تم تفعيل اشتراكك بنجاح!</b>\n\n"
                    f"لقد تم تأكيد دفعك لطلب رقم <b>{order_id}</b>.\n"
                    f"ستبدأ الآن باستلام إشعارات الصلاة والأذكار وفقاً لتوقيت محافظتك، "
                    f"وتم إدخالك في السحب الأسبوعي على 1000$!"
                ),
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"❌ فشل إرسال رسالة للمستخدم {user_id}: {e}")
            await update.message.reply_text(
                f"✅ تم تفعيل الاشتراك للمستخدم {user_id} ولكن فشل إرسال الإشعار له.",
                parse_mode='HTML'
            )
            return
        
        await update.message.reply_text(
            f"✅ تم تفعيل الاشتراك للمستخدم ID: {user_id} بنجاح.\n"
            f"تم إرسال رسالة تأكيد له.",
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(
            f"❌ فشل في تفعيل الاشتراك. تأكد من رقم الطلب أو أن الدفع تم تأكيده مسبقاً.",
            parse_mode='HTML'
        )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إحصائيات المشتركين"""
    if update.effective_user.id != int(OWNER_ID_STR):
        await update.message.reply_text("❌ هذا الأمر للمالك فقط.", parse_mode='HTML')
        return
    
    try:
        total_users, premium_users = get_user_counts()
        
        report = (
            f"📊 <b>إحصائيات المشتركين</b>\n\n"
            f"👤 <b>إجمالي المستخدمين المسجلين:</b> {total_users}\n"
            f"⭐️ <b>المشتركين المميزين (نشطين):</b> {premium_users}\n"
            f"📅 <b>التاريخ:</b> {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        await update.message.reply_text(report, parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"❌ فشل في جلب الإحصائيات: {e}")
        await update.message.reply_text("❌ حدث خطأ في جلب الإحصائيات.", parse_mode='HTML')

async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فحص صحة البوت"""
    if update.effective_user.id != int(OWNER_ID_STR):
        await update.message.reply_text("❌ هذا الأمر للمالك فقط.", parse_mode='HTML')
        return
    
    report_lines = []
    report_lines.append("🏥 <b>تقرير صحة البوت</b>")
    report_lines.append("=" * 40)
    
    # فحص متغيرات البيئة
    report_lines.append("🔑 <b>متغيرات البيئة:</b>")
    report_lines.append(f"  • TOKEN: {'✅ موجود' if TOKEN else '❌ مفقود'}")
    report_lines.append(f"  • OWNER_ID: {'✅ ' + OWNER_ID_STR if OWNER_ID_STR else '❌ مفقود'}")
    report_lines.append(f"  • WEBHOOK_URL: {'✅ موجود' if WEBHOOK_URL else '❌ مفقود'}")
    report_lines.append(f"  • DATABASE_URL: {'✅ PostgreSQL' if DATABASE_URL else '✅ SQLite (تطوير)'}")
    
    # فحص قاعدة البيانات
    try:
        total_users, premium_users = get_user_counts()
        report_lines.append(f"🗄️ <b>قاعدة البيانات:</b> ✅ تعمل")
        report_lines.append(f"  • المستخدمين: {total_users}")
        report_lines.append(f"  • المميزين: {premium_users}")
    except Exception as e:
        report_lines.append(f"🗄️ <b>قاعدة البيانات:</b> ❌ خطأ: {str(e)[:50]}")
    
    # فحص API
    api_ok, api_msg = get_prayer_api_status()
    report_lines.append(f"🌐 <b>API الأذان:</b> {'✅ يعمل' if api_ok else '❌ لا يعمل'}")
    if not api_ok:
        report_lines.append(f"  • التفاصيل: {api_msg}")
    
    # فحص Scheduler
    scheduler = context.application.bot_data.get('scheduler')
    if scheduler:
        jobs = scheduler.get_jobs()
        report_lines.append(f"⏰ <b>الجدولة:</b> ✅ نشط")
        report_lines.append(f"  • المهام المجدولة: {len(jobs)}")
        
        # عد المهام حسب النوع
        prayer_jobs = len([j for j in jobs if 'prayer_' in j.id])
        azkar_jobs = len([j for j in jobs if 'azkar_' in j.id])
        report_lines.append(f"  • إشعارات صلاة: {prayer_jobs}")
        report_lines.append(f"  • أذكار: {azkar_jobs}")
    else:
        report_lines.append(f"⏰ <b>الجدولة:</b> ❌ غير نشط")
    
    # الوقت
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_lines.append(f"🕐 <b>الوقت الحالي:</b> {now}")
    
    await update.message.reply_text("\n".join(report_lines), parse_mode='HTML')

async def test_scheduler_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اختبار الجدولة"""
    if update.effective_user.id != int(OWNER_ID_STR):
        await update.message.reply_text("❌ هذا الأمر للمالك فقط.", parse_mode='HTML')
        return
    
    scheduler = context.application.bot_data.get('scheduler')
    
    if not scheduler:
        await update.message.reply_text("❌ الـ Scheduler غير موجود.", parse_mode='HTML')
        return
    
    # جدولة اختبار بعد 30 ثانية
    from datetime import datetime, timedelta
    test_time = datetime.now() + timedelta(seconds=30)
    
    async def test_notification():
        await update.message.reply_text("✅ اختبار: تم إرسال إشعار بعد 30 ثانية")
    
    job_id = f"test_{int(time.time())}"
    scheduler.add_job(
        test_notification,
        'date',
        run_date=test_time,
        id=job_id
    )
    
    jobs = scheduler.get_jobs()
    await update.message.reply_text(
        f"✅ تم جدولة اختبار بعد 30 ثانية\n"
        f"📊 المهام المجدولة: {len(jobs)}\n"
        f"⏰ الاختبار الساعة: {test_time.strftime('%H:%M:%S')}",
        parse_mode='HTML'
    )

async def get_file_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على File ID للصورة"""
    if update.effective_user.id != int(OWNER_ID_STR):
        await update.message.reply_text("❌ هذا الأمر للمالك فقط.", parse_mode='HTML')
        return
    
    photo_file_id = None
    
    if update.message.reply_to_message and update.message.reply_to_message.photo:
        photo_file_id = update.message.reply_to_message.photo[-1].file_id
    elif update.message.photo:
        photo_file_id = update.message.photo[-1].file_id
    
    if not photo_file_id:
        await update.message.reply_text(
            "⚠️ <b>لم يتم العثور على صورة.</b>\n\n"
            "يرجى:\n"
            "1. إرسال الصورة أولاً\n"
            "2. الرد عليها بالأمر <code>/getfileid</code>",
            parse_mode='HTML'
        )
        return
    
    await update.message.reply_text(
        f"✅ <b>تم الحصول على File ID!</b>\n\n"
        f"File ID:\n<code>{photo_file_id}</code>\n\n"
        f"انسخ هذا الكود وضعة في متغير البيئة <code>QR_FILE_ID</code>.",
        parse_mode='HTML'
    )

# ==================== دوال الجدولة التلقائية ====================

async def send_single_prayer_notification(application: Application, user_id: int, prayer_name: str, city_name_ar: str):
    """إرسال إشعار صلاة واحد"""
    try:
        await application.bot.send_message(
            chat_id=user_id,
            text=f"🕋 <b>الله أكبر، الله أكبر.</b> حان الآن وقت صلاة <b>{prayer_name}</b> في محافظة <b>{city_name_ar}</b>.",
            parse_mode='HTML'
        )
        logger.info(f"✅ تم إرسال إشعار صلاة {prayer_name} للمستخدم {user_id}")
    except Exception as e:
        logger.warning(f"⚠️ فشل إرسال إشعار صلاة للمستخدم {user_id}: {e}")

async def send_static_content(application: Application, content_list: list, content_type: str):
    """إرسال محتوى ثابت (أذكار)"""
    if not content_list:
        logger.warning(f"⚠️ قائمة {content_type} فارغة")
        return
    
    users = get_premium_users()
    if not users:
        logger.info(f"ℹ️ لا يوجد مستخدمين مميزين لإرسال {content_type}")
        return
    
    message = random.choice(content_list)
    sent_count = 0
    
    for user_id, _ in users:
        try:
            await application.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode='HTML'
            )
            sent_count += 1
        except Exception as e:
            logger.warning(f"⚠️ فشل إرسال {content_type} للمستخدم {user_id}: {e}")
    
    logger.info(f"✅ تم إرسال {content_type} لـ {sent_count} من {len(users)} مشترك")

async def schedule_daily_prayer_notifications(application: Application):
    """جدولة إشعارات الصلاة اليومية"""
    logger.info("🔄 بدء جدولة إشعارات الصلاة اليومية")
    
    current_date = datetime.datetime.now().date()
    users_data = get_premium_users()
    
    if not users_data:
        logger.info("ℹ️ لا يوجد مستخدمين مميزين للجدولة")
        return
    
    logger.info(f"📋 عدد المستخدمين المميزين: {len(users_data)}")
    
    PRAYER_FIELDS = {
        "الفجر": 'Fajr',
        "الظهر": 'Dhuhr',
        "العصر": 'Asr',
        "المغرب": 'Maghrib',
        "العشاء": 'Isha'
    }
    
    scheduled_count = 0
    scheduler = application.bot_data.get('scheduler')
    
    if not scheduler:
        logger.error("❌ Scheduler غير موجود في bot_data")
        return
    
    for user_id, city_url in users_data:
        if not city_url:
            continue
        
        try:
            response = requests.get(city_url, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            if not data.get('data') or not data['data'].get('timings'):
                logger.warning(f"⚠️ بيانات غير صحيحة للمستخدم {user_id}")
                continue
            
            times_data = data['data']['timings']
            city_name_ar = get_city_ar_from_url(city_url)
            
            # جدولة كل صلاة
            for prayer_name_ar, prayer_key_en in PRAYER_FIELDS.items():
                time_str = times_data.get(prayer_key_en)
                
                if time_str:
                    try:
                        hour, minute = map(int, time_str.split(':'))
                        
                        run_datetime = datetime.datetime(
                            current_date.year,
                            current_date.month,
                            current_date.day,
                            hour,
                            minute,
                            0
                        )
                        
                        # إذا كان الوقت في المستقبل
                        if run_datetime > datetime.datetime.now():
                            job_id = f"prayer_{user_id}_{prayer_key_en}_{current_date.strftime('%Y%m%d')}"
                            
                            scheduler.add_job(
                                send_single_prayer_notification,
                                'date',
                                run_date=run_datetime,
                                args=[application, user_id, prayer_name_ar, city_name_ar],
                                id=job_id,
                                replace_existing=True
                            )
                            
                            scheduled_count += 1
                            logger.debug(f"✅ مجدولة: {prayer_name_ar} للمستخدم {user_id} الساعة {time_str}")
                    
                    except Exception as e:
                        logger.error(f"❌ خطأ في جدولة {prayer_name_ar}: {e}")
        
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ فشل جلب مواقيت الصلاة للمستخدم {user_id}: {e}")
        except Exception as e:
            logger.error(f"❌ خطأ غير متوقع للمستخدم {user_id}: {e}")
    
    logger.info(f"✅ تم جدولة {scheduled_count} إشعار صلاة")

async def schedule_daily_tasks(application: Application):
    """جدولة المهام اليومية"""
    scheduler = application.bot_data.get('scheduler')
    
    if not scheduler:
        logger.error("❌ Scheduler غير موجود")
        return
    
    # جدولة أذكار الصباح يومياً الساعة 6:30
    scheduler.add_job(
        lambda: send_static_content(application, AZKAR_SABAH_LIST, "أذكار الصباح"),
        'cron',
        hour=6,
        minute=30,
        timezone='Asia/Damascus',
        id='azkar_sabah_daily'
    )
    
    # جدولة أذكار المساء يومياً الساعة 19:00
    scheduler.add_job(
        lambda: send_static_content(application, AZKAR_MASAA_LIST, "أذكار المساء"),
        'cron',
        hour=19,
        minute=0,
        timezone='Asia/Damascus',
        id='azkar_masaa_daily'
    )
    
    logger.info("✅ تم جدولة المهام اليومية")

# ==================== إعداد Scheduler ====================

async def post_init_callback(application: Application):
    """تهيئة بعد إنشاء التطبيق"""
    logger.info("🚀 بدء تهيئة البوت")
    
    # إنشاء وتخزين Scheduler
    scheduler = AsyncIOScheduler(timezone='Asia/Damascus')
    application.bot_data['scheduler'] = scheduler
    
    # إضافة المهام المجدولة
    try:
        # مهمة: تحديث الاشتراكات المنتهية يومياً الساعة 00:05
        scheduler.add_job(
            check_expiry_and_update,
            'cron',
            hour=0,
            minute=5,
            timezone='Asia/Damascus',
            id='check_expiry_daily'
        )
        
        # مهمة: جدولة إشعارات الصلاة يومياً الساعة 01:00
        scheduler.add_job(
            schedule_daily_prayer_notifications,
            'cron',
            hour=1,
            minute=0,
            args=[application],
            timezone='Asia/Damascus',
            id='schedule_prayers_daily'
        )
        
        # مهمة: جدولة المهام اليومية فوراً (أذكار)
        scheduler.add_job(
            schedule_daily_tasks,
            'date',
            run_date=datetime.datetime.now() + datetime.timedelta(seconds=10),
            args=[application],
            id='schedule_tasks_initial'
        )
        
        # بدء Scheduler
        scheduler.start()
        application.bot_data['scheduler_started'] = True
        logger.info("✅ تم بدء تشغيل Scheduler بنجاح")
        
        # عرض المهام المجدولة
        jobs = scheduler.get_jobs()
        logger.info(f"📋 عدد المهام المجدولة: {len(jobs)}")
        for job in jobs[:5]:  # عرض أول 5 مهام فقط
            next_run = job.next_run_time.strftime('%Y-%m-%d %H:%M:%S') if job.next_run_time else "غير مجدول"
            logger.info(f"  - {job.id}: {next_run}")
        
        if len(jobs) > 5:
            logger.info(f"  ... و{len(jobs)-5} مهمة أخرى")
    
    except Exception as e:
        logger.error(f"❌ فشل في إعداد الجدولة: {e}")

# ==================== الدالة الرئيسية ====================

def main():
    """الدالة الرئيسية لتشغيل البوت"""
    # التحقق من المتغيرات المطلوبة
    if not TOKEN or not OWNER_ID_STR or not WEBHOOK_URL:
        logger.error("❌ يجب تحديد متغيرات البيئة: TOKEN, OWNER_ID, WEBHOOK_URL")
        sys.exit(1)
    
    try:
        OWNER_ID = int(OWNER_ID_STR)
    except ValueError:
        logger.error("❌ OWNER_ID يجب أن يكون رقماً")
        sys.exit(1)
    
    # إعداد قاعدة البيانات
    try:
        setup_db()
        logger.info("✅ تم إعداد قاعدة البيانات")
    except Exception as e:
        logger.error(f"❌ فشل إعداد قاعدة البيانات: {e}")
        sys.exit(1)
    
    # إنشاء التطبيق
    application = Application.builder().token(TOKEN).post_init(post_init_callback).build()
    
    # إضافة معالجات الأوامر
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("health", health_command))
    application.add_handler(CommandHandler("test_sched", test_scheduler_command))
    application.add_handler(CommandHandler("as", confirm_payment_command))
    application.add_handler(CommandHandler("getfileid", get_file_id_command))
    application.add_handler(CallbackQueryHandler(city_callback_handler))
    
    # تشغيل البوت
    logger.info(f"🚀 بدء البوت على المنفذ {PORT}...")
    
    try:
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
            drop_pending_updates=True
        )
    except Exception as e:
        logger.error(f"❌ فشل في تشغيل البوت: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
