import sqlite3
import datetime
import requests
import logging
import random
import time
import os
import sys

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ==================== إعدادات Logging ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)

# ==================== الثوابت (تُقرأ من متغيرات البيئة) ====================

# الرمز المميز الخاص بالبوت (مطلوب)
TOKEN = os.environ.get("TOKEN")
# رقم معرف المالك (مطلوب)
OWNER_ID_STR = os.environ.get("OWNER_ID") 
# رابط Render العام للخدمة (مطلوب للـ Webhooks)
WEBHOOK_URL = os.environ.get("WEBHOOK_URL") 
# يتم توفير PORT تلقائياً بواسطة Render
PORT = int(os.environ.get('PORT', '10000')) 

try:
    if not TOKEN or not OWNER_ID_STR or not WEBHOOK_URL:
        print("❌ خطأ: يجب تحديد متغيرات البيئة TOKEN و OWNER_ID و WEBHOOK_URL.") 
        sys.exit(1)
        
    OWNER_ID = int(OWNER_ID_STR)
except ValueError:
    print("❌ خطأ: OWNER_ID يجب أن يكون رقماً صحيحاً في متغيرات البيئة.")
    sys.exit(1)
    
# اسم ملف قاعدة البيانات
DB_NAME = os.environ.get("DB_NAME", "subscribers.db")

# --- كود الدفع لخدمة الشام كاش ---
PAYMENT_QR_CODE_CONTENT = os.environ.get("PAYMENT_CODE", "f03c73ecadf2eda455d7be0732207d68") 

# 🚨 File ID لصورة QR كود 🚨
QR_CODE_IMAGE_FILE_ID = os.environ.get("QR_FILE_ID", "AgACAgQAAxkBAAMeaStcosjM_zUZZajf9YbiBqvP2V8AAicMaxs7hlhRo_6zeTTibMABAAMCAAN4AAM2BA") 

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

# ==================== دوال قاعدة البيانات والخدمة ====================

def setup_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            is_premium INTEGER DEFAULT 0,
            end_date TEXT,
            city_url TEXT DEFAULT NULL,
            order_id TEXT DEFAULT NULL
        )
    """)
    conn.commit()
    conn.close()

def get_premium_users():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, city_url FROM users WHERE is_premium = 1")
    users = cursor.fetchall()
    conn.close()
    return users

def get_city_ar_from_url(url):
    for ar_name, en_name in SYRIAN_CITIES.items():
        if en_name in url:
            for key, val in SYRIAN_CITIES.items():
                if val == en_name:
                    return key
    return "مدينتك المختارة"

def generate_order_id(user_id):
    return f"{int(time.time())}-{str(user_id)[-4:]}"

def check_expiry_and_update():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    current_date_str = datetime.date.today().strftime("%Y-%m-%d")
    try:
        cursor.execute("""
            UPDATE users SET is_premium = 0 
            WHERE end_date <= ? AND is_premium = 1
        """, (current_date_str,))
        updated_rows = cursor.rowcount
        conn.commit()
        logging.info(f"تم إنهاء اشتراك {updated_rows} مستخدمين بتاريخ: {current_date_str}")
    except Exception as e:
        logging.error(f"فشل تحديث الاشتراكات المنتهية: {e}")
    finally:
        conn.close()

# ==================== معالجات الأوامر والأزرار ====================

async def get_file_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("عفواً، هذا الأمر مخصص للمالك فقط.", parse_mode='HTML')
        return

    photo_file_id = None
    
    if update.message.reply_to_message and update.message.reply_to_message.photo:
        photo_file_id = update.message.reply_to_message.photo[-1].file_id
    elif update.message.photo:
        photo_file_id = update.message.photo[-1].file_id

    if not photo_file_id:
        await update.message.reply_text(
            "⚠️ <b>لم يتم العثور على صورة في هذه الرسالة أو الرسالة المردود عليها.</b>\n\n"
            "يرجى محاولة إحدى الطريقتين التاليتين:\n"
            "1. أرسل الصورة أولاً (كصورة عادية)، ثم أرسل الأمر <code>/getfileid</code> كـ <b>رد</b> على تلك الصورة.\n"
            "2. أرسل الصورة أولاً، ثم أرسل الأمر <code>/getfileid</code> في رسالة جديدة بعد الصورة مباشرةً.\n\n"
            "تأكد أنك لا ترسلها كـ 'ملف'.", 
            parse_mode='HTML'
        )
        return

    response_message = (
        f"✅ <b>تم الحصول على File ID بنجاح!</b>\n\n"
        f"الـ File ID الخاص بهذه الصورة هو:\n"
        f"<code>{photo_file_id}</code>\n\n"
        f"الآن يمكنك نسخ هذا الكود ولصقه في متغير البيئة <code>QR_FILE_ID</code>."
    )
    
    await update.message.reply_text(response_message, parse_mode='HTML')


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    keyboard = []
    for city_ar, city_en in SYRIAN_CITIES.items():
        keyboard.append([InlineKeyboardButton(city_ar, callback_data=f"CITY_CHOICE_{city_en}")])
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    context.user_data['waiting_for_initial_city'] = True 
    
    await update.message.reply_text(
        "👋 مرحباً بك في بوت الإشعارات المتميزة! 🕌\n\n"
        "لضمان دقة مواقيت الصلاة حسب منطقتك، <b>يرجى اختيار محافظتك أولاً</b>:\n" 
        "<i>(هذه الخطوة مجانية ولا تفعل الاشتراك بعد)</i>" 
        , reply_markup=reply_markup,
        parse_mode='HTML' 
    )

async def subscribe_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT city_url FROM users WHERE user_id = ? AND city_url IS NOT NULL", (user_id,))
    user_data = cursor.fetchone()
    
    if not user_data:
        await query.answer("خطأ: لم يتم اختيار المحافظة بعد. يرجى البدء من جديد عبر /start.", show_alert=True)
        conn.close()
        return

    # 1. إنشاء وحفظ رقم الطلب
    new_order_id = generate_order_id(user_id)
    cursor.execute("UPDATE users SET order_id = ? WHERE user_id = ?", (new_order_id, user_id))
    conn.commit()
    conn.close()
    
    # 2. إرسال إشعار للمالك (تلقائياً)
    user = query.from_user
    username_info = f"@{user.username} ({user.full_name})" if user.username else user.full_name
    
    owner_notification = (
        f"🔔 <b>طلب دفع جديد!</b>\n"
        f"----------------------------------\n"
        f"🧑‍💻 <b>المستخدم:</b> {username_info} (ID: <code>{user_id}</code>)\n" 
        f"📝 <b>رقم الطلب:</b> <code>{new_order_id}</code>\n"
        f"🗺️ <b>المحافظة:</b> {get_city_ar_from_url(user_data[0])}\n"
        f"🔗 <b>رابط تأكيد الدفع:</b> <code>/as {new_order_id}</code>\n"
        f"----------------------------------\n"
        f"يرجى مراجعة إيصال الدفع الذي سيتم إرساله يدوياً من المستخدم."
    )
    await context.bot.send_message(
        chat_id=OWNER_ID,
        text=owner_notification,
        parse_mode='HTML' 
    )

    # 3. تعديل رسالة المستخدم بالتعليمات
    final_message = (
        f"✅ <b>خطوتك الأخيرة لتفعيل الخدمة!</b>\n\n"
        f"--- <b>طلب الخدمة رقم: {new_order_id}</b> ---\n\n"
        f"<b>💰 قيمة الاشتراك:</b> 1$ (USD).\n"
        f"<b>💳 طريقة الدفع:</b> شام كاش (Sham Cash).\n\n"
        f"<b>1. قم بالدفع:</b>\n"
        f"لاستكمال الدفع، يرجى مسح رمز QR الذي سيتم إرساله أدناه أو نسخ الكود:\n"
        f"<b>كود الدفع:</b>\n"
        f"<code>{PAYMENT_QR_CODE_CONTENT}</code>\n\n"
    )

    await query.edit_message_text(final_message, parse_mode='HTML')
    await query.answer("تم إنشاء رقم طلبك!")

    # 4. إرسال صورة QR Code
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
                text=f"⚠️ <b>خطأ في إرسال صورة QR</b>: يرجى نسخ الكود أعلاه مباشرةً:\n <code>{PAYMENT_QR_CODE_CONTENT}</code>",
                parse_mode='HTML'
            )
            logging.error(f"Failed to send QR photo for user {user_id}: {e}")


async def city_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    if query.data.startswith("CITY_CHOICE_"):
        
        city_en = query.data.replace("CITY_CHOICE_", "")
        final_prayer_url = BASE_PRAYER_API.format(city_en=city_en)
        city_ar = get_city_ar_from_url(final_prayer_url)
        
        if context.user_data.get('waiting_for_initial_city'):
            
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO users (user_id, city_url, is_premium)
                VALUES (?, ?, 0)
            """, (user_id, final_prayer_url))
            conn.commit()
            conn.close()
            
            del context.user_data['waiting_for_initial_city']
            
            subscribe_keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("💰 تفعيل الاشتراك الآن", callback_data="ACTIVATE_ORDER")
            ]])
            
            await query.answer(f"تم حفظ المحافظة: {city_ar}!")
            
            await query.edit_message_text(
                f"🎉 <b>اختيارك لمحافظة {city_ar} تم بنجاح!</b> 🎉\n\n" 
                f"الآن أنت جاهز للانطلاق نحو خدمة الإشعارات الدينية المتميزة، والتي تضمن لك:\n\n"
                f"🕋 <b>دقة لا مثيل لها:</b> مواقيت صلاة وإشعارات مُخصصة بالثانية لمحافظة {city_ar}.\n" 
                f"✨ <b>إثراء روحي يومي:</b> استلام الأذكار والأوراد الصباحية والمسائية تلقائياً.\n\n" 
                f"--- <b>فرصة العمر: السحب الأسبوعي!</b> ---\n" 
                f"كل عملية شراء لهذه الخدمة تدخل اسمك <b>مباشرةً في السحب الأسبوعي</b> للفوز بـ <b>جوائز نقدية تصل قيمتها إلى 1000$!</b> لا تفوّت فرصتك لتكون الفائز القادم.\n\n" 
                f"--- <b>لتفعيل الخدمة والمتابعة</b> ---\n" 
                f"<b>💰 قيمة الاشتراك:</b> 1$ USD.\n" 
                f"<b>💳 طريقة الدفع:</b> شام كاش (Sham Cash).\n\n" 
                f"اضغط على الزر أدناه للحصول على <b>رقم طلبك</b> وبدء عملية الدفع:", 
                reply_markup=subscribe_keyboard,
                parse_mode='HTML' 
            )
        else:
            await query.answer("تم حفظ اختيارك مسبقاً.")

    elif query.data == "ACTIVATE_ORDER":
        await subscribe_callback_handler(update, context) 

    else:
        await query.answer("أمر غير معروف.", show_alert=True)


async def confirm_payment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("عفواً، هذا الأمر مخصص للمالك فقط.", parse_mode='HTML')
        return
    
    try:
        order_id_to_confirm = context.args[0]
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute("SELECT user_id FROM users WHERE order_id = ?", (order_id_to_confirm,))
        result = cursor.fetchone()
        
        if not result:
            await update.message.reply_text(f"خطأ: لم يتم العثور على أي مستخدم مرتبط برقم الطلب: {order_id_to_confirm}", parse_mode='HTML')
            conn.close()
            return
            
        user_id_to_activate = result[0]
        today_str = (datetime.date.today() + datetime.timedelta(days=7)).strftime("%Y-%m-%d")

        cursor.execute("""
            UPDATE users SET is_premium = 1, end_date = ?, order_id = NULL 
            WHERE user_id = ?
        """, (today_str, user_id_to_activate))
        
        conn.commit()
        conn.close()

        await context.bot.send_message(
            chat_id=user_id_to_activate,
            text=f"✅ <b>تم تفعيل اشتراكك بنجاح!</b>\n" 
                 f"لقد تم تأكيد دفعك لطلب رقم <b>{order_id_to_confirm}</b>.\n" 
                 f"ستبدأ الآن باستلام إشعارات الصلاة والأذكار وفقاً لتوقيت محافظتك، وتم إدخالك في السحب الأسبوعي على 1000$!",
            parse_mode='HTML' 
        )
        
        await update.message.reply_text(f"تم تفعيل الاشتراك للمستخدم ID: {user_id_to_activate} المرتبط بالطلب {order_id_to_confirm} بنجاح.", parse_mode='HTML')
        
    except Exception as e:
        await update.message.reply_text(f"خطأ في التفعيل: تأكد من استخدام <code>/as &lt;رقم الطلب&gt;</code>. الخطأ الفني: {e}", parse_mode='HTML') 

# ==================== دوال الجدولة التلقائية ====================

async def send_single_prayer_notification(application: Application, user_id: int, prayer_name: str, city_name_ar: str):
    try:
        await application.bot.send_message(
            chat_id=user_id,
            text=f"🕋 <b>الله أكبر، الله أكبر.</b> حان الآن وقت صلاة <b>{prayer_name}</b> في محافظة <b>{city_name_ar}</b>.", 
            parse_mode='HTML' 
        )
        logging.info(f"تم إرسال إشعار صلاة {prayer_name} للمستخدم {user_id} في {city_name_ar}.")
    except Exception as e:
        logging.warning(f"فشل إرسال إشعار صلاة للمستخدم {user_id}: {e}")

async def send_static_content(application: Application, content_list: list, content_type: str):
    users = get_premium_users() 
    if not content_list:
        logging.warning(f"قائمة {content_type} فارغة. لم يتم إرسال شيء.")
        return

    message = random.choice(content_list)
    
    for user_id, _ in users:
        try:
            await application.bot.send_message(
                chat_id=user_id, 
                text=message, 
                parse_mode='HTML' 
            )
        except Exception:
            pass
            
    logging.info(f"تم إرسال {content_type} لـ {len(users)} مشتركين.")


# 🚨 الدالة المجدولة: تسحب الـ scheduler من application.bot_data
async def schedule_daily_prayer_notifications(application: Application): 
    # سحب الـ scheduler من bot_data
    scheduler = application.bot_data.get('scheduler') 
    if not scheduler:
        logging.error("❌ Scheduler object not found in bot_data. Cannot schedule jobs.")
        return
        
    current_date = datetime.datetime.now().date()
    logging.info(f"بدء مهمة الجدولة اليومية لمواقيت الصلاة وأذكار المساء بتاريخ {current_date}.")

    PRAYER_FIELDS = {
        "الفجر": 'Fajr', "الظهر": 'Dhuhr', "العصر": 'Asr', 
        "المغرب": 'Maghrib', "العشاء": 'Isha'
    }

    users_data = get_premium_users()

    for user_id, city_url in users_data:
        if not city_url: continue
            
        try:
            response = requests.get(city_url, timeout=10)
            response.raise_for_status() 
            times_data = response.json().get('data', {}).get('timings')
            
            if not times_data: continue
                
            city_name_ar = get_city_ar_from_url(city_url)
            
            for prayer_name_ar, prayer_key_en in PRAYER_FIELDS.items():
                time_str = times_data.get(prayer_key_en)
                
                if time_str and len(time_str.split(':')) == 2:
                    hour, minute = map(int, time_str.split(':'))
                    
                    run_datetime = datetime.datetime(
                        current_date.year, current_date.month, current_date.day, 
                        hour, minute, 0
                    )

                    if run_datetime > datetime.datetime.now():
                        scheduler.add_job(
                            send_single_prayer_notification, 
                            'date', 
                            run_date=run_datetime, 
                            args=[application, user_id, prayer_name_ar, city_name_ar],
                            id=f"prayer_{user_id}_{prayer_key_en}_{current_date.strftime('%Y%m%d')}",
                            replace_existing=True 
                        )

            isha_time_str = times_data.get('Isha')
            if isha_time_str and len(isha_time_str.split(':')) == 2:
                isha_hour, isha_minute = map(int, isha_time_str.split(':'))
                
                isha_datetime = datetime.datetime(
                    current_date.year, current_date.month, current_date.day, 
                    isha_hour, isha_minute, 0
                )
                
                send_time = isha_datetime + datetime.timedelta(minutes=30)
                
                if send_time > datetime.datetime.now():
                    scheduler.add_job(
                        send_static_content, 
                        'date', 
                        run_date=send_time, 
                        args=[application, AZKAR_MASAA_LIST, "أذكار المساء"],
                        id=f"azkar_masaa_{user_id}_{current_date.strftime('%Y%m%d')}",
                        replace_existing=True 
                    )

        except Exception as e:
            logging.error(f"خطأ أثناء جدولة الصلوات للمستخدم {user_id}: {e}")


# 🚨 دالة Callback لبدء الجدولة (تستخدم bot_data)
async def post_init_callback(application: Application):
    # سحب الـ scheduler من bot_data
    scheduler = application.bot_data.get('scheduler') 
    
    # التحقق من وجود scheduler وعدم بدء تشغيله بعد (باستخدام bot_data)
    if scheduler and not application.bot_data.get('scheduler_started', False):
        scheduler.start()
        # تخزين حالة البدء في bot_data
        application.bot_data['scheduler_started'] = True 
        logging.info("تم بنجاح بدء تشغيل مُجدول المهام (APScheduler).")

# ==================== دالة التشغيل الرئيسية المُعدلة للـ Webhooks ====================

def main():
    setup_db() 
    scheduler = AsyncIOScheduler(timezone='Asia/Damascus')
    
    # إضافة المهام الابتدائية إلى الـ scheduler
    scheduler.add_job(check_expiry_and_update, 'cron', hour=0, minute=5) 
    scheduler.add_job(
        schedule_daily_prayer_notifications, 'cron', hour=1, minute=0, 
        args=[None] 
    )
    scheduler.add_job(
        send_static_content, 'cron', hour=6, minute=30, 
        args=[None, AZKAR_SABAH_LIST, "أذكار الصباح"] 
    )
    
    application = Application.builder().token(TOKEN).post_init(post_init_callback).build() 
    
    # 🚨 FIX: تخزين الـ scheduler في application.bot_data
    application.bot_data['scheduler'] = scheduler 
    
    # FIX: تعديل وسائط الـ jobs لتمرير كائن الـ application
    for job in scheduler.get_jobs():
        if job.args and job.args[0] is None:
             job.modify(args=[application] + list(job.args[1:]))

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("as", confirm_payment_command))
    application.add_handler(CommandHandler("getfileid", get_file_id_command)) 
    application.add_handler(CallbackQueryHandler(city_callback_handler)) 

    # تشغيل Webhooks
    print(f"البوت جاهز للعمل بنظام Webhooks على المنفذ {PORT}...")
    
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=WEBHOOK_URL + '/' + TOKEN,
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
