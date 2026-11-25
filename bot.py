# -*- coding: utf-8 -*-
"""Бот-помощница канала «Ритм года» (@ritm_goda_bot).

Запуск:  python bot.py
Нужные переменные окружения: BOT_TOKEN, DATABASE_URL, ADMIN_IDS
Необязательные: CHANNEL_URL, DIRECT_URL, PRIVACY_URL
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, Update
from telegram.error import Forbidden, BadRequest, RetryAfter
from telegram.ext import (Application, CallbackQueryHandler, ChatMemberHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

import texts as T
import day
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
        [Btn(T.BTN_DAY, callback_data="den:open")],
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


async def mass_send(bot, uids, send_one, failed=None):
    ok = 0
    for uid in uids:
        if await safe_send(bot, uid, lambda u=uid: send_one(u)):
            ok += 1
        elif failed is not None:
            failed.append(uid)
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
    if source in day.DEN_PAID:  # оплата «Мой день»
        await day.on_paid(update.effective_chat.id, u, source, ctx)
        return
    if source == "den":  # ссылка t.me/ritm_goda_bot?start=den сразу открывает «Мой день»
        await day.open_day(update.effective_chat.id, u.id, ctx)
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
    if source and (source == "kit" or source.startswith("kit_")):  # ссылка t.me/ritm_goda_bot?start=kit сразу ведёт к покупке
        await show_kit(update.effective_chat.id, u.id, ctx)
    if source and (source == "webinar" or source.startswith("webinar_")):  # ссылка t.me/ritm_goda_bot?start=webinar сразу ведёт к записи
        await show_webinar(update.effective_chat.id, ctx)
    if source in ("efir", "otzyv"):  # ссылка на отзыв (в чат Zoom после эфира или для остальных)
        if source == "efir":
            await db.mark_attended(u.id)
            schedule_review_check(ctx.job_queue)
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
        await ctx.bot.send_message(chat_id, T.PAID_PLAN.format(confirm=T.ORDER_CONFIRM))
    else:
        text = T.CONSULT_ASK.format(plus=T.CONSULT_PLUS if source == "soprov" else "", confirm=T.ORDER_CONFIRM)
        await ctx.bot.send_message(chat_id, text, reply_markup=Kb([
            [Btn(T.BTN_CONS_ALEX, callback_data=f"cons:{source}:alex"),
             Btn(T.BTN_CONS_LUDA, callback_data=f"cons:{source}:luda")],
            [Btn(T.BTN_CONS_ANY, callback_data=f"cons:{source}:any")]]))
    try:
        await day.grant_plan_year(chat_id, u, ctx, source)
    except Exception as e:
        log.warning("«Мой день» к плану: %s", e)
        await notify_admins(ctx, f"⚠️ Не удалось открыть «Мой день» на год для {who(u)}: дайте доступ /den_access {u.id} 365")
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
    await ctx.bot.send_message(q.message.chat_id, T.CHANNEL_INVITE,
                               reply_markup=Kb([[Btn(T.BTN_CHANNEL_SUB, url=CHANNEL_URL)]]))
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
                                                [Btn(T.BTN_CHANNEL_SUB, url=CHANNEL_URL)],
                                                [Btn(T.BTN_WEBINAR, callback_data="webinar")]]))


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


async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = await db.get_user(uid)
    if not user:
        await update.message.reply_text(T.STATUS_NONE)
        return
    if user["stopped"]:
        news = T.STATUS_STOPPED
    elif user["news_consent"] is True:
        news = T.STATUS_NEWS_YES
    elif user["news_consent"] is False:
        news = T.STATUS_NEWS_NO
    else:
        news = T.STATUS_NEWS_UNKNOWN
    regs = await db.user_registrations(uid)
    if regs:
        lines = []
        for w in regs:
            b_ = w["starts_at"].astimezone(BERLIN)
            lines.append(f"• {T.WD_SHORT[b_.weekday()]} {b_.strftime('%d.%m')} в {b_.strftime('%H:%M')} (Берлин)")
        webs = T.STATUS_WEB_YES + "\n" + "\n".join(lines)
    else:
        webs = T.STATUS_WEB_NO
    if user["news_consent"] is True and not user["stopped"]:
        kb = Kb([[Btn(T.BTN_STATUS_NEWS_OFF, callback_data="consent:0")]])
    else:
        kb = Kb([[Btn(T.BTN_STATUS_NEWS_ON, callback_data="consent:1")]])
    rows = list(kb.inline_keyboard)
    if not regs:
        rows.append([Btn(T.BTN_BC_REGISTER, callback_data="webinar")])
    await update.message.reply_text(T.STATUS.format(news=news, webs=webs), reply_markup=Kb(rows))


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


def name_of(r):
    name = (r["first_name"] or "").strip() or "без имени"
    return f"{name} (@{r['username']})" if r["username"] else name


async def regs_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/regs 3 — кто и когда записался на эфир №3. Без номера — по всем предстоящим эфирам."""
    if not is_admin(update):
        return
    if ctx.args:
        try:
            ids = [int(ctx.args[0])]
        except ValueError:
            await update.message.reply_text("Формат: /regs 3 (номер эфира) или просто /regs")
            return
    else:
        ids = [w["id"] for w in await db.upcoming_webinars(limit=100)]
        if not ids:
            await update.message.reply_text("Предстоящих эфиров нет. Список по прошедшему: /regs 2")
            return
    out = []
    for wid in ids:
        w = await db.get_webinar(wid)
        if not w:
            out.append(f"Эфира №{wid} нет.")
            continue
        t = fmt_times(w["starts_at"])
        rows = await db.registrants_full(wid)
        lines = [f"№{wid} · {t['date']} {t['time_berlin']} · записались: {len(rows)}"]
        for i, r in enumerate(rows, 1):
            when = r["created_at"].astimezone(BERLIN).strftime("%d.%m %H:%M")
            src = f" · {r['source']}" if r["source"] else ""
            stop = " · ⛔ заблокировал(а) бота" if r["stopped"] else ""
            lines.append(f"{i}. {name_of(r)} · {when}{src}{stop}")
        out.append("\n".join(lines))
    text = "\n\n".join(out)
    for i in range(0, len(text), 3800):
        await update.message.reply_text(text[i:i + 3800])


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
    await schedule_reminders(ctx.application)
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
    if ok:
        await schedule_reminders(ctx.application)
    await update.message.reply_text("Эфир удалён." if ok else "Формат: /webinar_del 1")


async def _broadcast_ask(update, ctx, mode):
    if not is_admin(update):
        return
    cmd = "/broadcast" if mode == "all" else "/broadcast_unreg"
    src = update.message.reply_to_message
    if not src:
        await update.message.reply_text(f"Ответьте командой {cmd} на сообщение, которое нужно разослать.")
        return
    uids = await (db.news_recipients() if mode == "all" else db.news_unregistered())
    ctx.user_data["bc"] = (src.chat_id, src.message_id, mode)
    who = "подписчикам" if mode == "all" else "подписчикам, которые ещё не записались на эфир (с кнопкой записи)"
    await update.message.reply_text(f"Разослать это сообщение {len(uids)} {who}?", reply_markup=Kb([[
        Btn("Да, отправить", callback_data="bc:yes"), Btn("Отмена", callback_data="bc:no")]]))


async def broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ответьте /broadcast на любое сообщение — бот попросит подтверждение."""
    await _broadcast_ask(update, ctx, "all")


async def broadcast_unreg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Рассылка тем, кто согласился на новости, но не записан ни на один предстоящий эфир."""
    await _broadcast_ask(update, ctx, "unreg")


async def on_broadcast_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        return
    data = ctx.user_data.pop("bc", None)
    if q.data == "bc:no" or not data:
        await q.edit_message_text("Рассылка отменена.")
        return
    chat_id, msg_id, mode = data
    if mode == "all":
        uids = await db.news_recipients()
        markup = None
    else:
        uids = await db.news_unregistered()
        markup = Kb([[Btn(T.BTN_BC_REGISTER, callback_data="webinar")]])
    await q.edit_message_text(f"Отправляю {len(uids)} подписчикам…")
    ok = await mass_send(ctx.bot, uids,
                         lambda uid: ctx.bot.copy_message(uid, chat_id, msg_id, reply_markup=markup))
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
        until = await db.get_setting("prices_until", "")
        extra = ""
        if cur == "webinar" and until:
            extra = f" до {datetime.fromisoformat(until).astimezone(BERLIN):%d.%m в %H:%M}, потом бот сам вернёт обычные"
        await update.message.reply_text(
            f"Сейчас в боте: {'цены эфира' if cur == 'webinar' else 'обычные цены'}{extra}.\n"
            "Формат: /prices webinar (на 48 часов), /prices webinar 24 (на 24 часа) или /prices regular")
        return
    if mode == "webinar" and not (PAY_URL_KIT_WEB and PAY_URL_FULL_WEB and PAY_URL_PLUS_WEB):
        await update.message.reply_text("Не заданы ссылки PAY_URL_KIT_WEB, PAY_URL_FULL_WEB, PAY_URL_PLUS_WEB в Render.")
        return
    await db.set_setting("prices", mode)
    if mode == "webinar":
        try:
            hours = int(ctx.args[1]) if len(ctx.args) > 1 else 48
        except ValueError:
            hours = 48
        until = datetime.now(BERLIN) + timedelta(hours=hours)
        await db.set_setting("prices_until", until.isoformat())
        await update.message.reply_text(
            "✅ В боте цены эфира: 69 / 168 / 303 €.\n"
            f"{until:%d.%m в %H:%M} бот сам вернёт обычные цены и напишет вам.\n"
            "Не забудьте включить три ссылки по цене эфира в Stripe.")
    else:
        await db.set_setting("prices_until", "")
        await update.message.reply_text(
            "✅ В боте обычные цены: 89 / 213 / 402 €.\n"
            "Ссылки по цене эфира в Stripe можно выключить.")


async def prices_job(ctx: ContextTypes.DEFAULT_TYPE):
    """Каждые 10 минут: если время цен эфира прошло, вернуть обычные цены."""
    try:
        if (await db.get_setting("prices", "regular")) != "webinar":
            return
        until = await db.get_setting("prices_until", "")
        if not until or datetime.now(BERLIN) < datetime.fromisoformat(until):
            return
        await db.set_setting("prices", "regular")
        await db.set_setting("prices_until", "")
        await notify_admins(ctx, "⏰ 48 часов прошли: в боте снова обычные цены 89 / 213 / 402 €.\n"
                                 "Выключите три ссылки по цене эфира в Stripe.")
    except Exception as e:
        log.warning("prices_job: %s", e)


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


REVIEW_LOCK = asyncio.Lock()
REM_LOCK = asyncio.Lock()


async def review_reminder_job(ctx: ContextTypes.DEFAULT_TYPE):
    async with REVIEW_LOCK:  # чтобы два задания не отправили одно напоминание дважды
        uids = await db.due_review_reminders()
        if not uids:
            return
        await db.mark_review_reminded(uids)  # сначала отмечаем, чтобы не отправить дважды
    kb = Kb([[Btn(T.BTN_RV_WRITE, callback_data="rvwrite")]])
    ok = await mass_send(ctx.bot, uids, lambda uid: ctx.bot.send_message(uid, T.REVIEW_REMIND, reply_markup=kb))
    log.info("Напоминание об отзыве: %s из %s", ok, len(uids))


def schedule_review_check(jq):
    """Проверка отзывов через ~20 часов после отметки «был на эфире».
    Все отметки в течение одного часа объединяются в одно задание."""
    when = datetime.now(timezone.utc) + timedelta(hours=20, minutes=5)
    when = when.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    name = "review:" + when.strftime("%Y%m%d%H")
    if not jq.get_jobs_by_name(name):
        jq.run_once(review_reminder_job, when=when, name=name)


# ---------- напоминания об эфирах ----------
# Раньше бот проверял базу каждую минуту, и база Neon никогда не «засыпала»
# (бесплатный лимит 100 CU-часов в месяц заканчивался к середине месяца).
# Теперь напоминания ставятся по расписанию: база нужна только в момент отправки.
REM_KINDS = (("sent_24h", timedelta(hours=24)), ("sent_1h", timedelta(hours=1)),
             ("sent_10m", timedelta(minutes=10)))


async def schedule_reminders(app: Application):
    """Пересчитать расписание напоминаний для всех будущих эфиров."""
    jq = app.job_queue
    for j in jq.jobs():
        if j.name and j.name.startswith("rem:"):
            j.schedule_removal()
    now = datetime.now(timezone.utc)
    n = 0
    for w in await db.upcoming_webinars(limit=100):
        overdue = None
        for kind, delta in REM_KINDS:
            if w[kind]:
                continue
            when = w["starts_at"] - delta
            if when > now:
                jq.run_once(reminder_job, when=when, data=(w["id"], kind), name=f"rem:{w['id']}:{kind}")
                n += 1
            else:
                overdue = kind  # пропущенное (бот был выключен): шлём только самое свежее
        if overdue:
            jq.run_once(reminder_job, when=2, data=(w["id"], overdue), name=f"rem:{w['id']}:{overdue}")
            n += 1
    log.info("Напоминаний в расписании: %s", n)


async def reminder_job(ctx: ContextTypes.DEFAULT_TYPE):
    wid, kind = ctx.job.data
    async with REM_LOCK:
        w = await db.get_webinar(wid)
        if not w or w[kind] or w["starts_at"] <= datetime.now(timezone.utc):
            return
        await db.mark_sent(wid, kind)  # сначала отмечаем, чтобы не отправить дважды
    t = fmt_times(w["starts_at"])
    if kind == "sent_24h":
        text = T.REMIND_24H.format(title=w["title"], **t)
    elif kind == "sent_1h":
        text = T.REMIND_1H.format(title=w["title"], **t)
    else:
        text = (T.REMIND_10M_LINK.format(link=w["link"]) if w["link"]
                else T.REMIND_10M_NOLINK.format(channel=CHANNEL_URL))
    uids = await db.registrants(wid)
    failed = []
    ok = await mass_send(ctx.bot, uids, lambda uid: ctx.bot.send_message(uid, text), failed)
    log.info("Напоминание %s по эфиру %s: %s из %s", kind, wid, ok, len(uids))
    report = f"Напоминание ({kind}) по эфиру №{wid} отправлено: {ok} из {len(uids)}."
    if failed:
        names = [name_of(r) for r in await db.users_by_ids(failed)]
        report += "\nНе дошло: " + ", ".join(names) + ". Лучше отправить им ссылку лично."
    for a in ADMIN_IDS:
        try:
            await ctx.bot.send_message(a, report)
        except Exception:
            pass


async def sync_job(ctx: ContextTypes.DEFAULT_TYPE):
    """Раз в 6 часов: сверить расписание с базой и проверить отзывы (на случай перезапуска бота)."""
    await schedule_reminders(ctx.application)
    await review_reminder_job(ctx)


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.exception("Ошибка: %s", ctx.error)


# ---------- запуск ----------
async def post_init(app: Application):
    await db.connect()
    from telegram import BotCommand
    await app.bot.set_my_commands([
        BotCommand("start", "Главное меню"),
        BotCommand("status", "Мои подписки и записи на эфиры"),
        BotCommand("privacy", "Какие данные хранит бот"),
        BotCommand("stop", "Не получать сообщения"),
        BotCommand("delete_me", "Удалить мои данные"),
    ])
    await schedule_reminders(app)
    day.setup(db, pay_url, notify_admins)
    await day.schedule_all(app)
    app.job_queue.run_repeating(prices_job, interval=600, first=60, name="prices")
    log.info("Бот запущен. Администраторы: %s", ADMIN_IDS or "не заданы!")


async def post_shutdown(app: Application):
    await db.close()


# ---------- свободные сообщения ----------
async def on_private_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Бот не принимает вопросы: подсказываем написать в директ канала."""
    if await day.on_text(update, ctx):
        return
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
                     ("myid", myid), ("status", status_cmd), ("admin", admin_help), ("stats", stats), ("webinars", webinars),
                     ("webinar_add", webinar_add), ("webinar_link", webinar_link),
                     ("webinar_del", webinar_del), ("broadcast", broadcast), ("broadcast_unreg", broadcast_unreg),
                     ("send_registered", send_registered), ("regs", regs_cmd), ("prices", prices_cmd),
                     ("reviews", reviews_cmd), ("consults", consults_cmd),
                     ("den_access", lambda u, c: day.admin_access(u, c, is_admin)),
                     ("den_stats", lambda u, c: day.admin_stats(u, c, is_admin))]:
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
    app.add_handler(CallbackQueryHandler(day.on_cb, pattern="^den:"))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_private_message))
    app.add_error_handler(on_error)
    app.job_queue.run_repeating(sync_job, interval=6 * 3600, first=6 * 3600)
    return app


if __name__ == "__main__":
    if not BOT_TOKEN or not DATABASE_URL:
        raise SystemExit("Не заданы BOT_TOKEN и/или DATABASE_URL")
    build_app().run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
