import os
import sqlite3
import datetime
import random
import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.base import JobLookupError

# --------------------------
# 1. الإعدادات والثوابت
# --------------------------

# يجب تعريف هذه المتغيرات في بيئة Render
TOKEN = os.environ.get("TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID")) # تأكد من أن هذا رقم صحيح
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
QR_FILE_ID = os.environ.get("QR_FILE_ID") # File ID لصورة QR
PAYMENT_CODE = os.environ.get("PAYMENT_CODE")
PORT = int(os.environ.get("PORT", 10000))

DB_NAME = 'subscribers.db'
PRAYER_API_URL = "http://api.aladhan.com/v1/timingsByCity?city={}&country=Syria&method=3"

# حقول قاعدة البيانات ومفاتيح الـ API للصلوات
PRAYER_FIELDS = {
    "الفجر": 'Fajr',
    "الظهر": 'Dhuhr',
    "العصر": 'Asr',
    "المغرب": 'Maghrib',
    "العشاء": 'Isha'
}

# قائمة أذكار الصباح والمساء (لأغراض العرض، يجب أن تكون هذه قائمة حقيقية)
AZKAR_SABAH_LIST = [
    "☀️ أصبحنا وأصبح الملك لله، والحمد لله، لا إله إلا الله وحده لا شريك له. (مثال لأذكار الصباح)",
    "☀️ سبحان الله وبحمده عدد خلقه، ورضا نفسه، وزنة عرشه، ومداد كلماته.",
]
AZKAR_MASAA_LIST = [
    "🌙 أمسينا وأمسى الملك لله، والحمد لله، لا إله إلا الله وحده لا شريك له. (مثال لأذكار المساء)",
    "🌙 يامقلب القلوب ثبت قلبي على دينك. (مثال لأذكار المساء)",
]

# بيانات المدن السورية مع الـ URL الخاص بمواقيت الصلاة
SYRIAN_CITIES = {
    "دمشق": PRAYER_API_URL.format("Damascus"),
    "حلب": PRAYER_API_URL.format("Aleppo"),
    "حمص": PRAYER_API_URL.format("Homs"),
    "حماة": PRAYER_API_URL.format("Hama"),
    "اللاذقية": PRAYER_API_URL.format("Latakia"),
    "طرطوس": PRAYER_API_URL.format("Tartus"),
}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --------------------------
# 2. وظائف قاعدة البيانات (Database Functions)
# --------------------------

def setup_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            city_url TEXT,
            city_name TEXT,
            is_premium INTEGER DEFAULT 0,
            subscription_end_date TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_user_status(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT is_premium, city_url FROM users WHERE user_id=?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        return result
    return (0, None)

def update_user_city(user_id, city_name, city_url):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO users (user_id, city_name, city_url, is_premium)
        VALUES (?, ?, ?, (SELECT is_premium FROM users WHERE user_id=?))
    """, (user_id, city_name, city_url, user_id))
    conn.commit()
    conn.close()

def get_premium_users():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, city_url FROM users WHERE is_premium=1 AND city_url IS NOT NULL")
    result = cursor.fetchall()
    conn.close()
    return result

def get_user_counts():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(user_id) FROM users")
    total_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(user_id) FROM users WHERE is_premium=1")
    premium_users = cursor.fetchone()[0]
    conn.close()
    return total_users, premium_users

def update_subscription(user_id, duration_days=7):
    end_date = datetime.datetime.now() + datetime.timedelta(days=duration_days)
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO users (user_id, is_premium, subscription_end_date, city_url, city_name)
        VALUES (?, 1, ?, 
                (SELECT city_url FROM users WHERE user_id=?),
                (SELECT city_name FROM users WHERE user_id=?)
        )
    """, (user_id, end_date.strftime('%Y-%m-%d %H:%M:%S'), user_id, user_id))
    conn.commit()
    conn.close()
    return end_date.strftime('%Y-%m-%d')

def get_city_ar_from_url(url):
    for name_ar, city_url in SYRIAN_CITIES.items():
        if city_url == url:
            return name_ar
    return "المدينة" # Fallback

# 🆕 دالة جديدة لفحص حالة API
async def check_prayer_api_status():
    """يفحص اتصال API الأذان عبر محاولة جلب مواقيت دمشق."""
    test_url = SYRIAN_CITIES.get("دمشق") # استخدام دمشق كمدينة اختبار
    
    try:
        response = requests.get(test_url, timeout=10)
        response.raise_for_status() # إثارة HTTPError لأكواد 4xx/5xx
        
        data = response.json()
        if data and data.get('data') and data.get('data').get('timings'):
            return True, "✅ API يعمل بشكل صحيح وتم استلام مواقيت الصلاة."
        else:
            return False, f"⚠️ API يعمل (كود {response.status_code}) لكن البيانات المستلمة غير صالحة."

    except requests.exceptions.HTTPError as e:
        return False, f"❌ فشل الاتصال بالـ API: حدث خطأ HTTP: {e}"
    except requests.exceptions.RequestException as e:
        return False, f"❌ فشل الاتصال بالـ API: خطأ في الشبكة/المهلة الزمنية: {e}"
    except Exception as e:
        return False, f"❌ خطأ غير متوقع أثناء فحص API: {e}"


# --------------------------
# 3. وظائف الإشعارات والجدولة (Notification and Scheduling Functions)
# --------------------------

async def send_single_prayer_notification(application: Application, user_id: int, prayer_name: str, city_name: str):
    try:
        message = f"🕌 **حان الآن موعد صلاة {prayer_name}** 🕌\n"
        message += f"في مدينة **{city_name}**.\n"
        message += f"تقبل الله منكم صالح الأعمال."
        await application.bot.send_message(
            chat_id=user_id,
            text=message,
            parse_mode='HTML'
        )
    except Exception as e:
        logging.error(f"فشل إرسال إشعار الصلاة للمستخدم {user_id}: {e}")

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
    
    # 🔔 إشعار للمالك - تقرير الأذكار
    try:
        await application.bot.send_message(
            chat_id=OWNER_ID,
            text=f"✅ **تقرير إرسال {content_type}**:\nتم إرسال الأذكار بنجاح لـ **{len(users)}** مشتركين.",
            parse_mode='HTML'
        )
    except Exception as e:
        logging.error(f"فشل إرسال تقرير الأذكار للمالك: {e}")


async def schedule_daily_prayer_notifications(application: Application): 
    """وظيفة CRON تنطلق يومياً لجدولة صلوات اليوم التالي."""
    scheduler = application.bot_data.get('scheduler') 
    if not scheduler:
        logging.error("❌ Scheduler object not found in bot_data. Cannot schedule jobs.")
        return
        
    scheduled_prayers_count = 0
    scheduled_azkar_masaa_count = 0
    total_users_for_report = 0
    
    current_date = datetime.datetime.now().date()
    logging.info(f"بدء مهمة الجدولة اليومية لمواقيت الصلاة وأذكار المساء بتاريخ {current_date}.")

    users_data = get_premium_users()
    total_users_for_report = len(users_data)
    
    for user_id, city_url in users_data:
        if not city_url: continue
            
        try:
            # جلب البيانات من API
            response = requests.get(city_url, timeout=10)
            response.raise_for_status() 
            times_data = response.json().get('data', {}).get('timings')
            
            if not times_data: continue
                
            city_name_ar = get_city_ar_from_url(city_url)
            
            # جدولة الصلوات
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
                        scheduled_prayers_count += 1
                    else:
                        pass
                        
            # جدولة أذكار المساء
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
                    scheduled_azkar_masaa_count += 1

        except Exception as e:
            logging.error(f"خطأ أثناء جدولة الصلوات للمستخدم {user_id} أو فشل جلب API: {e}")

    # 🔔 إشعار للمالك - تقرير الجدولة الذكي
    job_run_time = datetime.datetime.now().strftime("%H:%M:%S")
    report_message = f"📰 **تقرير جدولة الصلوات اليومي**\n"
    report_message += f"**عدد المشتركين المميزين:** {total_users_for_report}\n"
    report_message += f"**تم التشغيل في:** {job_run_time}\n"
    
    if total_users_for_report > 0:
        max_possible_prayers = total_users_for_report * len(PRAYER_FIELDS) 
        
        if scheduled_prayers_count < max_possible_prayers:
            skipped_prayers = max_possible_prayers - scheduled_prayers_count
            report_message += (
                f"\n⚠️ **تأخير أو سبات (Sleep/Misfire)** ⚠️\n"
                f"تم جدولتها متأخرة! تم **تخطي** جدولة حوالي **{skipped_prayers}** صلاة "
                f"(كالفجر والظهر) لأن وقتها قد فات.\n"
                f"✅ تم جدولـة **{scheduled_prayers_count}** صلاة بنجاح (المغرب والعشاء وما تبقى). "
            )
        else:
            report_message += f"✅ **الجدولة كاملة:** تم جدولـة **{scheduled_prayers_count}** صلاة بنجاح لجميع المشتركين."
            
        report_message += f"\n🔔 تم جدولـة **{scheduled_azkar_masaa_count}** إشعار لأذكار المساء."
    else:
        report_message += "❌ **لا يوجد مشتركين مميزين** لتتم الجدولة لهم."

    try:
        await application.bot.send_message(
            chat_id=OWNER_ID,
            text=report_message,
            parse_mode='HTML'
        )
    except Exception as e:
        logging.error(f"فشل إرسال تقرير الجدولة للمالك: {e}")
        
    
# --------------------------
# 4. معالجة الأوامر (Handlers)
# --------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_status, _ = get_user_status(update.effective_user.id)
    
    message = f"أهلاً بك يا {update.effective_user.first_name} في بوت الميقات الذهبي! 🕌\n"
    message += "للحصول على تنبيهات الصلاة والأذكار، يجب عليك الاشتراك أولاً.\n"
    
    keyboard = []
    
    city_buttons = [
        InlineKeyboardButton(name, callback_data=f"select_city_{name}")
        for name in SYRIAN_CITIES.keys()
    ]
    
    keyboard.append(city_buttons[:3])
    keyboard.append(city_buttons[3:])
    
    subscribe_button_text = "💰 إدارة/تجديد الاشتراك (1$ أسبوعياً)"
    keyboard.append([InlineKeyboardButton(subscribe_button_text, callback_data="manage_subscription")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(message, reply_markup=reply_markup)
    
async def show_subscribers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ هذا الأمر مخصص للمالك فقط.")
        return

    try:
        total_users, premium_users = get_user_counts()
        
        report = f"📊 **إحصائيات المشتركين**\n\n"
        report += f"👤 **إجمالي المستخدمين المسجلين:** {total_users}\n"
        report += f"⭐️ **المشتركين المميزين (Active Premium):** {premium_users}"
        
        await update.message.reply_text(report, parse_mode='Markdown')
        
    except Exception as e:
        logging.error(f"فشل إرسال إحصائيات المشتركين: {e}")
        await update.message.reply_text("❌ عذراً، حدث خطأ أثناء جلب الإحصائيات.")

# 🆕 أمر جديد: فحص حالة الجدولة والـ API
async def check_jobs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ هذا الأمر مخصص للمالك فقط.")
        return
        
    scheduler = context.application.bot_data.get('scheduler')
    if not scheduler:
        await update.message.reply_text("❌ لم يتم تشغيل مُجدول المهام بعد.")
        return

    # 1. فحص حالة API
    api_status_ok, api_message = await check_prayer_api_status()
    
    report = f"🛠️ **تقرير فحص حالة البوت والجدولة**\n"
    report += "--------------------------------------\n"
    report += f"🌐 **حالة API مواقيت الصلاة:**\n{api_message}\n"
    report += "--------------------------------------\n"
    
    # 2. فحص المهام المجدولة
    jobs = scheduler.get_jobs()
    
    prayer_jobs_count = sum(1 for job in jobs if job.id.startswith('prayer_'))
    azkar_masaa_jobs_count = sum(1 for job in jobs if job.id.startswith('azkar_masaa_'))
    cron_jobs_count = sum(1 for job in jobs if isinstance(job.trigger, CronTrigger))

    report += f"⏱️ **المهام المُجدوَلة حالياً:**\n"
    report += f"  - صلوات مجدولة (فردية): **{prayer_jobs_count}**\n"
    report += f"  - أذكار المساء مجدولة: **{azkar_masaa_jobs_count}**\n"
    report += f"  - مهام CRON يومية (جدولة/انتهاء): **{cron_jobs_count}**\n"
    report += f"  - إجمالي المهام في قائمة الانتظار: **{len(jobs)}**\n"
    
    # فحص موعد تشغيل CRON التالي
    try:
        daily_schedule_job = scheduler.get_job('schedule_daily_prayer_notifications')
        if daily_schedule_job:
            next_run = daily_schedule_job.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z")
            report += f"\n🗓️ **موعد الجدولة اليومية القادم (01:00):**\n"
            report += f"  - {next_run}"
    except JobLookupError:
         report += f"\n❌ فشل تحديد موقع وظيفة الجدولة اليومية."

    await update.message.reply_text(report, parse_mode='Markdown')


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    user_id = query.from_user.id
    
    if data.startswith("select_city_"):
        city_name = data.replace("select_city_", "")
        city_url = SYRIAN_CITIES.get(city_name)
        
        update_user_city(user_id, city_name, city_url)
        
        await query.edit_message_text(
            text=f"✅ تم اختيار مدينة **{city_name}** بنجاح!\n"
                 f"الآن يمكنك إدارة اشتراكك للحصول على الإشعارات.",
            parse_mode='Markdown'
        )

    elif data == "manage_subscription":
        user_status, city_url = get_user_status(user_id)
        
        if not city_url:
            await query.edit_message_text(
                text="⚠️ الرجاء اختيار مدينتك أولاً من القائمة الرئيسية لكي نعمل على جدولة مواقيت الصلاة لك.",
            )
            return
            
        message = "💳 **إدارة الاشتراك (الخدمة المتميزة)**\n"
        
        if user_status == 1:
            message += "حالتك الحالية: **مشترك مميز** ✅\n"
            message += "يمكنك تجديد اشتراكك الآن."
        else:
            message += "حالتك الحالية: **غير مشترك** ❌\n"
            message += "اشترك الآن للحصول على تنبيهات دقيقة وفرصة لدخول السحب الأسبوعي!"
            
        message += "\n\n**للاشتراك (1$ أسبوعياً):**\n"
        message += f"1. حول المبلغ إلى رقم **{PAYMENT_CODE}** (شام كاش).\n"
        message += "2. أرسل رقم عملية التحويل هنا للتحقق.\n"

        keyboard = [[
            InlineKeyboardButton("عرض رمز QR للدفع", callback_data="show_qr"),
            InlineKeyboardButton("تجديد/تفعيل الاشتراك", callback_data="activate_sub")
        ]]
        
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data == "show_qr":
        try:
            await context.bot.send_photo(
                chat_id=user_id,
                photo=QR_FILE_ID,
                caption="رمز QR الخاص بالدفع (شام كاش). يرجى إرسال رقم العملية بعد التحويل."
            )
        except Exception:
             await query.message.reply_text("عذراً، فشل إرسال صورة QR. تأكد من صحة QR_FILE_ID.")

    elif data == "activate_sub":
        end_date = update_subscription(user_id)
        await query.edit_message_text(
            f"🎉 **تم تفعيل اشتراكك المميز بنجاح!** 🎉\n"
            f"الاشتراك سينتهي في: **{end_date}**\n"
            f"ستبدأ باستقبال الإشعارات فوراً، وتم إدخالك في السحب الأسبوعي."
        )


# --------------------------
# 5. وظائف إدارة المُجدول (Scheduler Functions)
# --------------------------

async def check_expiry_and_update(application: Application):
    """وظيفة CRON لتحديث حالة الاشتراكات المنتهية."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute("""
        UPDATE users 
        SET is_premium=0, subscription_end_date=NULL
        WHERE is_premium=1 AND subscription_end_date < ?
    """, (now_str,))
    
    updated_count = cursor.rowcount
    conn.commit()
    conn.close()
    
    logging.info(f"تم إنهاء اشتراك {updated_count} مستخدمين بتاريخ: {now_str}")


async def post_init_callback(application: Application):
    """يتم استدعاؤها بعد تهيئة البوت وقبل بدء تشغيل الـ Webhook."""
    
    if application.bot_data.get('scheduler_started'):
        logging.info("المُجدول تم تشغيله مسبقاً.")
        return
        
    try:
        scheduler = AsyncIOScheduler(timezone="Asia/Damascus")
        
        # 1. مهمة التحقق من انتهاء الاشتراكات
        scheduler.add_job(
            check_expiry_and_update, 
            trigger=CronTrigger(hour=0, minute=5, timezone="Asia/Damascus"),
            id='check_expiry_and_update', 
            replace_existing=True
        )

        # 2. مهمة جدولة الصلوات اليومية
        scheduler.add_job(
            schedule_daily_prayer_notifications, 
            trigger=CronTrigger(hour=1, minute=0, timezone="Asia/Damascus"),
            args=[application],
            id='schedule_daily_prayer_notifications', 
            replace_existing=True
        )
        
        # 3. مهمة إرسال أذكار الصباح
        scheduler.add_job(
            send_static_content, 
            trigger=CronTrigger(hour=6, minute=30, timezone="Asia/Damascus"),
            args=[application, AZKAR_SABAH_LIST, "أذكار الصباح"],
            id='send_static_content', 
            replace_existing=True
        )

        application.bot_data['scheduler'] = scheduler
        scheduler.start()
        application.bot_data['scheduler_started'] = True
        logging.info("تم بنجاح بدء تشغيل مُجدول المهام (APScheduler).")

    except Exception as e:
        logging.error(f"❌ فشل بدء تشغيل المُجدول: {e}")
        
# --------------------------
# 6. دالة التشغيل الرئيسية (Main Function)
# --------------------------

def main():
    if not all([TOKEN, OWNER_ID, WEBHOOK_URL, QR_FILE_ID, PAYMENT_CODE]):
        logging.error("❌ أحد متغيرات البيئة الأساسية غير معرّف. يرجى مراجعة إعدادات Render.")
        return

    setup_db()
    
    application = Application.builder().token(TOKEN).post_init(post_init_callback).build()

    # إضافة المعالجات
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stats", show_subscribers_command))
    application.add_handler(CommandHandler("check_jobs", check_jobs_command)) # 🆕 الأمر الجديد
    application.add_handler(CallbackQueryHandler(handle_callback))

    logging.info(f"البوت جاهز للعمل بنظام Webhooks على المنفذ {PORT}...")

    # تشغيل البوت بنظام Webhook
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=WEBHOOK_URL + '/' + TOKEN,
        drop_pending_updates=True
    )

if __name__ == '__main__':
    main()
