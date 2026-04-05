# Telegram Hatırlatma Botu

Bu proje, Python kullanarak Telegram hatırlatma botu yapman için hazır bir temel sağlar.

## Gereksinimler

- Python 3.10 veya üzeri
- `pip`

## Proje Yapısı

```
reminderbot/
├── bot.py
├── requirements.txt
├── .env
├── README.md
├── .gitignore
```

## Gerekli Kütüphaneler

- `python-telegram-bot` (v20+)
- `APScheduler`
- `python-dotenv`

## Kurulum

1. Proje klasörüne git:

```bash
cd /Users/yigittopcu/reminderbot
```

2. Sanal ortam oluştur ve etkinleştir:

```bash
python3 -m venv venv
source venv/bin/activate
```

3. Bağımlılıkları yükle:

```bash
pip install -r requirements.txt
```

4. `.env` dosyasını oluştur ve Telegram bot tokenini ekle:

```env
BOT_TOKEN=senin_telegram_bot_tokenin
```

## Çalıştırma

```bash
source venv/bin/activate
python bot.py
```

Bot çalışmaya başladıktan sonra Telegram'da botuna `/start`, `/remind`, `/list` ve `/cancel` komutlarını gönderebilirsin.

## Komutlar

- `/start` — Hoş geldin mesajı gösterir.
- `/remind <dakika> <mesaj>` — Belirtilen dakika sonra hatırlatma gönderir.
  - Örnek: `/remind 10 İlaç iç`
- `/list` — Bekleyen hatırlatmaları listeler.
- `/cancel <id>` — Belirtilen id'li hatırlatmayı iptal eder.

## Not

`.env` dosyası `.gitignore` içinde bulunduğu için token gizli kalır.
