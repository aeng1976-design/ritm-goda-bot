# Бот-помощница канала «Ритм года» (@ritm_goda_bot)

Подробная инструкция по запуску — в PDF «Инструкция по запуску бота Ритм года».

Коротко:
- Render → New → Background Worker → этот репозиторий
- Build: `pip install -r requirements.txt` · Start: `python bot.py`
- Переменные: `BOT_TOKEN`, `DATABASE_URL`, `ADMIN_IDS` (через запятую), по желанию `CHANNEL_URL`, `DIRECT_URL`, `PRIVACY_URL`
- Все тексты бота — в `texts.py`, файлы (гайды, картинка) — в папке `files/`
