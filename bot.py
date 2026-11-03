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
from telegram.ext import (Application, CallbackQueryHandler, ChatMemberHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

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
PAY_URL_KIT = os.environ.get("PAY_URL_KIT", "")    # Stripe Payment Link: только план
PAY_URL_FULL = os.environ.get("PAY_URL_FULL", "")  # Stripe Payment Link: план + консультация
PAY_URL_PLUS = os.environ.get("PAY_URL_PLUS", "")  # Stripe Payment Link: план + консультация + сопровождение
# Ссылки по цене эфира (69 / 168 / 303 €); в боте включаются командой /prices webinar
PAY_URL_KIT_WEB = os.environ.get("PAY_URL_KIT_WEB", "")
PAY_URL_FULL_WEB = os.environ.get("PAY_URL_FULL_WEB", "")
PAY_URL_PLUS_WEB = os.environ.get("PAY_URL_PLUS_WEB", "")

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
        [Btn(T.BTN_KIT, callback_data="kit")],
        [Btn(T.BTN_REVIEW, callback_data="rvwrite")],
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


def who(u):
    name = (u.first_name or "").strip() or "без имени"
    return f"{name} (@{u.username})" if u.username else name


async def notify_admins(ctx, text):
    """Сообщение администраторам о новом событии. Ошибки не мешают работе бота."""
    for a in ADMIN_IDS:
        try:
            await ctx.bot.send_message(a, text)
        except Exception as e:
            log.warning("Не удалось уведомить %s: %s", a, e)


# ---------- для всех ----------
PAID_SOURCES = {"plan": "план", "konsult": "план + консультация",
                "soprov": "план + консультация + сопровождение"}


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    source = ctx.args[0][:64] if ctx.args else None
    is_new = await db.get_user(u.id) is None
    await db.upsert_user(u.id, u.first_name, u.username, source)
    if is_new:
        await notify_admins(ctx, f"🆕 Новый пользователь: {who(u)}" + (f"\nИсточник: {source}" if source else ""))
    if source in PAID_SOURCES:  # сюда Stripe переадресует после оплаты
        await show_paid(update.effective_chat.id, u, source, ctx)
        return
    if not is_new:
        caption = T.WELCOME_BACK.format(name=u.first_name or "")
        photo = None
    else:
        caption = T.WELCOME.format(name=u.first_name or "")
        photo = FILES / "welcome.jpg"
    if photo is not None and photo.exists():
        with photo.open("rb") as f:
            await update.message.reply_photo(f, caption=caption, reply_markup=main_menu())
    else:
        await update.message.reply_text(caption, reply_markup=main_menu())
    if source == "kit":  # ссылка t.me/ritm_goda_bot?start=kit сразу ведёт к покупке
        await show_kit(update.effective_chat.id, u.id, ctx)
    if source == "webinar":  # ссылка t.me/ritm_goda_bot?start=webinar сразу ведёт к записи
        await show_webinar(update.effective_chat.id, ctx)
    if source in ("efir", "otzyv"):  # ссылка на отзыв (в чат Zoom после эфира или для остальных)
        if source == "efir":
            await db.mark_attended(u.id)
        ctx.user_data["await_review"] = True
        await ctx.bot.send_message(update.effective_chat.id, T.REVIEW_ASK)
    if source == "vopros":  # ссылка t.me/ritm_goda_bot?start=vopros сразу открывает вопрос
        await ctx.bot.send_message(update.effective_chat.id, T.ASK_DIRECT,
                                   reply_markup=Kb([[Btn(T.BTN_QUESTION, url=DIRECT_URL)]]))


async def send_guide12(chat_id, ctx):
    """Подарок после оплаты: гайд «12 дней». Файл хранится в Telegram (file_id), не в репозитории."""
    fid = await db.get_setting("guide12_file_id")
    if not fid:
        return False
    try:
        await ctx.bot.send_document(chat_id, fid, caption=T.GUIDE12_CAPTION)
        pid = await db.get_setting("guide12print_file_id")
        if pid:
            await ctx.bot.send_document(chat_id, pid, caption=T.PRINT_CAPTION)
        return True
    except Exception as e:
        log.warning("Не удалось отправить гайд 12 дней: %s", e)
        return False


async def show_paid(chat_id, u, source, ctx):
    await notify_admins(ctx, f"💳 После оплаты зашёл(зашла) в бота: {who(u)}\n"
                             f"Тариф: {PAID_SOURCES[source]} · Telegram ID: {u.id}\n"
                             f"(в Stripe это client_reference_id tg{u.id}; проверьте оплату)")
    if source == "plan":
        await ctx.bot.send_message(chat_id, T.PAID_PLAN)
    else:
        text = T.CONSULT_ASK.format(plus=T.CONSULT_PLUS if source == "soprov" else "")
        await ctx.bot.send_message(chat_id, text, reply_markup=Kb([
            [Btn(T.BTN_CONS_ALEX, callback_data=f"cons:{source}:alex"),
             Btn(T.BTN_CONS_LUDA, callback_data=f"cons:{source}:luda")],
            [Btn(T.BTN_CONS_ANY, callback_data=f"cons:{source}:any")]]))
    if not await send_guide12(chat_id, ctx):
        await notify_admins(ctx, f"⚠️ Гайд «12 дней» не отправлен ({who(u)}): файл не загружен в бота. "
                                 "Отправьте боту PDF с подписью guide12 и перешлите покупателю вручную.")


async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Администратор отправляет боту PDF с подписью guide12: бот запоминает файл."""
    if not is_admin(update):
        await on_private_message(update, ctx)  # обычный человек: подсказка про директ
        return
    m = update.effective_message
    cap = (m.caption or "").strip().lower()
    if m.document and cap == "guide12":
        await db.set_setting("guide12_file_id", m.document.file_id)
        await m.reply_text(T.GUIDE12_SAVED)
    elif m.document and cap == "guide12print":
        await db.set_setting("guide12print_file_id", m.document.file_id)
        await m.reply_text(T.GUIDE12P_SAVED)


async def on_consult_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, source, choice = q.data.split(":")
    if choice == "help":  # старые сообщения с кнопкой «Помогите выбрать»
        choice = "any"
    if source not in PAID_SOURCES or choice not in ("alex", "luda", "any"):
        return
    u = q.from_user
    await db.upsert_user(u.id, u.first_name, u.username)
    await db.set_consult(u.id, source, choice)
    await q.edit_message_reply_markup(None)
    if choice == "any":
        await ctx.bot.send_message(q.message.chat_id, T.CONSULT_ANY_DONE)
        lab = "без разницы, к кому (решаете сами)"
    else:
        await ctx.bot.send_message(q.message.chat_id, T.CONSULT_DONE.format(name=T.CONSULT_NAMES[choice]))
        lab = "выбрал(а) " + {"alex": "Александру", "luda": "Людмилу"}[choice]
    await notify_admins(ctx, f"🗓 Консультация: {who(u)} {lab}\n"
                             f"Тариф: {PAID_SOURCES[source]} · Telegram ID: {u.id}")


async def consults_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    rows = await db.last_consults(20)
    if not rows:
        await update.message.reply_text("Пока никто не выбрал консультанта.")
        return
    lab = {"alex": "Александра", "luda": "Людмила", "any": "без разницы", "help": "без разницы"}
    out = [f"{r['created_at'].astimezone(BERLIN).strftime('%d.%m %H:%M')} · "
           f"{r['first_name'] or 'без имени'}{' @' + r['username'] if r['username'] else ''} · "
           f"{PAID_SOURCES.get(r['product'], r['product'])} → {lab.get(r['choice'], r['choice'])} "
           f"(tg{r['user_id']})" for r in rows]
    await update.message.reply_text("\n".join(out)[:4000])


async def on_guide(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    with (FILES / "gayd_lichnyy_god_2027.pdf").open("rb") as f:
        await ctx.bot.send_document(q.message.chat_id, f, filename="Гайд Личный год 2027.pdf",
                                    caption=T.GUIDE_CAPTION)
    pr = FILES / "gayd_lichnyy_god_2027_pechat.pdf"
    if pr.exists():
        with pr.open("rb") as f:
            await ctx.bot.send_document(q.message.chat_id, f, filename="Гайд Личный год 2027 для печати.pdf",
                                        caption=T.PRINT_CAPTION)
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
    ws = await db.upcoming_webinars()
    if not ws:
        await ctx.bot.send_message(chat_id, T.NO_WEBINAR,
                                   reply_markup=Kb([[Btn(T.BTN_CHANNEL, url=CHANNEL_URL)]]))
        return
    lines, rows = [], []
    for w in ws:
        b = w["starts_at"].astimezone(BERLIN)
        t = fmt_times(w["starts_at"])
        extra = "" if w["title"] == T.DEFAULT_TITLE else f" · «{w['title']}»"
        lines.append(T.WEBINAR_LINE.format(wd=T.WEEKDAYS[b.weekday()], day=b.day, month=T.MONTHS[b.month],
                                           title=extra, **{k: t[k] for k in ("time_berlin", "time_msk")}))
        rows.append([Btn(T.WEBINAR_BTN.format(wd_short=T.WD_SHORT[b.weekday()], dm=b.strftime("%d.%m"),
                                              time_berlin=t["time_berlin"]), callback_data=f"reg:{w['id']}")])
    await ctx.bot.send_message(chat_id, T.WEBINAR_INFO.format(dates="\n".join(lines)),
                               reply_markup=Kb(rows))


def pay_url(base, uid):
    """Добавляет Telegram ID к ссылке Stripe, чтобы в оплате было видно, кто купил."""
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}client_reference_id=tg{uid}"


async def show_kit(chat_id, uid, ctx):
    web = (await db.get_setting("prices", "regular")) == "webinar"
    if web:
        links = (PAY_URL_KIT_WEB, PAY_URL_FULL_WEB, PAY_URL_PLUS_WEB)
        prices = (69, 168, 303)
    else:
        links = (PAY_URL_KIT, PAY_URL_FULL, PAY_URL_PLUS)
        prices = (89, 213, 402)
    labels = (T.BTN_BUY_KIT, T.BTN_BUY_FULL, T.BTN_BUY_PLUS)
    rows = []
    for url, label, p in zip(links, labels, prices):
        if url:
            rows.append([Btn(label.format(p=p), url=pay_url(url, uid))])
    info = T.KIT_INFO.format(p1=prices[0], p2=prices[1], p3=prices[2], web=T.WEB_NOTE if web else "")
    text = info if rows else info + "\n\n" + T.KIT_SOON
    rows.append([Btn(T.BTN_QUESTION, url=DIRECT_URL)])
    await ctx.bot.send_message(chat_id, text, reply_markup=Kb(rows))


async def on_kit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    u = q.from_user
    await db.upsert_user(u.id, u.first_name, u.username)
    await show_kit(q.message.chat_id, u.id, ctx)


async def on_webinar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await show_webinar(q.message.chat_id, ctx)


async def on_register(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    wid = int(q.data.split(":")[1])
    w = await db.get_webinar(wid)
    if not w:
        await ctx.bot.send_message(q.message.chat_id, T.NO_WEBINAR)
        return
    u = q.from_user
    await db.upsert_user(u.id, u.first_name, u.username)
    new = await db.register(u.id, wid)
    await q.edit_message_reply_markup(None)
    t = fmt_times(w["starts_at"])
    if new:
        await notify_admins(ctx, f"✅ Записалась(ся) на эфир: {who(u)}\n"
                                 f"{t['date']}, {t['time_berlin']} (Берлин) · «{w['title']}»")
    text = (T.REGISTERED if new else T.ALREADY_REGISTERED).format(title=w["title"], **t)
    await ctx.bot.send_message(q.message.chat_id, text,
                               reply_markup=Kb([[Btn(T.BTN_UNREGISTER, callback_data=f"unreg:{wid}")],
                                                [Btn(T.BTN_WEBINAR, callback_data="webinar")],
                                                [Btn(T.BTN_CHANNEL, url=CHANNEL_URL)]]))


async def on_unregister(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    wid = int(q.data.split(":")[1])
    await db.unregister(q.from_user.id, wid)
    await q.edit_message_text(T.UNREGISTERED)
    w = await db.get_webinar(wid)
    if w:
        t = fmt_times(w["starts_at"])
        await notify_admins(ctx, f"❌ Отменила(ся) запись на эфир: {who(q.from_user)}\n"
                                 f"{t['date']}, {t['time_berlin']} (Берлин)")


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
    x = await db.counts_extra()
    lines = [f"Всего пользователей: {s['total']}", f"Согласились на рассылку: {s['subscribed']}",
             f"Отписались/заблокировали: {s['stopped']}", f"Новых за 7 дней: {s['week']}",
             f"Нажали «был на эфире»: {x['attended']}", f"Оставили отзыв: {x['reviews']}", "",
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


async def prices_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    mode = (ctx.args[0].lower() if ctx.args else "")
    if mode not in ("webinar", "regular"):
        cur = await db.get_setting("prices", "regular")
        await update.message.reply_text(
            f"Сейчас в боте: {'цены эфира' if cur == 'webinar' else 'обычные цены'}.\n"
            "Формат: /prices webinar или /prices regular")
        return
    if mode == "webinar" and not (PAY_URL_KIT_WEB and PAY_URL_FULL_WEB and PAY_URL_PLUS_WEB):
        await update.message.reply_text("Не заданы ссылки PAY_URL_KIT_WEB, PAY_URL_FULL_WEB, PAY_URL_PLUS_WEB в Render.")
        return
    await db.set_setting("prices", mode)
    if mode == "webinar":
        await update.message.reply_text(
            "✅ В боте цены эфира: 69 / 168 / 303 €.\n"
            "Не забудьте включить три ссылки по цене эфира в Stripe и выключить их через 48 часов, "
            "а затем отправить /prices regular.")
    else:
        await update.message.reply_text(
            "✅ В боте обычные цены: 89 / 213 / 402 €.\n"
            "Проверьте, что ссылки по цене эфира в Stripe выключены.")


async def reviews_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    rows = await db.last_reviews(10)
    if not rows:
        await update.message.reply_text("Отзывов пока нет.")
        return
    lab = {"name": "можно с именем", "anon": "можно анонимно", "no": "не публиковать", None: "ответ не получен"}
    out = [f"№{r['id']} · {r['first_name'] or 'без имени'}"
           f"{' @' + r['username'] if r['username'] else ''} · {lab.get(r['consent'], r['consent'])}\n{r['text']}"
           for r in rows]
    await update.message.reply_text("\n\n".join(out)[:4000])


async def on_review_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    text = (update.effective_message.text or "").strip()
    if not text:
        return
    ctx.user_data["await_review"] = False
    rid = await db.add_review(u.id, text[:3500])
    await update.effective_message.reply_text(T.REVIEW_PUBLISH, reply_markup=Kb([
        [Btn(T.BTN_RV_NAME, callback_data=f"rv:{rid}:name")],
        [Btn(T.BTN_RV_ANON, callback_data=f"rv:{rid}:anon")],
        [Btn(T.BTN_RV_NO, callback_data=f"rv:{rid}:no")]]))
    await notify_admins(ctx, f"⭐ Новый отзыв от {who(u)}:\n\n{text[:3500]}")


async def on_review_consent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, rid, val = q.data.split(":")
    if await db.set_review_consent(int(rid), q.from_user.id, val):
        await q.edit_message_text(T.REVIEW_DONE)
        lab = {"name": "можно публиковать с именем", "anon": "можно публиковать анонимно",
               "no": "не публиковать"}[val]
        await notify_admins(ctx, f"Отзыв №{rid} ({who(q.from_user)}): {lab}.")


async def on_review_write(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.user_data["await_review"] = True
    await ctx.bot.send_message(q.message.chat_id, T.REVIEW_ASK)


async def on_my_chat_member(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    m = update.my_chat_member
    if m.chat.type != "private":
        return
    if m.new_chat_member.status == "kicked":
        await db.stop_user(m.from_user.id)
        await notify_admins(ctx, f"🚫 Заблокировала(ся) бота: {who(m.from_user)}")


async def review_reminder_job(ctx: ContextTypes.DEFAULT_TYPE):
    uids = await db.due_review_reminders()
    if not uids:
        return
    await db.mark_review_reminded(uids)  # сначала отмечаем, чтобы не отправить дважды
    kb = Kb([[Btn(T.BTN_RV_WRITE, callback_data="rvwrite")]])
    ok = await mass_send(ctx.bot, uids, lambda uid: ctx.bot.send_message(uid, T.REVIEW_REMIND, reply_markup=kb))
    log.info("Напоминание об отзыве: %s из %s", ok, len(uids))


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


# ---------- свободные сообщения ----------
async def on_private_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Бот не принимает вопросы: подсказываем написать в директ канала."""
    if ctx.user_data.get("await_review"):
        await on_review_text(update, ctx)
        return
    if update.effective_user.id in ADMIN_IDS:
        return
    await update.effective_message.reply_text(
        T.ASK_DIRECT, reply_markup=Kb([[Btn(T.BTN_QUESTION, url=DIRECT_URL)]]))

def build_app() -> Application:
    app = (Application.builder().token(BOT_TOKEN)
           .post_init(post_init).post_shutdown(post_shutdown).build())
    for name, fn in [("start", start), ("stop", stop), ("delete_me", delete_me), ("privacy", privacy),
                     ("myid", myid), ("admin", admin_help), ("stats", stats), ("webinars", webinars),
                     ("webinar_add", webinar_add), ("webinar_link", webinar_link),
                     ("webinar_del", webinar_del), ("broadcast", broadcast),
                     ("send_registered", send_registered), ("prices", prices_cmd),
                     ("reviews", reviews_cmd), ("consults", consults_cmd)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_guide, pattern="^guide$"))
    app.add_handler(CallbackQueryHandler(on_webinar, pattern="^webinar$"))
    app.add_handler(CallbackQueryHandler(on_kit, pattern="^kit$"))
    app.add_handler(CallbackQueryHandler(on_consent, pattern="^consent:"))
    app.add_handler(CallbackQueryHandler(on_register, pattern="^reg:"))
    app.add_handler(CallbackQueryHandler(on_unregister, pattern="^unreg:"))
    app.add_handler(CallbackQueryHandler(on_broadcast_confirm, pattern="^bc:"))
    app.add_handler(CallbackQueryHandler(on_review_consent, pattern="^rv:"))
    app.add_handler(CallbackQueryHandler(on_review_write, pattern="^rvwrite$"))
    app.add_handler(CallbackQueryHandler(on_consult_choice, pattern="^cons:"))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_private_message))
    app.add_error_handler(on_error)
    app.job_queue.run_repeating(reminder_job, interval=60, first=10)
    app.job_queue.run_repeating(review_reminder_job, interval=600, first=60)
    return app


if __name__ == "__main__":
    if not BOT_TOKEN or not DATABASE_URL:
        raise SystemExit("Не заданы BOT_TOKEN и/или DATABASE_URL")
    build_app().run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
