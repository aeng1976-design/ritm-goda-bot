# -*- coding: utf-8 -*-
"""Статистика для администраторов: /report, /log, /user, /export."""
import io
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")

PAY = {"plan": "план", "konsult": "план + консультация", "soprov": "план + сопровождение",
       "kons": "консультация без плана",
       "den_m": "«Мой день» · месяц", "den_msub": "«Мой день» · месяц с автопродлением",
       "den_3m": "«Мой день» · 3 месяца", "den_3mp": "«Мой день» · 3 месяца + PDF",
       "den_6m": "«Мой день» · полгода", "den_6mp": "«Мой день» · полгода + PDF",
       "den_y": "«Мой день» · год", "den_yp": "«Мой день» · год + PDF", "den_pdf": "только PDF"}

LABELS = {
    "news_yes": "✅ Согласие на рассылку", "news_no": "Отказ от рассылки",
    "stop": "🚫 Отписка или блокировка бота", "attended": "Отметка «была на эфире»",
    "review": "⭐ Отзыв об эфире", "guide": "🎁 Гайд забран", "kit_view": "👀 Просмотр тарифов плана",
    "den_open": "🌿 Открыт «Мой день»", "trial_end": "Конец пробной недели",
    "sub_cancel": "❌ Отмена автопродления", "sub_resume": "🔄 Автопродление снова включено",
    "sub_end": "Подписка закончилась", "den_pause": "⏸ «Мой день» на паузе",
    "den_unpause": "▶️ «Мой день» снова включён", "den_reset": "«Мой день» сброшен (админ)",
}


def fmt_at(at):
    return at.astimezone(BERLIN).strftime("%d.%m %H:%M")


def label(kind, detail, webs):
    if kind == "new":
        return "🆕 Первый вход в бот" + (f" (ссылка: {detail})" if detail else "")
    if kind == "start":
        if detail in PAY:
            return f"💳 Оплата: {PAY[detail]}"
        if detail == "den":
            return "Переход по ссылке «Мой день»"
        return f"Переход по ссылке «{detail}»"
    if kind in ("reg", "unreg"):
        w = webs.get(str(detail), f"№{detail}")
        return ("📅 Запись на эфир " if kind == "reg" else "Отмена записи на эфир ") + w
    if kind == "den_trial":
        return f"🌿 Регистрация в «Мой день», проба до {detail}"
    if kind == "den_paid":
        key, _, rest = (detail or "").partition(" ")
        name = PAY.get(key) or ("«Мой день» на год к плану" if key.startswith("plan_") else key)
        return f"🌿 Доступ открыт: {name} {rest}"
    if kind == "den_access":
        return f"🌿 Доступ выдан вручную {detail}"
    if kind == "sub_renew":
        return f"🔄 Автопродление: списание, доступ {detail}"
    if kind == "feedback":
        return f"⭐ Отзыв о пробной неделе, порекомендует: {detail}"
    if kind == "consult":
        prod, _, ch = (detail or "").partition(": ")
        ch = {"alex": "Александра", "luda": "Людмила", "any": "любая из вас"}.get(ch, ch)
        return f"🗓 Выбор консультанта: {ch} ({PAY.get(prod, prod)})"
    return LABELS.get(kind, kind) + (f" ({detail})" if detail else "")


async def _webs(db):
    rows = await db.webinars()
    return {str(w["id"]): w["starts_at"].astimezone(BERLIN).strftime("%d.%m") for w in rows}


async def _split_send(msg, text):
    while text:
        part = text[:3900]
        if len(text) > 3900:
            cut = part.rfind("\n")
            part = text[:cut if cut > 0 else 3900]
        await msg.reply_text(part)
        text = text[len(part):].lstrip("\n")


async def report_cmd(update, ctx, db, is_admin):
    """/report — за 7 дней, /report 30 — за 30 дней, /report all — за всё время."""
    if not is_admin(update):
        return
    arg = ctx.args[0] if ctx.args else "7"
    now = datetime.now(timezone.utc)
    if arg == "all":
        since, title = datetime(2000, 1, 1, tzinfo=timezone.utc), "за всё время"
    else:
        try:
            days = max(1, int(arg))
        except ValueError:
            days = 7
        since = now - timedelta(days=days)
        title = "за сегодня" if days == 1 else f"за {days} дней"
    c = await db.counts_since(since)
    ev = {}
    for r in await db.events_period(since):
        ev.setdefault(r["kind"], {})[r["detail"]] = r["n"]
    n = lambda k: sum(ev.get(k, {}).values())
    pays = {k: v for k, v in ev.get("start", {}).items() if k in PAY}
    fb = ev.get("feedback", {})
    es = await db.get_setting("events_since")
    lines = [f"📊 Статистика {title} (с {since.astimezone(BERLIN):%d.%m.%Y})", "",
             f"👥 Новые в боте: {c['new_users']}"]
    src = {}
    for k, v in ev.get("new", {}).items():
        src[k or "без ссылки"] = v
    if src:
        lines.append("   откуда: " + ", ".join(f"{k} {v}" for k, v in sorted(src.items(), key=lambda x: -x[1])))
    lines += [f"📩 Рассылка: согласие {n('news_yes')} · отказ {n('news_no')} · отписка/блокировка {n('stop')}",
              f"🎁 Гайд забрали: {n('guide')}",
              f"📅 Записи на эфир: {c['regs']} · отмены {n('unreg')} · отметили «была на эфире» {c['attended']} · отзывы {c['reviews']}",
              f"👀 Смотрели тарифы плана: {n('kit_view')}", "",
              f"🌿 «Мой день»: регистраций {c['den_users']} · закончили пробу {n('trial_end')} · на паузе {n('den_pause')}"]
    if fb:
        lines.append("   отзывы о пробе: " + ", ".join(f"{k.lower()} {v}" for k, v in fb.items()))
    lines.append("")
    if pays:
        lines.append(f"💳 Оплаты: {sum(pays.values())}")
        lines += [f"   {PAY[k]}: {v}" for k, v in sorted(pays.items(), key=lambda x: -x[1])]
    else:
        lines.append("💳 Оплат за этот период нет")
    lines.append(f"🔄 Автопродление: списаний {n('sub_renew')} · отмен {n('sub_cancel')}")
    lines.append(f"🗓 Выбрали консультанта: {n('consult')}")
    if es:
        lines += ["", f"Оплаты, отписки, гайд и просмотры тарифов считаются с {datetime.fromisoformat(es).astimezone(BERLIN):%d.%m.%Y %H:%M}, "
                      "когда включили журнал."]
    lines += ["", "История человека: /user ID · последние события: /log · таблица Excel: /export"]
    await _split_send(update.message, "\n".join(lines))


async def log_cmd(update, ctx, db, is_admin):
    """/log — последние 30 событий, /log 100 — последние 100."""
    if not is_admin(update):
        return
    try:
        k = min(200, max(1, int(ctx.args[0]))) if ctx.args else 30
    except ValueError:
        k = 30
    webs = await _webs(db)
    rows = await db.last_events(k)
    if not rows:
        return await update.message.reply_text("Событий пока нет.")
    lines = [f"🕒 Последние {len(rows)} событий (новые сверху):", ""]
    for r in rows:
        who = r["first_name"] or "—"
        if r["username"]:
            who += f" @{r['username']}"
        lines.append(f"{fmt_at(r['at'])} · {who} (ID {r['user_id']}) · {label(r['kind'], r['detail'], webs)}")
    await _split_send(update.message, "\n".join(lines))


async def user_cmd(update, ctx, db, is_admin):
    """/user 123456789 или /user @username — вся история человека."""
    if not is_admin(update):
        return
    if not ctx.args:
        return await update.message.reply_text("Формат: /user 123456789 или /user @username")
    a = ctx.args[0].lstrip("@")
    if a.isdigit():
        u = await db.get_user(int(a))
    else:
        u = await db.pool.fetchrow("SELECT * FROM users WHERE lower(username)=lower($1)", a)
    if not u:
        return await update.message.reply_text("Такого человека в боте нет.")
    uid = u["id"]
    du = await db.day_user(uid)
    webs = await _webs(db)
    es = await db.get_setting("events_since")
    since = datetime.fromisoformat(es) if es else None
    items = [(r["at"], r["kind"], r["detail"]) for r in await db.user_events(uid)]
    for r in await db.user_history(uid):
        if since is None or r["at"] < since:
            items.append((r["at"], r["kind"], r["detail"]))
    if not any(k == "new" for _, k, _ in items) and (since is None or u["created_at"] < since):
        items.append((u["created_at"], "new", u["source"]))
    items.sort(key=lambda x: x[0])
    news = {True: "да", False: "нет", None: "не отвечала"}[u["news_consent"]]
    head = [f"👤 {u['first_name'] or '—'}" + (f" @{u['username']}" if u["username"] else "") + f" · ID {uid}",
            f"Первый вход: {fmt_at(u['created_at'])} · последняя активность: {fmt_at(u['last_seen'])}",
            f"Пришла по ссылке: {u['source'] or 'без ссылки'} · рассылка: {news}" + (" · 🚫 отписалась или заблокировала" if u["stopped"] else "")]
    if du and du["bd"]:
        st = (f"оплачено до {du['paid_until']:%d.%m.%Y}" if du["paid_until"] else
              f"проба до {du['trial_until']:%d.%m.%Y}" if du["trial_until"] else "без доступа")
        head.append(f"🌿 «Мой день»: {du['name']}, {st}" + (" · автопродление" if du["sub_id"] and not du["sub_cancel"] else ""))
    lines = head + ["", "История:"] + [f"{fmt_at(at)} · {label(k, d, webs)}" for at, k, d in items]
    await _split_send(update.message, "\n".join(lines))


async def export_cmd(update, ctx, db, is_admin):
    """/export — таблица Excel: все люди и все события."""
    if not is_admin(update):
        return
    from openpyxl import Workbook
    from openpyxl.styles import Font
    webs = await _webs(db)
    wb = Workbook()
    ws = wb.active
    ws.title = "Люди"
    cols = [("ID", "id"), ("Имя", "first_name"), ("Username", "username"), ("Ссылка", "source"),
            ("Первый вход", "created_at"), ("Последняя активность", "last_seen"), ("Рассылка", "news_consent"),
            ("Отписка/блокировка", "stopped"), ("Записи на эфиры", "webinars"), ("Была на эфире", "was_live"),
            ("Отзывов об эфире", "n_reviews"), ("Мой день: имя", "den_name"), ("Мой день: с", "den_since"),
            ("Проба до", "trial_until"), ("Оплачено до", "paid_until"), ("Тариф", "plan_key"),
            ("Нужен PDF", "plan_pdf"), ("Автопродление", "sub_id"), ("Автопродление отменено", "sub_cancel"),
            ("Сообщения включены", "den_active"), ("Консультация", "consult_product"), ("Консультант", "consult_choice"),
            ("Понравилось", "liked"), ("Неудобно", "inconvenient"), ("Порекомендует", "recommend")]
    ws.append([c for c, _ in cols])
    for cell in ws[1]:
        cell.font = Font(bold=True)

    def val(k, v):
        if v is None:
            return ""
        if isinstance(v, bool):
            return "да" if v else "нет"
        if isinstance(v, datetime):
            return v.astimezone(BERLIN).strftime("%d.%m.%Y %H:%M")
        if k == "plan_key":
            return PAY.get(v) or ("«Мой день» к плану" if str(v).startswith("plan_") else v)
        if k == "sub_id":
            return "да"
        return v if not hasattr(v, "strftime") else v.strftime("%d.%m.%Y")
    for r in await db.export_users():
        ws.append([val(k, r[k]) for _, k in cols])
    ws2 = wb.create_sheet("События")
    ws2.append(["Когда", "ID", "Имя", "Username", "Событие"])
    for cell in ws2[1]:
        cell.font = Font(bold=True)
    for r in await db.export_events():
        ws2.append([r["at"].astimezone(BERLIN).strftime("%d.%m.%Y %H:%M"), r["user_id"], r["first_name"] or "",
                    r["username"] or "", label(r["kind"], r["detail"], webs)])
    for sh in (ws, ws2):
        for col in sh.columns:
            w = max(len(str(c.value or "")) for c in col)
            sh.column_dimensions[col[0].column_letter].width = min(45, max(10, w + 2))
        sh.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    name = f"Статистика бота {datetime.now(BERLIN):%d.%m.%Y}.xlsx"
    await update.message.reply_document(buf, filename=name, caption="📊 Лист «Люди»: все пользователи. Лист «События»: всё по времени.")
