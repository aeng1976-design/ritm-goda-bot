# -*- coding: utf-8 -*-
"""Бот-помощница канала «Ритм года» (@ritm_goda_bot).

Запуск:  python bot.py
Нужные переменные окружения: BOT_TOKEN, DATABASE_URL, ADMIN_IDS
Необязательные: CHANNEL_URL, DIRECT_URL, PRIVACY_URL
"""
import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, Update
from telegram.error import Forbidden, BadRequest, RetryAfter
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes)

import texts as T
from db import DB

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("ritm")

BERLIN = ZoneInfo("Europe/Berlin")
MOSCOW = ZoneInfo("Europe/Moscow")
FILES = Path(__file__).parent / "files"

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x}
CHANNEL_URL = os.environ.get("CHANNEL_URL", "https://t.me/ritm_goda")
DIRECT_URL = os.environ.get("DIRECT_URL", "https://t.me/ritm_goda?direct")
PRIVACY_URL = os.environ.get("PRIVACY_URL", "")

db = DB(DATABASE_URL)


# ---------- помощники ----------
def is_admin(update: Update) -> bool:
    return update.effective_user is not None and update.effective_user.id in ADMIN_IDS


def fmt_times(starts_at):
    b = starts_at.astimezone(BERLIN)
    m = starts_at.astimezone(MOSCOW)
    date = f"{T.WEEKDAYS[b.weekday()].capitalize()}, {b.day} {T.MONTHS[b.month]}"
    return dict(date=date, time_berlin=b.strftime("%H:%M"), time_msk=m.strftime("%H:%M"))


def main_menu():
    return Kb([
        [Btn(T.BTN_GUIDE, callback_data="guide")],
        [Btn(T.BTN_WEBINAR, callback_data="webinar")],
        [Btn(T.BTN_CHANNEL, url=CHANNEL_URL)],
        [Btn(T.BTN_QUESTION, url=DIRECT_URL)],
    ])


async def safe_send(bot, uid, coro_factory):
    """Отправка одному человеку с обработкой блокировок и лимитов."""
    for _ in range(3):
        try:
            await coro_factory()
            return True
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except Forbidden:
            await db.stop_user(uid)  # человек заблокировал бота
            return False
        except BadRequest as e:
            log.warning("BadRequest для %s: %s", uid, e)
            return False
    return False


async def mass_send(bot, uids, send_one):
    ok = 0
    for uid in uids:
        if await safe_send(bot, uid, lambda u=uid: send_one(u)):
            ok += 1
        await asyncio.sleep(0.05)  # не больше ~20 сообщений в секунду
    return ok


# ---------- для всех ----------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    source = ctx.args[0][:64] if ctx.args else None
    await db.upsert_user(u.id, u.first_name, u.username, source)
    caption = T.WELCOME.format(name=u.first_name or "")
    photo = FILES / "welcome.jpg"
    if photo.exists():
        with photo.open("rb") as f:
            await update.message.reply_photo(f, caption=caption, reply_markup=main_menu())
    else:
        await update.message.reply_text(caption, reply_markup=main_menu())
    if source == "webinar":  # ссылка t.me/ritm_goda_bot?start=webinar сразу ведёт к записи
        await show_webinar(update.effective_chat.id, ctx)


async def on_guide(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    with (FILES / "gayd_lichnyy_god_2027.pdf").open("rb") as f:
        await ctx.bot.send_document(q.message.chat_id, f, filename="Гайд Личный год 2027.pdf",
                                    caption=T.GUIDE_CAPTION)
    user = await db.get_user(q.from_user.id)
    if user and user["news_consent"] is None:
        await ctx.bot.send_message(q.message.chat_id, T.ASK_CONSENT, reply_markup=Kb([[
            Btn(T.BTN_CONSENT_YES, callback_data="consent:1"),
            Btn(T.BTN_CONSENT_NO, callback_data="consent:0")]]))


async def on_consent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    yes = q.data.endswith("1")
    await db.set_consent(q.from_user.id, yes)
    await q.edit_message_text(T.CONSENT_YES if yes else T.CONSENT_NO)


async def show_webinar(chat_id, ctx):
    w = await db.next_webinar()
    if not w:
        await ctx.bot.send_message(chat_id, T.NO_WEBINAR,
                                   reply_markup=Kb([[Btn(T.BTN_CHANNEL, url=CHANNEL_URL)]]))
        return
    text = T.WEBINAR_INFO.format(title=w["title"], **fmt_times(w["starts_at"]))
    await ctx.bot.send_message(chat_id, text, reply_markup=Kb([
        [Btn(T.BTN_REGISTER, callback_data=f"reg:{w['id']}")]]))


async def on_webinar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await show_webinar(q.message.chat_id, ctx)


async def on_register(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    wid = int(q.data.split(":")[1])
    u = q.from_user
    await db.upsert_user(u.id, u.first_name, u.username)
    new = await db.register(u.id, wid)
    await q.edit_message_reply_markup(None)
    await ctx.bot.send_message(q.message.chat_id, T.REGISTERED if new else T.ALREADY_REGISTERED,
                               reply_markup=Kb([[Btn(T.BTN_UNREGISTER, callback_data=f"unreg:{wid}")],
                                                [Btn(T.BTN_CHANNEL, url=CHANNEL_URL)]]))


async def on_unregister(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await db.unregister(q.from_user.id, int(q.data.split(":")[1]))
    await q.edit_message_text(T.UNREGISTERED)


async def stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await db.stop_user(update.effective_user.id)
    await update.message.reply_text(T.STOPPED)


async def delete_me(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await db.delete_user(update.effective_user.id)
    await update.message.reply_text(T.DELETED)


async def privacy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(T.PRIVACY.format(privacy_url=PRIVACY_URL), disable_web_page_preview=True)


async def myid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Ваш Telegram ID: {update.effective_user.id}")


# ---------- для администраторов ----------
async def admin_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if is_admin(update):
        await update.message.reply_text(T.ADMIN_HELP)


async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    s = await db.stats()
    src = await db.sources()
    lines = [f"Всего пользователей: {s['total']}", f"Согласились на рассылку: {s['subscribed']}",
             f"Отписались/заблокировали: {s['stopped']}", f"Новых за 7 дней: {s['week']}", "",
             "Откуда пришли:"] + [f"  {r['source']}: {r['n']}" for r in src]
    await update.message.reply_text("\n".join(lines))


async def webinars(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    rows = await db.webinars()
    if not rows:
        await update.message.reply_text("Эфиров пока нет. Добавьте: /webinar_add 2026-11-15 15:00 Название")
        return
    out = []
    for w in rows:
        t = fmt_times(w["starts_at"])
        out.append(f"№{w['id']} · {t['date']} {t['time_berlin']} · «{w['title']}»\n"
                   f"   записались: {w['regs']} · ссылка: {w['link'] or 'нет'}")
    await update.message.reply_text("\n\n".join(out), disable_web_page_preview=True)


async def webinar_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    try:
        d, t, *title = ctx.args
        starts = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M").replace(tzinfo=BERLIN)
        title = " ".join(title).strip() or "Какой будет ваш 2027 год"
    except Exception:
        await update.message.reply_text("Формат: /webinar_add 2026-11-15 15:00 Какой будет ваш 2027 год")
        return
    wid = await db.add_webinar(starts, title)
    await update.message.reply_text(f"Эфир №{wid} добавлен: {fmt_times(starts)['date']}, "
                                    f"{fmt_times(starts)['time_berlin']} по Берлину. "
                                    f"Ссылку добавьте командой /webinar_link {wid} https://...")


async def webinar_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    try:
        wid, link = int(ctx.args[0]), ctx.args[1]
    except Exception:
        await update.message.reply_text("Формат: /webinar_link 1 https://...")
        return
    ok = await db.set_link(wid, link)
    await update.message.reply_text("Ссылка сохранена ✅" if ok else "Эфир с таким номером не найден.")


async def webinar_del(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    try:
        ok = await db.delete_webinar(int(ctx.args[0]))
    except Exception:
        ok = False
    await update.message.reply_text("Эфир удалён." if ok else "Формат: /webinar_del 1")


async def broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ответьте /broadcast на любое сообщение — бот попросит подтверждение."""
    if not is_admin(update):
        return
    src = update.message.reply_to_message
    if not src:
        await update.message.reply_text("Ответьте командой /broadcast на сообщение, которое нужно разослать.")
        return
    n = len(await db.news_recipients())
    ctx.user_data["bc"] = (src.chat_id, src.message_id)
    await update.message.reply_text(f"Разослать это сообщение {n} подписчикам?", reply_markup=Kb([[
        Btn("Да, отправить", callback_data="bc:yes"), Btn("Отмена", callback_data="bc:no")]]))


async def on_broadcast_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        return
    data = ctx.user_data.pop("bc", None)
    if q.data == "bc:no" or not data:
        await q.edit_message_text("Рассылка отменена.")
        return
    chat_id, msg_id = data
    uids = await db.news_recipients()
    await q.edit_message_text(f"Отправляю {len(uids)} подписчикам…")
    ok = await mass_send(ctx.bot, uids, lambda uid: ctx.bot.copy_message(uid, chat_id, msg_id))
    await ctx.bot.send_message(q.from_user.id, f"Рассылка завершена: доставлено {ok} из {len(uids)}.")


async def send_registered(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    src = update.message.reply_to_message
    try:
        wid = int(ctx.args[0])
    except Exception:
        wid = None
    if not src or wid is None:
        await update.message.reply_text("Ответьте на сообщение командой /send_registered 1 (номер эфира).")
        return
    uids = await db.registrants(wid)
    ok = await mass_send(ctx.bot, uids, lambda uid: ctx.bot.copy_message(uid, src.chat_id, src.message_id))
    await update.message.reply_text(f"Отправлено записавшимся на эфир №{wid}: {ok} из {len(uids)}.")


async def gift12(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    try:
        wid = int(ctx.args[0])
    except Exception:
        await update.message.reply_text("Формат: /gift12 1 (номер эфира)")
        return
    uids = await db.registrants(wid)
    path = FILES / "gayd_12_dney_2027.pdf"
    cap = "Ваш подарок с эфира — гайд-практикум «12 дней, которые задают год» 🎁"

    async def one(uid):
        with path.open("rb") as f:
            await ctx.bot.send_document(uid, f, filename="Гайд 12 дней 2027.pdf", caption=cap)
    ok = await mass_send(ctx.bot, uids, one)
    await update.message.reply_text(f"Гайд «12 дней» отправлен: {ok} из {len(uids)}.")


# ---------- напоминания (проверка раз в минуту) ----------
ORDER = {"sent_24h": 1, "sent_1h": 2, "sent_10m": 3}


async def reminder_job(ctx: ContextTypes.DEFAULT_TYPE):
    due = await db.due_reminders()
    latest = {}
    for w, kind in due:  # если бот был выключен — шлём только самое свежее напоминание
        if w["id"] not in latest or ORDER[kind] > ORDER[latest[w["id"]][1]]:
            latest[w["id"]] = (w, kind)
    for w, kind in latest.values():
        await db.mark_sent(w["id"], kind)  # сначала отмечаем, чтобы не отправить дважды
        t = fmt_times(w["starts_at"])
        if kind == "sent_24h":
            text = T.REMIND_24H.format(title=w["title"], **t)
        elif kind == "sent_1h":
            text = T.REMIND_1H.format(title=w["title"], **t)
        else:
            text = (T.REMIND_10M_LINK.format(link=w["link"]) if w["link"]
                    else T.REMIND_10M_NOLINK.format(channel=CHANNEL_URL))
        uids = await db.registrants(w["id"])
        ok = await mass_send(ctx.bot, uids, lambda uid: ctx.bot.send_message(uid, text))
        log.info("Напоминание %s по эфиру %s: %s из %s", kind, w["id"], ok, len(uids))
        for a in ADMIN_IDS:
            try:
                await ctx.bot.send_message(a, f"Напоминание ({kind}) по эфиру №{w['id']} отправлено: {ok} из {len(uids)}.")
            except Exception:
                pass


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.exception("Ошибка: %s", ctx.error)


# ---------- запуск ----------
async def post_init(app: Application):
    await db.connect()
    from telegram import BotCommand
    await app.bot.set_my_commands([
        BotCommand("start", "Главное меню"),
        BotCommand("privacy", "Какие данные хранит бот"),
        BotCommand("stop", "Не получать сообщения"),
        BotCommand("delete_me", "Удалить мои данные"),
    ])
    log.info("Бот запущен. Администраторы: %s", ADMIN_IDS or "не заданы!")


async def post_shutdown(app: Application):
    await db.close()


def build_app() -> Application:
    app = (Application.builder().token(BOT_TOKEN)
           .post_init(post_init).post_shutdown(post_shutdown).build())
    for name, fn in [("start", start), ("stop", stop), ("delete_me", delete_me), ("privacy", privacy),
                     ("myid", myid), ("admin", admin_help), ("stats", stats), ("webinars", webinars),
                     ("webinar_add", webinar_add), ("webinar_link", webinar_link),
                     ("webinar_del", webinar_del), ("broadcast", broadcast),
                     ("send_registered", send_registered), ("gift12", gift12)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_guide, pattern="^guide$"))
    app.add_handler(CallbackQueryHandler(on_webinar, pattern="^webinar$"))
    app.add_handler(CallbackQueryHandler(on_consent, pattern="^consent:"))
    app.add_handler(CallbackQueryHandler(on_register, pattern="^reg:"))
    app.add_handler(CallbackQueryHandler(on_unregister, pattern="^unreg:"))
    app.add_handler(CallbackQueryHandler(on_broadcast_confirm, pattern="^bc:"))
    app.add_error_handler(on_error)
    app.job_queue.run_repeating(reminder_job, interval=60, first=10)
    return app


if __name__ == "__main__":
    if not BOT_TOKEN or not DATABASE_URL:
        raise SystemExit("Не заданы BOT_TOKEN и/или DATABASE_URL")
    build_app().run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
