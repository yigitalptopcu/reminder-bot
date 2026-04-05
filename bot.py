import asyncio
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes

# .env dosyasını yükle
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN .env dosyasında bulunamadı. Lütfen .env dosyasına ekle.")

# APScheduler ile zamanlanmış görev yöneticisi
scheduler = None
ISTANBUL_OFFSET = timedelta(hours=3)
reminders: Dict[int, List[dict]] = {}
next_reminder_id = 1
application = None
KNOWN_TAGS = ["iş", "okul", "ev", "aile"]
REMINDERS_FILE = "reminders.json"


def save_reminders():
    """Hatırlatmaları JSON dosyasına kaydet."""
    try:
        data = {}
        for chat_id, reminder_list in reminders.items():
            data[str(chat_id)] = [
                {
                    "id": r["id"],
                    "message": r["message"],
                    "due_time": r["due_time"].isoformat(),
                    "job_id": r["job_id"],
                    "tag": r["tag"],
                }
                for r in reminder_list
            ]
        with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Hatırlatmaları kaydetme hatası: {e}")


def load_reminders():
    """Hatırlatmaları JSON dosyasından yükle."""
    global reminders, next_reminder_id
    if not os.path.exists(REMINDERS_FILE):
        return
    try:
        with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for chat_id_str, reminder_list in data.items():
            chat_id = int(chat_id_str)
            reminders[chat_id] = []
            for r in reminder_list:
                due_time = datetime.fromisoformat(r["due_time"])
                reminders[chat_id].append({
                    "id": r["id"],
                    "message": r["message"],
                    "due_time": due_time,
                    "job_id": r["job_id"],
                    "tag": r["tag"],
                })
                # Job'u yeniden zamanla
                if due_time > datetime.utcnow() + ISTANBUL_OFFSET:
                    scheduler.add_job(
                        send_reminder,
                        trigger="date",
                        run_date=due_time,
                        args=[chat_id, r["id"]],
                        id=r["job_id"],
                    )
                next_reminder_id = max(next_reminder_id, r["id"] + 1)
    except Exception as e:
        print(f"Hatırlatmaları yükleme hatası: {e}")


def parse_time(time_str: str) -> datetime:
    """Zaman ifadesini datetime nesnesine çevirir."""
    now = datetime.utcnow() + ISTANBUL_OFFSET
    time_str = time_str.lower().strip()

    # Yarın
    if "yarın" in time_str:
        tomorrow = now + timedelta(days=1)
        if "sabah" in time_str:
            return tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)
        elif "öğle" in time_str:
            return tomorrow.replace(hour=12, minute=0, second=0, microsecond=0)
        elif "akşam" in time_str:
            return tomorrow.replace(hour=18, minute=0, second=0, microsecond=0)
        else:
            return tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)  # varsayılan sabah

    # Gün adı
    days = {"pazartesi": 0, "salı": 1, "çarşamba": 2, "perşembe": 3, "cuma": 4, "cumartesi": 5, "pazar": 6}
    for day_name, day_num in days.items():
        if day_name in time_str:
            current_day = now.weekday()
            days_ahead = (day_num - current_day) % 7
            if days_ahead == 0:
                days_ahead = 7  # gelecek hafta
            target_date = now + timedelta(days=days_ahead)
            return target_date.replace(hour=9, minute=0, second=0, microsecond=0)

    # Tarih ve saat: "gün ay yıl saat dakika"
    match = re.search(r'(\d{1,2})\s+(\w+)\s+(\d{4})\s+saat\s+(\d{1,2})\.(\d{2})', time_str)
    if match:
        day = int(match.group(1))
        month_name = match.group(2)
        year = int(match.group(3))
        hour = int(match.group(4))
        minute = int(match.group(5))
        months = {"ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6, "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12}
        month = months.get(month_name)
        if month:
            try:
                return datetime(year, month, day, hour, minute, 0, 0)
            except ValueError:
                pass

    # Tarih: "gün ay yıl"
    match = re.search(r'(\d{1,2})\s+(\w+)\s+(\d{4})', time_str)
    if match:
        day = int(match.group(1))
        month_name = match.group(2)
        year = int(match.group(3))
        months = {"ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6, "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12}
        month = months.get(month_name)
        if month:
            try:
                return datetime(year, month, day, 9, 0, 0, 0)
            except ValueError:
                pass

    # Tarih ve saat: "gün ay saat dakika"
    match = re.search(r'(\d{1,2})\s+(\w+)\s+saat\s+(\d{1,2})\.(\d{2})', time_str)
    if match:
        day = int(match.group(1))
        month_name = match.group(2)
        hour = int(match.group(3))
        minute = int(match.group(4))
        months = {"ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6, "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12}
        month = months.get(month_name)
        if month:
            year = now.year
            if month < now.month or (month == now.month and day < now.day):
                year += 1
            try:
                return datetime(year, month, day, hour, minute, 0, 0)
            except ValueError:
                pass

    # Tarih: "gün ay"
    match = re.search(r'(\d{1,2})\s+(\w+)', time_str)
    if match:
        day = int(match.group(1))
        month_name = match.group(2)
        months = {"ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6, "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12}
        month = months.get(month_name)
        if month:
            year = now.year
            if month < now.month or (month == now.month and day < now.day):
                year += 1
            try:
                return datetime(year, month, day, 9, 0, 0, 0)
            except ValueError:
                pass

    # Dakika: "X dk" veya "X dakika"
    match = re.search(r'(\d+)\s+(dk|dakika)', time_str)
    if match:
        minutes = int(match.group(1))
        return now + timedelta(minutes=minutes)

    # Saat: "X saat"
    match = re.search(r'(\d+)\s+saat', time_str)
    if match:
        hours = int(match.group(1))
        return now + timedelta(hours=hours)

    # Gün: "X gün"
    match = re.search(r'(\d+)\s+gün', time_str)
    if match:
        days = int(match.group(1))
        return now + timedelta(days=days)

    # Hafta: "X hafta"
    match = re.search(r'(\d+)\s+hafta', time_str)
    if match:
        weeks = int(match.group(1))
        return now + timedelta(weeks=weeks)

    # Ay: "X ay"
    match = re.search(r'(\d+)\s+ay', time_str)
    if match:
        months = int(match.group(1))
        return now + timedelta(days=months * 30)

    # Dakika sayısı
    try:
        minutes = int(time_str)
        return now + timedelta(minutes=minutes)
    except ValueError:
        pass

    # Fallback
    return now + timedelta(minutes=10)


def get_next_reminder_id() -> int:
    global next_reminder_id
    reminder_id = next_reminder_id
    next_reminder_id += 1
    return reminder_id


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kullanıcı /start yazdığında hoş geldin mesajı gönder."""
    await update.message.reply_text(
        "⏰ **Hatırlatıcı Bot'a Hoş Geldiniz!**\n\n"
        "Merhaba! Ben kişisel hatırlatma asistanınızım. 📅\n"
        "Günlük görevlerinizi, önemli tarihleri ve randevularınızı kaçırmamanız için buradayım.\n\n"
        "🚀 **Hızlı Başlangıç**\n"
        "Yeni bir hatırlatma oluşturmak için `/remind` veya kısa komut `/r` kullanabilirsiniz.\n\n"
        "📋 **Komutlar**\n"
        "• `/remind` veya `/r` `<zaman>` `[etiket]` `[mesaj]` → Yeni hatırlatma ekler\n"
        "• `/list` → Aktif hatırlatmaları listeler\n"
        "• `/cancel` → Son hatırlatmayı siler\n"
        "• `/cancel all` → Tüm hatırlatmaları temizler\n\n"
        "🕒 **Zaman Formatları**\n"
        "• Dakika: `10` / `5 dk` / `5 dakika`\n"
        "• Saat: `2 saat`\n"
        "• Gün: `3 gün`\n"
        "• Hafta: `1 hafta`\n"
        "• Ay: `2 ay`\n"
        "• Gün bazlı: `çarşamba`, `yarın sabah`\n"
        "• Tarih: `5 nisan`, `15 nisan saat 17.00`\n\n"
        "🏷️ **Etiketler (opsiyonel)**\n"
        "`iş`, `okul`, `ev`, `aile`\n\n"
        "💡 **Örnek Kullanımlar**\n"
        "`/remind 5 dk sonra kahve iç`\n"
        "`/remind 3 gün sonra toplantı`\n"
        "`/remind çarşamba okul toplantı`\n"
        "`/remind 15 nisan saat 17.00 aile doğum günü`\n\n"
        "Başlamak için hemen bir hatırlatma oluşturun. 🎯"
    )


async def send_reminder(chat_id: int, reminder_id: int) -> None:
    """Zamanı gelen hatırlatmayı kullanıcıya gönderir."""
    reminder_list = reminders.get(chat_id, [])
    reminder = next((r for r in reminder_list if r["id"] == reminder_id), None)
    if not reminder:
        return

    await application.bot.send_message(
        chat_id=chat_id,
        text=f"⏰ Hatırlatma: {reminder['message']}"
    )

    # Gönderildikten sonra listeden kaldır
    reminders[chat_id] = [r for r in reminder_list if r["id"] != reminder_id]
    save_reminders()


async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Yeni bir hatırlatma ekler ve APScheduler ile zamanlar."""
    if not context.args:
        await update.message.reply_text(
            "Kullanım: /remind <zaman> [tag] [mesaj]\n"
            "Zaman örnekleri:\n"
            "- Dakika: 10\n"
            "- Dakika: 5 dk veya 5 dakika\n"
            "- Dakika: 5 dk sonra\n"
            "- Saat: 2 saat\n"
            "- Gün: 3 gün\n"
            "- Hafta: 1 hafta\n"
            "- Ay: 2 ay\n"
            "- Gün: çarşamba günü\n"
            "- Tarih: 15 nisan saat 17.00\n"
            "- Tarih: 5 nisan\n"
            "- Yarın: yarın sabah\n"
            "Tag'ler: iş, okul, ev, aile (opsiyonel)\n"
            "Mesaj opsiyonel, varsayılan 'Hatırlatma zamanı geldi'"
        )
        return

    # Zaman ifadesini parse et
    time_args = []
    i = 0
    while i < len(context.args):
        arg = context.args[i].lower()
        if arg == "sonra":
            break
        time_args.append(context.args[i])
        # Eğer sayı ve sonraki kelime zaman birimi ise devam et
        if arg.isdigit() and i + 1 < len(context.args) and context.args[i+1].lower() in ["dk", "dakika", "saat", "gün", "hafta", "ay"]:
            i += 1
            time_args.append(context.args[i])
        # Veya ay adı ise
        elif arg.isdigit() and i + 1 < len(context.args) and context.args[i+1].lower() in ["ocak", "şubat", "mart", "nisan", "mayıs", "haziran", "temmuz", "ağustos", "eylül", "ekim", "kasım", "aralık"]:
            i += 1
            time_args.append(context.args[i])
            # Yıl varsa
            if i + 1 < len(context.args) and context.args[i+1].isdigit() and len(context.args[i+1]) == 4:
                i += 1
                time_args.append(context.args[i])
            # Saat varsa
            if i + 1 < len(context.args) and context.args[i+1].lower() == "saat" and i + 2 < len(context.args):
                i += 1
                time_args.append(context.args[i])
                i += 1
                time_args.append(context.args[i])
        else:
            break
        i += 1

    time_str = " ".join(time_args)
    remaining_args = context.args[i:]

    tag = None
    message_start = 0
    if remaining_args and remaining_args[0].lower() in KNOWN_TAGS:
        tag = remaining_args[0].lower()
        message_start = 1

    message = " ".join(remaining_args[message_start:]).strip() if remaining_args[message_start:] else "Hatırlatma zamanı geldi"

    try:
        run_time = parse_time(time_str)
    except Exception as e:
        await update.message.reply_text(f"Zaman parse edilemedi: {e}")
        return

    chat_id = update.effective_chat.id
    reminder_id = get_next_reminder_id()
    job_id = f"reminder-{chat_id}-{reminder_id}"

    scheduler.add_job(
        send_reminder,
        trigger="date",
        run_date=run_time,
        args=[chat_id, reminder_id],
        id=job_id,
    )

    reminders.setdefault(chat_id, []).append({
        "id": reminder_id,
        "message": message,
        "due_time": run_time,
        "job_id": job_id,
        "tag": tag,
    })

    save_reminders()

    tag_str = f" (Tag: {tag})" if tag else ""
    message_text = (
        f"✅ Hatırlatma eklendi!\n"
        f"Mesaj: {message}{tag_str}\n"
        f"Zaman: {run_time.strftime('%Y-%m-%d %H:%M')} (Istanbul)"
    )

    if not tag:
        keyboard = [
            [
                InlineKeyboardButton("İş", callback_data=f"tag|{chat_id}|{reminder_id}|iş"),
                InlineKeyboardButton("Okul", callback_data=f"tag|{chat_id}|{reminder_id}|okul"),
            ],
            [
                InlineKeyboardButton("Ev", callback_data=f"tag|{chat_id}|{reminder_id}|ev"),
                InlineKeyboardButton("Aile", callback_data=f"tag|{chat_id}|{reminder_id}|aile"),
            ],
            [
                InlineKeyboardButton("Tag ekleme", callback_data=f"tag|{chat_id}|{reminder_id}|none"),
            ],
        ]
        await update.message.reply_text(
            message_text + "\n\nTag eklemek ister misiniz?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        await update.message.reply_text(message_text)


async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kullanıcının bekleyen hatırlatmalarını tag'lara göre gruplandırarak listeler."""
    chat_id = update.effective_chat.id
    reminder_list = reminders.get(chat_id, [])

    if not reminder_list:
        await update.message.reply_text("Şu anda bekleyen hatırlatman yok.")
        return

    # Tag'lara göre grupla
    grouped = defaultdict(list)
    for item in reminder_list:
        tag = item.get('tag', 'Genel')
        grouped[tag].append(item)

    lines = ["Bekleyen hatırlatmalar (tag'lara göre):"]
    for tag in sorted(grouped.keys()):
        lines.append(f"\n{tag.upper()}:")
        for item in grouped[tag]:
            lines.append(
                f"  - {item['message']} ({item['due_time'].strftime('%Y-%m-%d %H:%M UTC')})"
            )

    await update.message.reply_text("\n".join(lines))


async def cancel_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hatırlatma iptal etme komutu. 'all' ile tümünü siler."""
    chat_id = update.effective_chat.id
    arg = context.args[0].lower() if context.args else "last"

    if arg == "all":
        reminder_list = reminders.get(chat_id, [])
        if not reminder_list:
            await update.message.reply_text("Silinecek hatırlatma yok.")
            return

        # Tüm job'ları kaldır
        for item in reminder_list:
            try:
                scheduler.remove_job(item["job_id"])
            except JobLookupError:
                pass

        # Sözlükten temizle
        reminders[chat_id] = []
        await update.message.reply_text("✅ Tüm hatırlatmalar silindi.")
        save_reminders()
        return

    if arg in ("last", "son"):
        reminder_list = reminders.get(chat_id, [])
        if not reminder_list:
            await update.message.reply_text("Silinecek hatırlatma yok.")
            return

        reminder = reminder_list[-1]
    else:
        try:
            reminder_id = int(arg)
        except ValueError:
            await update.message.reply_text("Lütfen geçerli bir hatırlatma ID'si gir, 'last' / 'son' kullan ya da 'all' yaz.")
            return

        reminder_list = reminders.get(chat_id, [])
        reminder = next((r for r in reminder_list if r["id"] == reminder_id), None)
        if not reminder:
            await update.message.reply_text(f"ID {reminder_id} için bir hatırlatma bulunamadı.")
            return

    try:
        scheduler.remove_job(reminder["job_id"])
    except JobLookupError:
        pass

    reminders[chat_id] = [r for r in reminders.get(chat_id, []) if r["id"] != reminder["id"]]
    await update.message.reply_text(f"✅ Son eklenen hatırlatmanız iptal edildi: {reminder['message']}")
    save_reminders()


async def tag_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback query for selecting a tag after reminder creation."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()
    parts = query.data.split("|")
    if len(parts) != 4 or parts[0] != "tag":
        return

    _, chat_id_str, reminder_id_str, selected_tag = parts
    try:
        chat_id = int(chat_id_str)
        reminder_id = int(reminder_id_str)
    except ValueError:
        await query.edit_message_text("Geçersiz tag seçimi.")
        return

    reminder_list = reminders.get(chat_id, [])
    reminder = next((r for r in reminder_list if r["id"] == reminder_id), None)
    if not reminder:
        await query.edit_message_text("Bu hatırlatma bulunamadı veya silinmiş.")
        return

    if selected_tag == "none":
        reminder["tag"] = None
        await query.edit_message_text(
            "Tamam, bu hatırlatmaya tag eklemedim."
        )
        save_reminders()
        return

    reminder["tag"] = selected_tag
    await query.edit_message_text(
        f"Hatırlatma için \"{selected_tag}\" tag'ı kaydedildi."
    )
    save_reminders()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hataları konsola yazdırır."""
    print(f"Hata: {context.error}")


def main() -> None:
    global application, scheduler
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    scheduler = AsyncIOScheduler(timezone="Europe/Istanbul", event_loop=loop)
    scheduler.start()

    load_reminders()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("remind", remind))
    application.add_handler(CommandHandler("r", remind))
    application.add_handler(CommandHandler("list", list_reminders))
    application.add_handler(CommandHandler("cancel", cancel_reminder))
    application.add_handler(CallbackQueryHandler(tag_callback, pattern=r'^tag\|'))
    application.add_error_handler(error_handler)

    print("Bot çalışıyor... Ctrl+C ile durdurabilirsin.")
    application.run_polling()


if __name__ == "__main__":
    main()
