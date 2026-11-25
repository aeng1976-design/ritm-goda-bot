# -*- coding: utf-8 -*-
"""«Мой день»: пробная неделя, утро (энергия дня, аффирмация, дела), благодарности,
вечер (отметки и 3 дела на завтра), обзор и итог недели, план периода с даты покупки,
отзыв после пробной недели, разовая покупка на месяц, 3 месяца, полгода или год (с PDF или без)."""
import calendar
import logging
import os
import re
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, Update
from telegram.ext import ContextTypes

import day_calc as C
import day_data as DD

log = logging.getLogger("ritm.day")
BERLIN = ZoneInfo("Europe/Berlin")
TRIAL_DAYS = 7
BOT_LINK = "t.me/ritm_goda_bot?start=den"

# ---------- тарифы ----------
# ключ → (название, месяцев доступа, PDF, переменная ссылки Stripe, цена по умолчанию)
# месяцев = 0: только PDF, без бота
DEN_OPTIONS = [
    ("den_m", "Месяц", 1, False, "PAY_URL_DEN_M", "7,90"),
    ("den_3m", "3 месяца", 3, False, "PAY_URL_DEN_3M", "19,90"),
    ("den_3mp", "3 месяца + PDF", 3, True, "PAY_URL_DEN_3MP", "29,90"),
    ("den_6m", "Полгода", 6, False, "PAY_URL_DEN_6M", "34,90"),
    ("den_6mp", "Полгода + PDF", 6, True, "PAY_URL_DEN_6MP", "49,90"),
    ("den_y", "Год", 12, False, "PAY_URL_DEN_Y", "59"),
    ("den_yp", "Год + PDF", 12, True, "PAY_URL_DEN_YP", "89"),
    ("den_pdf", "Только личный план PDF на год, без бота", 0, True, "PAY_URL_DEN_PDF", "69"),
]
OPT = {k: dict(label=lab, months=mo, pdf=pdf, url=os.environ.get(u, ""),
               price=os.environ.get("PRICE_" + k.upper(), dflt)) for k, lab, mo, pdf, u, dflt in DEN_OPTIONS}
# Месяц с автоматическим продлением (подписка Stripe)
OPT["den_msub"] = dict(label="Месяц с автоматическим продлением", months=1, pdf=False,
                       url=os.environ.get("PAY_URL_DEN_MSUB", ""), price=os.environ.get("PRICE_DEN_MSUB", "7,90"))
STRIPE_KEY = os.environ.get("STRIPE_KEY", "")
SUB_ON = bool(OPT["den_msub"]["url"] and STRIPE_KEY)
DEN_PAID = set(OPT)
# срок → (без PDF, с PDF)
TERMS = [("m", "Месяц", "den_m", None), ("3m", "3 месяца", "den_3m", "den_3mp"),
         ("6m", "Полгода", "den_6m", "den_6mp"), ("y", "Год", "den_y", "den_yp")]
PDF_PAGES = {3: "около 40 страниц", 6: "около 80 страниц", 12: "около 155 страниц"}

# Простые названия задач (вместо названий Таро)
PLAIN = {1: "Я могу", 2: "Внутренний голос", 3: "Изобилие", 4: "Порядок", 5: "Знание", 6: "Выбор сердцем",
         7: "В путь", 8: "Честность", 9: "Тишина", 10: "Перемены", 11: "Сила", 12: "Пауза", 13: "Обновление",
         14: "Мера", 15: "Соблазн", 16: "Перестройка", 17: "Мечта", 18: "Интуиция", 19: "Радость",
         20: "Пробуждение", 21: "Целостность", 22: "Лёгкость"}
MORNING_TIMES = ["06:00", "06:30", "07:00", "07:30", "08:00", "09:00"]
EVENING_TIMES = ["19:00", "20:00", "21:00", "22:00"]
MN = ["", "январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
      "октябрь", "ноябрь", "декабрь"]
MNg = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
       "октября", "ноября", "декабря"]
MNp = ["", "январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре",
       "октябре", "ноябре", "декабре"]
WDs = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WDl = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

# Дни обнуления и благоприятные общие дни
ZERO = (10, 20, 30)
ZTXT = ("🔴 <b>Сегодня день обнуления</b> (10, 20 и 30 числа): ничего важного не планируйте. "
        "Хорошо завершать старое, наводить порядок и отдыхать.")
GOOD_USE = ("• подписание договоров и оформление документов\n• регистрацию брака\n"
            "• крупные покупки: машина, недвижимость\n• поездки: бронирование, оплату и сам выезд\n"
            "• операции и большие обследования здоровья")

# Личный день: на что быть осторожнее
PDC = {1: "не браться сразу за много дел и не давить на других своим решением",
       2: "не принимать всё близко к сердцу и не решать за других",
       3: "не распыляться на всё сразу",
       4: "не уходить в критику себя и других, не перерабатывать",
       5: "не обещать лишнего и не болтать о том, что ещё не готово",
       6: "не идти на поводу у соблазнов: еда, траты, лень",
       7: "без дисциплины день может пойти в кризис; важное не начинать, не спорить",
       8: "не давить и не контролировать всех; с деньгами без риска",
       9: "не начинать новое и не цепляться за то, что уходит"}
# Даты по делам (личный день)
CATS = [(1, "Начинать новое, ставить цели"), (2, "Важные встречи и разговоры по душам"),
        (3, "Учёба, новые знания, движение"), (4, "Работа, порядок, спорт"),
        (5, "Выходить к людям, рассказывать о себе"), (6, "Отдых, семья, удовольствие"),
        (7, "Тишина, практики, ничего важного не начинать"), (8, "Деньги, договоры, большие результаты"),
        (9, "Завершать дела, подводить итоги")]
# Луна и Солнце: что делать (по разнице Солнце − Луна)
MSDO = {0: "сохранять равновесие, пересмотреть планы, без резких шагов",
        1: "пробовать новое понемногу, принимать подарки судьбы, укреплять веру в себя",
        2: "просить о помощи коллег и друзей, браться за денежные дела",
        3: "начинать новое, творить, решать затянувшиеся вопросы",
        4: "знакомиться, заключать договоры, сходить на медосмотр",
        5: "учиться, путешествовать, проявлять себя в творчестве, время для любви",
        6: "время для себя и романтики, хорошо для денежных дел",
        7: "действовать смело во всех сферах, удача на вашей стороне",
        8: "сделки, рост дохода, расширение дела",
        9: "принимать важные решения о здоровье и карьере",
        10: "переезды, учёба, поездки, выход к большой аудитории",
        11: "быть открытой к важным семейным и рабочим событиям",
        12: "просить повышение, поднимать цены, расти в статусе",
        13: "крупные покупки, недвижимость, финансовые перемены",
        14: "действовать во всех сферах сразу"}
MSNO = {1: "без конфликтов, осторожно с публичными выступлениями, меньше обсуждать других",
        2: "беречь силы, больше отдыха, осторожно с травмами",
        3: "решать головой, а не импульсом, важное перепроверять",
        4: "без рисков: режим, здоровье, опора на близких",
        5: "не покупать дорогое, обходить конфликты дома и в отношениях",
        6: "не спорить о деньгах, слушать своё тело, больше отдыха",
        7: "без резких решений в семье, больше тепла и разговоров",
        8: "контролировать расходы, без крупных покупок и долгов",
        9: "внимательно выбирать, с кем делиться планами",
        10: "беречь здоровье и репутацию, без кредитов, переездов и рисков"}
LIKED = [("energy", "☀️ Энергия дня утром"), ("aff", "💛 Аффирмации"), ("grat", "🙏 Благодарности"),
         ("tasks", "📝 3 дела на день"), ("year", "📅 Обзор года")]
REC = {"yes": "Да, конечно", "maybe": "Возможно", "no": "Нет"}

db = None            # передаётся из bot.py
pay_url = None       # функция из bot.py: добавляет Telegram ID к ссылке Stripe
notify_admins = None


def setup(_db, _pay_url, _notify_admins):
    global db, pay_url, notify_admins
    db, pay_url, notify_admins = _db, _pay_url, _notify_admins


def today():
    return datetime.now(BERLIN).date()


# ---------- помощники ----------
def cap(t):
    return t[:1].upper() + t[1:]


def low(t):
    return t[:1].lower() + t[1:]


def first_sentence(t):
    return t.split(". ")[0].rstrip(".") + "."


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def is_paid(du, d=None):
    d = d or today()
    return bool(du) and bool(du["paid_until"]) and du["paid_until"] >= d and (not du["paid_from"] or du["paid_from"] <= d)


def has_access(du, d=None):
    d = d or today()
    return bool(du) and du["active"] and (
        (du["trial_until"] and du["trial_until"] >= d) or (du["paid_until"] and du["paid_until"] >= d))


def access_text(du):
    d = today()
    if du["paid_until"] and du["paid_until"] >= d:
        return f"Доступ оплачен до {du['paid_until']:%d.%m.%Y}."
    if du["trial_until"] and du["trial_until"] >= d:
        return f"Пробная неделя до {du['trial_until']:%d.%m.%Y}."
    return "Пробная неделя закончилась."


def gen_num(d):
    """Общее число дня: сумма всех цифр даты до одной цифры."""
    return C.red(C.ds(f"{d:%d%m%Y}"))


def is_good(d):
    return d.day not in ZERO and gen_num(d) in (3, 6)


def kind(df):
    return "сильный месяц ★" if df >= 3 else ("месяц беречь силы ●" if df <= -3 else "ровный месяц ○")


def mark(df):
    return "★" if df >= 3 else ("●" if df <= -3 else "○")


def ms_advice(df):
    return MSDO[min(df, 14)] if df >= 0 else MSNO[min(-df, 10)]


def ms_rec(df, su=None, mo=None):
    extra = []
    if su in DD.SUN:
        extra.append(low(DD.SUN[su].split(": ", 1)[1]).replace(" – ", ", "))
    if mo in DD.MOON:
        extra.append(low(DD.MOON[mo].split(": ", 1)[1]).replace(" – ", ", "))
    return cap(ms_advice(df)) + "." + (" Также: " + "; ".join(extra) + "." if extra else "")


def energy_rec(e0, e1):
    t, a, b = DD.EG[e0]
    dr = ("Силы к следующему дню рождения идут на спад: не берите лишних обязательств, заранее планируйте отдых." if e1 < e0 else
          ("Силы к следующему дню рождения растут: хорошее время брать новые задачи и расширяться." if e1 > e0 else
           "Силы ровные: держите свой темп."))
    return f"<b>{t}.</b> {a}\n{dr}\n<i>Что делать:</i> {low(b)}"


def month_info(bd, d):
    """Данные календарного месяца для даты d."""
    pm = C.personal_month(bd, d)
    ma = C.r22(C.year_task(bd, d.year) + d.month)
    _, (mo, su, df) = C.moon_sun(bd, d)
    return dict(pm=pm, ma=ma, mo=mo, su=su, df=df)


def day_energy(bd, d):
    return C.day_task(bd, d), C.personal_day(bd, d)


def tomorrow_hint(bd, d):
    if d.day in ZERO:
        return f"Завтра, {d.day}-е, <b>день обнуления</b> 🔴: ничего важного не планируйте, лучше лёгкие дела и отдых."
    n, p = day_energy(bd, d)
    t = f"Завтра день <b>«{PLAIN[n]}»</b>: {DD.PD[p][0]}."
    if is_good(d):
        t += "\n🖋 Удачный день для договоров, документов, крупных покупок, поездок и планирования операций."
    return t


def ddmm(d):
    return f"{d:%d.%m}"


# ---------- тексты года и периода ----------
def year_text(du, d=None):
    d = d or today()
    bd = du["bd"]
    py = C.personal_year(bd, d.year)
    lb = C.last_birthday(bd, d)
    yt = C.year_task(bd, lb.year)
    (mo, su, df), (mo2, su2, df2) = C.moon_sun(bd, d)
    age = C.age_on(bd, d)
    e0, e1 = C.energy(bd, age), C.energy(bd, age + 1)
    vt, va, vb = DD.VED[py]
    t, ta, tb = DD.YARC[yt]
    return (
        f"📅 <b>Ваш {d.year} год, {du['name']}</b>\n\n"
        f"<b>1. Личный год {py} · {vt}</b>\n{va}\n<i>Важно:</i> {vb}\n\n"
        f"<b>2. Задача года · «{PLAIN[yt]}»</b> (с {lb:%d.%m.%Y})\n{ta}\n<i>Важно:</i> {tb}\n\n"
        f"<b>3. Луна и Солнце</b>\nВ этом году жизни: {low(ms_rec(df))}\n"
        f"В {MNp[d.month]}, {kind(df2)}: {low(ms_rec(df2, su2, mo2))}\n\n"
        f"<b>4. Ваша энергия</b>\n{energy_rec(e0, e1)}\n\n"
        "Каждое утро я буду присылать энергию дня."
    )


def segments(start, end):
    """Разбивает период на части по календарным месяцам: [(начало, конец), …]."""
    out, s = [], start
    while s <= end:
        last = date(s.year, s.month, calendar.monthrange(s.year, s.month)[1])
        e = min(last, end)
        out.append((s, e))
        s = e + timedelta(days=1)
    return out


def seg_title(s, e):
    if s.month == e.month and s.day == 1 and e.day == calendar.monthrange(e.year, e.month)[1]:
        return f"{cap(MN[s.month])} {s.year}"
    if s.month == e.month:
        return f"{s.day}–{e.day} {MNg[s.month]}"
    return f"{s:%d.%m}–{e:%d.%m}"


def period_texts(du, start):
    """План периода с даты start на месяц вперёд. Возвращает список сообщений."""
    bd, name = du["bd"], du["name"]
    end = add_months(start, 1) - timedelta(days=1)
    segs = segments(start, end)
    one = len(segs) == 1
    head = (f"🗓 <b>Ваш {MN[start.month]} {start.year}, {name}</b>" if one and start.day == 1 else
            f"🗓 <b>Ваш месяц с {start.day} {MNg[start.month]} по {end.day} {MNg[end.month]}, {name}</b>")
    msgs = []
    first_pm = None
    for i, (s, e) in enumerate(segs, 1):
        x = month_info(bd, s)
        first_pm = first_pm or x["pm"]
        pt, ptx = DD.PM[x["pm"]]
        title = DD.ME[x["pm"]][0]
        ya = DD.YARC[x["ma"]]
        part = (head + f"\n<b>{title}</b>\n\n") if one else (
            (head + "\n\n" if i == 1 else "") + f"<b>Часть {i} · {seg_title(s, e)}: {low(title)}</b>\n\n")
        if s.month == 1 and s.day == 1:
            py = C.personal_year(bd, s.year)
            part += f"🎆 С 1 января начинается ваш <b>личный год {py} · {DD.VED[py][0]}</b>.\n{DD.VED[py][1]}\n\n"
        part += (f"<b>Личный месяц {x['pm']} · {pt}</b>\n{ptx}\n\n"
                 f"<b>Задача месяца · «{PLAIN[x['ma']]}»</b>\n{first_sentence(ya[1])}\n<i>Важно:</i> {cap(ya[2].split(';')[0])}.\n\n"
                 f"<b>Луна и Солнце: {kind(x['df'])}</b>\n{ms_rec(x['df'], x['su'], x['mo'])}")
        msgs.append(part)
    # энергия на период
    age = C.age_on(bd, start)
    e0, e1 = C.energy(bd, age), C.energy(bd, age + 1)
    en = f"⚡ <b>Ваша энергия на этот период</b>\n{energy_rec(e0, e1)}"
    bday = C.last_birthday(bd, end)
    if start < bday <= end:
        e2 = C.energy(bd, age + 2)
        en += (f"\n\n🎂 С {bday:%d.%m}, вашего дня рождения, начинается новый год жизни: задача года "
               f"«{PLAIN[C.year_task(bd, bday.year)]}», энергия: {low(DD.EG[e1][0])}"
               + (", силы растут." if e2 > e1 else (", силы идут на спад." if e2 < e1 else ".")))
    t, goals, man = DD.ME[first_pm]
    en += "\n\n🎯 <b>Фокус месяца</b>\n" + "\n".join("▫️ " + g for g in goals)
    en += f"\n\n💛 <b>Манифест месяца</b>\n<i>«{man}»</i>"
    msgs.append(en)
    # даты
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    fmt = (lambda d: str(d.day)) if one else ddmm
    good = [fmt(d) for d in days if is_good(d)]
    groups = {k: [] for k, _ in CATS}
    for d in days:
        if d.day not in ZERO:
            groups[C.personal_day(bd, d)].append(fmt(d))
    zero = [fmt(d) for d in days if d.day in ZERO]
    dt = (f"📅 <b>Даты {MNg[start.month] if one else 'периода'}</b>\n\n"
          f"🖋 <b>Благоприятные общие дни:</b> {', '.join(good) or 'в этом периоде нет'}\n"
          f"Учитывайте их, когда планируете:\n{GOOD_USE}\n\n"
          "👤 <b>Ваши личные дни по делам:</b>\n"
          + "\n".join(f"• <b>{lab}:</b> {', '.join(groups[k])}" for k, lab in CATS if groups[k])
          + (f"\n🔴 <b>Дни обнуления, ничего важного не планировать:</b> {', '.join(zero)}" if zero else ""))
    msgs.append(dt)
    return msgs


def overview_text(du, start, end):
    """Обзор всех месяцев срока (для 3 месяцев, полугода, года)."""
    bd = du["bd"]
    rows, strong, care = [], [], []
    for s, e in segments(start, end):
        x = month_info(bd, s)
        nm = cap(MN[s.month]) + (f" {s.year}" if s.month == 1 or (s, e) == segments(start, end)[0] else "")
        part = "" if (s.day == 1 and e.day == calendar.monthrange(e.year, e.month)[1]) else f" ({s.day}–{e.day})"
        rows.append(f"{mark(x['df'])} <b>{nm}</b>{part}: {low(DD.PM[x['pm']][0])}. <i>Совет:</i> {ms_advice(x['df'])}.")
        (strong if x["df"] >= 3 else care if x["df"] <= -3 else []).append(MN[s.month])
    j = lambda l: ", ".join(dict.fromkeys(l)) if l else "нет"
    return (f"🗺 <b>Ваши месяцы с {start:%d.%m.%Y} по {end:%d.%m.%Y}, {du['name']}</b>\n\n" + "\n\n".join(rows) +
            "\n\n★ сильный месяц ○ ровный ● беречь силы\n\n"
            f"<b>Самые сильные:</b> {j(strong)}. Здесь начинать важное, вести переговоры, запускать.\n"
            f"<b>Беречь силы:</b> {j(care)}. Меньше рисков, больше заботы о здоровье и отдыха; важное лучше перенести на сильные месяцы.\n\n"
            f"План на каждый следующий месяц приходит {start.day}-го числа.")


def period_start(du, d=None):
    """Начало текущего оплаченного месяца (от даты покупки)."""
    d = d or today()
    if not is_paid(du, d) or not du["paid_from"]:
        return None
    k = 0
    while add_months(du["paid_from"], k + 1) <= d:
        k += 1
    return add_months(du["paid_from"], k)


def week_text(du, mon):
    lines = []
    for i in range(7):
        d = mon + timedelta(days=i)
        if d.day in ZERO:
            lines.append(f"<b>{WDs[i]} {d.day}</b> · 🔴 день обнуления, ничего важного")
            continue
        n, p = day_energy(du["bd"], d)
        lines.append(f"<b>{WDs[i]} {d.day}</b> «{PLAIN[n]}»: {low(dict(CATS)[p])}" + (" 🖋" if is_good(d) else ""))
    end = mon + timedelta(days=6)
    rng = f"{mon.day}–{end.day} {MNg[end.month]}" if mon.month == end.month else f"{mon.day} {MNg[mon.month]} – {end.day} {MNg[end.month]}"
    return (f"📆 <b>Ваша неделя {rng}</b>\nДля чего подходит каждый день:\n\n" + "\n".join(lines) +
            "\n\n🖋 удачный день для договоров, документов, крупных покупок, поездок и планирования операций\n\n"
            "Планируйте важное на подходящий день, тогда оно пойдёт легче.")


async def tasks_text(uid, d):
    rows = await db.day_tasks(uid, d)
    if not rows:
        return None
    return "\n".join(("✅ " if r["done"] else "▫️ ") + r["text"] for r in rows)


async def goals_text(uid, period):
    rows = await db.day_goals(uid, period)
    return "\n".join(("✅ " if r["done"] else "▫️ ") + r["text"] for r in rows) if rows else None


# ---------- оплата ----------
def offer_text():
    return ("Продолжим вместе? 🌿\n\n"
            "Выберите срок. 3 месяца, полгода и год: <b>разовая покупка</b>, без автоматических списаний, перед окончанием я напомню."
            + (" Месяц можно оплатить разово или с <b>автоматическим продлением</b>." if SUB_ON else "") + "\n\n"
            "Каждый день: энергия дня, аффирмация, благодарности, 3 дела. Каждый месяц: подробный план с датами.\n"
            "Для 3 месяцев, полугода и года можно добавить личный план в PDF для печати.")


def offer_kb(uid):
    rows = []
    for t, lab, k, kp in TERMS:
        if OPT[k]["url"] or (kp and OPT[kp]["url"]):
            rows.append([Btn(f"{lab} · {OPT[k]['price']} €", callback_data=f"den:term:{t}")])
    if OPT["den_pdf"]["url"]:
        rows.append([Btn(f"📄 Только PDF, без бота · {OPT['den_pdf']['price']} €", url=pay_url(OPT["den_pdf"]["url"], uid))])
    return Kb(rows) if rows else None


def term_msg(t, uid):
    _, lab, k, kp = next(x for x in TERMS if x[0] == t)
    if not kp:
        rows = [[Btn(f"💳 Разово · {OPT[k]['price']} €", url=pay_url(OPT[k]["url"], uid))]]
        txt = f"<b>{lab}</b> · {OPT[k]['price']} €\n\n💳 <b>Разово</b>: платите один раз, без списаний. За 3 дня до конца я напомню."
        if SUB_ON:
            rows.append([Btn(f"🔄 Автоматически · {OPT['den_msub']['price']} € в месяц", callback_data="den:subinfo")])
            txt += "\n🔄 <b>Автоматически</b>: продлевается каждый месяц, за 3 дня до списания я предупрежу, отменить можно в любой момент."
        rows.append([Btn("◀️ Назад", callback_data="den:offer")])
        return (txt + "\n\nПосле оплаты вернитесь в бот, доступ откроется сразу.", Kb(rows))
    rows = []
    if OPT[k]["url"]:
        rows.append([Btn(f"Без PDF · {OPT[k]['price']} €", url=pay_url(OPT[k]["url"], uid))])
    if OPT[kp]["url"]:
        rows.append([Btn(f"С PDF · {OPT[kp]['price']} €", url=pay_url(OPT[kp]["url"], uid))])
    rows.append([Btn("◀️ Назад", callback_data="den:offer")])
    return (f"<b>{lab}</b>\n\nДобавить личный план в PDF для печати на этот срок? "
            f"{cap(PDF_PAGES[OPT[k]['months']])}: месяцы, недели, дни, аффирмации и дневник. "
            "Мы подготовим его и пришлём сюда в течение 3 рабочих дней.\n\n"
            "После оплаты вернитесь в бот, доступ откроется сразу.", Kb(rows))


SUB_INFO = ("🔄 <b>«Мой день» с автоматическим продлением</b>\n\n"
            "• {price} € в месяц\n"
            "• <b>продлевается автоматически</b> каждый месяц, пока вы его не отмените\n"
            "• за 3 дня до списания я напомню: когда и сколько спишется\n"
            "• отменить можно в любой момент: «Мой день» → «🔄 Моё продление». Оплаченный месяц остаётся до конца\n\n"
            "Нажимая «Оформить», вы соглашаетесь на ежемесячное списание.")


# ---------- Stripe: подписка на месяц ----------
async def stripe(method, path, data=None, params=None):
    import httpx
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.request(method, "https://api.stripe.com/v1/" + path, auth=(STRIPE_KEY, ""), data=data, params=params)
        r.raise_for_status()
        return r.json()


def _period_end(sub):
    pe = sub.get("current_period_end")
    if not pe:
        items = (sub.get("items") or {}).get("data") or []
        pe = items[0].get("current_period_end") if items else None
    return datetime.fromtimestamp(pe, BERLIN).date() if pe else None


async def find_sub(uid):
    """Ищет подписку человека по client_reference_id tg<ID> среди последних оплат."""
    res = await stripe("GET", "checkout/sessions", params={"limit": 100, "status": "complete"})
    for cs in res.get("data", []):
        if cs.get("client_reference_id") == f"tg{uid}" and cs.get("subscription"):
            return cs["subscription"]
    return None


async def stripe_job(ctx: ContextTypes.DEFAULT_TYPE):
    """Раз в час: продления, отмены, напоминание за 3 дня до списания."""
    if not STRIPE_KEY:
        return
    d = today()
    for du in await db.day_all_users():
        if du["plan_key"] != "den_msub" and not du["sub_id"]:
            continue
        uid = du["user_id"]
        try:
            sid = du["sub_id"] or await find_sub(uid)
            if not sid:
                continue
            if sid != du["sub_id"]:
                await db.day_set(uid, sub_id=sid)
            sub = await stripe("GET", f"subscriptions/{sid}")
        except Exception as e:
            log.warning("Stripe для %s: %s", uid, e)
            continue
        st, pe, canc = sub.get("status"), _period_end(sub), bool(sub.get("cancel_at_period_end"))
        if st in ("active", "trialing") and pe:
            if not du["paid_until"] or pe > du["paid_until"]:
                renewed = bool(du["paid_until"]) and du["paid_until"] < pe and du["sub_reminded"] is not None
                await db.day_set(uid, paid_until=pe, sub_cancel=canc, active=True)
                if renewed:
                    await notify_admins(ctx, f"🔄 «Мой день»: продление {du['name']} (ID {uid}), {OPT['den_msub']['price']} €, до {pe:%d.%m.%Y}")
                    await _send(ctx, uid, f"🔄 Подписка продлена до {pe:%d.%m.%Y}. Спасибо, что вы с нами 💛")
            if canc != du["sub_cancel"]:
                await db.day_set(uid, sub_cancel=canc)
            if not canc and pe - d == timedelta(days=3) and du["sub_reminded"] != pe:
                await db.day_set(uid, sub_reminded=pe)
                await _send(ctx, uid, f"🔄 Через 3 дня, <b>{pe:%d.%m.%Y}</b>, подписка «Мой день» продлится на месяц, "
                                      f"спишется <b>{OPT['den_msub']['price']} €</b>.\n\nЕсли всё в порядке, ничего делать не нужно.",
                            Kb([[Btn("👍 Хорошо", callback_data="den:subok")], [Btn("Отменить продление", callback_data="den:subcancel")]]))
        elif st in ("canceled", "unpaid", "incomplete_expired"):
            await db.day_set(uid, sub_id=None, sub_cancel=False, plan_key="den_m")
            await notify_admins(ctx, f"❌ «Мой день»: подписка {du['name']} (ID {uid}) закончилась ({st})")
        elif st == "past_due":
            await notify_admins(ctx, f"⚠️ «Мой день»: не прошло списание у {du['name']} (ID {uid}), Stripe попробует ещё раз")


def sub_status_text(du):
    if not du["sub_id"]:
        return "🔄 Автоматическое продление ещё подключается, обычно это до часа. Загляните чуть позже."
    if du["sub_cancel"]:
        return f"🔄 Продление отменено. «Мой день» работает до {du['paid_until']:%d.%m.%Y}, дальше списаний не будет."
    return f"🔄 Подписка активна: {OPT['den_msub']['price']} € в месяц. Следующее списание {du['paid_until']:%d.%m.%Y}."


INTRO = (
    "🌿 <b>Мой день</b>\n\n"
    "Каждый день я помогаю жить в своём ритме, всего несколько минут:\n"
    "• <b>утром</b> энергия вашего дня по дате рождения и аффирмация;\n"
    "• <b>3 благодарности</b> утром или вечером, как удобно;\n"
    "• <b>вечером</b> отметить, что получилось, и запланировать до 3 дел на завтра с учётом энергии дня.\n\n"
    "Сразу после регистрации покажу ваш год по четырём методам: личный год, задача года, Луна и Солнце, энергия.\n\n"
    f"<b>Первые {TRIAL_DAYS} дней в подарок.</b> Потом можно продолжить на месяц, 3 месяца, полгода или год.\n\n"
    "Для расчётов я сохраню ваше имя, дату рождения, выбранное время, ваши дела и цели. Удалить всё можно в любой момент командой /delete_me."
)


def menu_kb(du):
    rows = [[Btn("📅 Мой год", callback_data="den:year"), Btn("📝 Дела на сегодня", callback_data="den:today")]]
    if is_paid(du):
        rows.append([Btn("🗓 Мой месяц", callback_data="den:month"), Btn("📆 Эта неделя", callback_data="den:week")])
    rows.append([Btn("⏰ Время сообщений", callback_data="den:time"),
                 Btn("🙏 Благодарности: " + ("утром" if du["grat"] == "am" else "вечером"), callback_data="den:grat")])
    if du["sub_id"] or du["plan_key"] == "den_msub":
        rows.append([Btn("🔄 Моё продление", callback_data="den:sub")])
    else:
        rows.append([Btn("💳 Продлить доступ", callback_data="den:offer")])
    rows.append([Btn("⏸ Поставить сообщения на паузу" if du["active"] else "▶️ Включить сообщения снова", callback_data="den:pause")])
    return Kb(rows)


# ---------- расписание ----------
def schedule(app, du):
    for k in ("m", "e"):
        for j in app.job_queue.get_jobs_by_name(f"den{k}{du['user_id']}"):
            j.schedule_removal()
    if not du["active"] or not du["bd"]:
        return
    for k, t, fn in (("m", du["morning"], morning_job), ("e", du["evening"], evening_job)):
        h, m = map(int, t.split(":"))
        app.job_queue.run_daily(fn, time=dtime(h, m, tzinfo=BERLIN), name=f"den{k}{du['user_id']}", data=du["user_id"])


async def schedule_all(app):
    rows = await db.day_active_users()
    for du in rows:
        schedule(app, du)
    app.job_queue.run_daily(access_job, time=dtime(10, 0, tzinfo=BERLIN), name="den_access")
    if STRIPE_KEY:
        app.job_queue.run_repeating(stripe_job, interval=3600, first=120, name="den_stripe")
    log.info("«Мой день»: запланировано %d участниц", len(rows))


async def _send(ctx, uid, text, kb=None):
    try:
        await ctx.bot.send_message(uid, text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        return True
    except Exception as e:
        log.warning("«Мой день»: не отправлено %s: %s", uid, e)
        if "blocked" in str(e).lower() or "deactivated" in str(e).lower():
            await db.day_set(uid, active=False)
        return False


def morning_text(du, d, tasks):
    n, p = day_energy(du["bd"], d)
    aff = DD.A[n][d.toordinal() % 3]
    head = f"☀️ Доброе утро, {du['name']}! {cap(WDl[d.weekday()])}, {d.day} {MNg[d.month]}\n\n"
    zero = (ZTXT + "\n\n") if d.day in ZERO else ""
    good = ("🖋 <b>Сегодня благоприятный общий день</b> для договоров, документов, крупных покупок, "
            "поездок и планирования операций.\n\n") if is_good(d) else ""
    if True:  # пробная неделя и платный доступ: одинаково подробно
        x = month_info(du["bd"], d)
        txt = (head + zero + good +
               f"🔮 <b>Задача дня · «{PLAIN[n]}»</b>\n{first_sentence(DD.YARC[n][1])}\n"
               f"<i>Осторожно:</i> {low(DD.YARC[n][2].split(';')[0])}.\n\n"
               f"🔢 <b>Личный день {p} · {DD.PD[p][0].split(':')[0]}</b>\n{DD.PD[p][1]}\n"
               f"<i>Осторожно:</i> {PDC[p]}.\n\n"
               f"🌗 <b>{cap(MN[d.month])} для вас:</b> {kind(x['df'])}. {cap(ms_advice(x['df']))}.\n\n"
               f"💛 <i>«{aff}»</i>\n\n💡 {DD.TIP[n]}")
    else:
        txt = (head + zero + good +
               f"Сегодня день <b>«{PLAIN[n]}»</b>\n{first_sentence(DD.YARC[n][1])}\n"
               f"<b>Что делать:</b> {DD.PD[p][0]}.\n\n💛 <i>«{aff}»</i>\n\n💡 {DD.TIP[n]}")
    txt += (f"\n\n📝 <b>Ваши дела на сегодня:</b>\n{tasks}" if tasks else
            "\n\n📝 Дел на сегодня пока нет. Нажмите «📝 Дела на сегодня», и я спрошу по одному.")
    return txt


async def send_period_plan(ctx, uid, du, start):
    for m in period_texts(du, start):
        await _send(ctx, uid, m)
    await db.day_set(uid, last_plan=start)
    await _send(ctx, uid, "🎯 Поставьте себе до 3 целей на этот месяц. Я буду напоминать о них по понедельникам.",
                Kb([[Btn("✍️ Записать цели", callback_data=f"den:goals:{start:%Y%m%d}"),
                     Btn("Без целей", callback_data="den:nogoals")]]))


async def morning_job(ctx: ContextTypes.DEFAULT_TYPE):
    uid = ctx.job.data
    du = await db.day_user(uid)
    d = today()
    if not has_access(du, d):
        return
    kb = Kb([[Btn("📝 Дела на сегодня", callback_data="den:today")] +
             ([Btn("🗓 Мой месяц", callback_data="den:month")] if is_paid(du, d) else [])])
    await _send(ctx, uid, morning_text(du, d, await tasks_text(uid, d)), kb)
    # новый оплаченный месяц: план периода
    ps = period_start(du, d)
    if ps == d and du["last_plan"] != d:
        await send_period_plan(ctx, uid, du, d)
    # понедельник: обзор недели
    if d.weekday() == 0:
        txt = week_text(du, d)
        ps = period_start(du, d)
        g = await goals_text(uid, ps) if ps else None
        if g:
            txt += f"\n\n🎯 <b>Ваши цели месяца:</b>\n{g}"
        await _send(ctx, uid, txt)
    if du["grat"] == "am":
        await ask_gratitude(ctx, uid)


async def ask_gratitude(ctx, uid, then_plan=False):
    ctx.application.user_data[uid]["den_st"] = "grat_then_plan" if then_plan else "grat"
    await _send(ctx, uid, "🙏 За что вы благодарны сегодня? Напишите 3 благодарности, можно одним сообщением.",
                Kb([[Btn("Пропустить сегодня", callback_data="den:gskip")]]))


async def evening_job(ctx: ContextTypes.DEFAULT_TYPE):
    uid = ctx.job.data
    du = await db.day_user(uid)
    d = today()
    if not has_access(du, d):
        return
    if await db.day_tasks(uid, d):
        await _send(ctx, uid, "🌙 Как прошёл день? Отметьте, что получилось:", await done_kb(uid, d))
    if du["grat"] == "pm":
        await ask_gratitude(ctx, uid, then_plan=True)
    else:
        await after_grat(ctx, uid, du)


async def after_grat(ctx, uid, du):
    """После благодарностей: в воскресенье итог недели, иначе план на завтра."""
    if today().weekday() == 6:
        return await ask_week(ctx, uid)
    await ask_plan(ctx, uid, du)


async def ask_week(ctx, uid):
    ctx.application.user_data[uid]["den_st"] = "wk_good"
    await _send(ctx, uid, "🌿 <b>Итог недели</b>, 3 коротких вопроса.\n\n<b>1. Что получилось на этой неделе?</b>",
                Kb([[Btn("Пропустить итог", callback_data="den:wkskip")]]))


TASK_Q = {1: "1️⃣ Какое дело нужно сделать <b>в первую очередь</b>?",
          2: "2️⃣ Какое дело нужно сделать <b>в течение дня</b>?",
          3: "3️⃣ Какое дело можно сделать, <b>если хватит сил</b>?"}


async def ask_task(ctx, uid, d, q):
    """Задаёт вопрос q (1–3) про дела на день d. Пропускает уже записанные."""
    ud = ctx.application.user_data[uid]
    have = await db.day_tasks(uid, d)
    if q > 3 or len(have) >= 3:
        ud.pop("den_st", None)
        return await task_summary(ctx, uid, d)
    ud.update(den_st="task", den_td=d.isoformat(), den_q=q)
    when = "сегодня" if d == today() else "завтра"
    await _send(ctx, uid, TASK_Q[q] + f"\nНапишите одно дело на {when}.",
                Kb([[Btn("Пропустить", callback_data="den:tskip"), Btn("Готово", callback_data="den:tdone")]]))


async def task_summary(ctx, uid, d):
    t = await tasks_text(uid, d)
    when = "сегодня" if d == today() else "завтра"
    if not t:
        return await _send(ctx, uid, f"Хорошо 🌿 Дел на {when} нет. Записать можно в любой момент: «🌿 Мой день» → «📝 Дела на сегодня».")
    await _send(ctx, uid, f"Записала на {when}:\n{t}\n\n" + ("Вечером отметим, что получилось 🌿" if when == "сегодня" else "Утром напомню 🌿"))


async def ask_plan(ctx, uid, du):
    d = today() + timedelta(days=1)
    if not has_access(du, d):
        ctx.application.user_data[uid].pop("den_st", None)
        return await _send(ctx, uid, "Завтра заканчивается ваш доступ. Если хотите продолжить, нажмите «Продлить доступ» в «Мой день» 🌿")
    ctx.application.user_data[uid].pop("den_st", None)
    await _send(ctx, uid, f"🗓 {tomorrow_hint(du['bd'], d)}\n\nЗапишем до 3 дел на завтра?",
                Kb([[Btn("✍️ Записать", callback_data="den:plan"), Btn("Без планов на завтра", callback_data="den:noplan")]]))


async def done_kb(uid, d):
    rows = await db.day_tasks(uid, d)
    btns = [[Btn(("✅ " if r["done"] else "▫️ ") + r["text"][:40], callback_data=f"den:done:{d:%Y%m%d}:{r['idx']}")] for r in rows]
    btns.append([Btn("Готово", callback_data="den:donefin")])
    return Kb(btns)


async def ask_feedback(ctx, uid):
    await _send(ctx, uid, "Пробная неделя закончилась 💛 Расскажите, пожалуйста, как она прошла: 3 коротких вопроса, "
                          "ответы помогут нам сделать «Мой день» лучше.\n\n<b>1. Что понравилось больше всего?</b>",
                Kb([[Btn(lab, callback_data=f"den:fb1:{k}")] for k, lab in LIKED]))


async def access_job(ctx: ContextTypes.DEFAULT_TYPE):
    """Раз в день: конец пробной недели, отзыв, напоминание о конце оплаченного срока."""
    d = today()
    for du in await db.day_all_users():
        uid = du["user_id"]
        paid = du["paid_until"]
        if paid and paid >= d - timedelta(days=1):
            if du["sub_id"] and not du["sub_cancel"]:
                continue  # подписка: напоминание присылает stripe_job
            if paid == d + timedelta(days=3):
                await _send(ctx, uid, f"⏳ Через 3 дня, {paid:%d.%m}, заканчивается ваш доступ к «Мой день».\n\n" + offer_text(), offer_kb(uid))
            elif paid == d - timedelta(days=1):
                await _send(ctx, uid, "Ваш доступ к «Мой день» закончился. Спасибо, что были с нами! 🌿\n\n" + offer_text(), offer_kb(uid))
            continue
        if not du["bd"]:
            continue
        if du["trial_until"] == d:
            await _send(ctx, uid, "Сегодня последний день пробной недели 🌿\n\n" + offer_text(), offer_kb(uid))
        elif du["trial_until"] == d - timedelta(days=1) and not paid:
            await notify_admins(ctx, f"🌿 Пробная неделя закончилась: {du['name']} (ID {uid})")
            if not du["fb_done"]:
                await ask_feedback(ctx, uid)


# ---------- регистрация и меню ----------
async def open_day(chat_id, uid, ctx):
    du = await db.day_user(uid)
    if du and du["bd"]:
        await ctx.bot.send_message(chat_id, f"🌿 <b>Мой день</b>\n{access_text(du)}", parse_mode="HTML", reply_markup=menu_kb(du))
        return
    await ctx.bot.send_message(chat_id, INTRO, parse_mode="HTML",
                               reply_markup=Kb([[Btn("Согласна, начать пробную неделю", callback_data="den:agree")]]))


async def on_paid(chat_id, u, source, ctx):
    o = OPT[source]
    d = today()
    du = await db.day_user(u.id)
    if not du:
        await db.day_create(u.id)
        du = await db.day_user(u.id)
    if o["months"] == 0:  # только PDF
        await db.day_set(u.id, plan_pdf=True)
        await notify_admins(ctx, f"📄 «Мой день»: оплата «{o['label']}» ({o['price']} €) от {u.first_name} (ID {u.id}).\n"
                                 f"Проверьте оплату в Stripe (client_reference_id tg{u.id}) и подготовьте PDF на год."
                                 + ("" if du["bd"] else "\nДаты рождения в боте нет, нужно спросить."))
        await ctx.bot.send_message(chat_id, "Спасибо! 💛 Ваш личный план «Баланс в ритме» в PDF мы подготовим "
                                            "и пришлём сюда в течение 3 рабочих дней.")
        if not du["bd"]:
            ctx.user_data["den_st"] = "pdf_bd"
            await ctx.bot.send_message(chat_id, "Для расчёта нужна ваша дата рождения. Напишите её, пожалуйста, "
                                                "в формате дд.мм.гггг")
        return
    # продление подряд: новый срок начинается на следующий день после старого
    cont = du["paid_until"] and du["paid_until"] >= d
    start = du["paid_until"] + timedelta(days=1) if cont else d
    until = add_months(start, o["months"]) - timedelta(days=1)
    paid_from = du["paid_from"] if (cont and du["paid_from"]) else start
    await db.day_set(u.id, paid_until=until, paid_from=paid_from, active=True, plan_key=source,
                     plan_pdf=(o["pdf"] or du["plan_pdf"]))
    await notify_admins(ctx, f"💳 «Мой день»: оплата «{o['label']}» ({o['price']} €) от {u.first_name} (ID {u.id}). "
                             f"Доступ до {until:%d.%m.%Y}.\nПроверьте оплату в Stripe (client_reference_id tg{u.id})."
                             + ("\n📄 Нужно подготовить личный план PDF." if o["pdf"] else ""))
    txt = f"Спасибо! 💛 Тариф «{o['label']}» оплачен: доступ до {until:%d.%m.%Y}."
    if source == "den_msub":
        txt = (f"Спасибо! 💛 Подписка оформлена: {o['price']} € в месяц. Следующее списание примерно {until + timedelta(days=1):%d.%m.%Y}.\n"
               "За 3 дня до списания я напомню. Отменить можно в любой момент: «Мой день» → «🔄 Моё продление».")
    if o["pdf"]:
        txt += "\n\nВаш личный план в PDF мы подготовим и пришлём сюда в течение 3 рабочих дней."
    await ctx.bot.send_message(chat_id, txt)
    du = await db.day_user(u.id)
    if not du["bd"]:
        return await open_day(chat_id, u.id, ctx)
    schedule(ctx.application, du)
    if cont:
        return await ctx.bot.send_message(chat_id, f"Новый срок начнётся {start:%d.%m.%Y}, план на него я пришлю в этот день 🌿")
    if o["months"] > 1:
        await ctx.bot.send_message(chat_id, overview_text(du, start, until), parse_mode="HTML")
    await send_period_plan(ctx, u.id, du, start)


async def grant_plan_year(chat_id, u, ctx, label):
    """Личный план (89 / 213 / 402 €) включает «Мой день» на год."""
    d = today()
    du = await db.day_user(u.id)
    if not du:
        await db.day_create(u.id)
        du = await db.day_user(u.id)
    cont = du["paid_until"] and du["paid_until"] >= d
    start = du["paid_until"] + timedelta(days=1) if cont else d
    until = add_months(start, 12) - timedelta(days=1)
    await db.day_set(u.id, paid_until=until, paid_from=(du["paid_from"] if cont and du["paid_from"] else start),
                     active=True, plan_key="plan_" + label)
    await ctx.bot.send_message(chat_id, f"🌿 В ваш тариф входит «Мой день» на год: доступ до {until:%d.%m.%Y}.\n"
                                        "Каждое утро и вечер я буду присылать энергию дня, а каждый месяц подробный план.")
    du = await db.day_user(u.id)
    if not du["bd"]:
        return await open_day(chat_id, u.id, ctx)
    schedule(ctx.application, du)
    if cont:
        return
    await ctx.bot.send_message(chat_id, overview_text(du, start, until), parse_mode="HTML")
    await send_period_plan(ctx, u.id, du, start)


def time_kb(prefix, times):
    return Kb([[Btn(t, callback_data=f"den:{prefix}:{t}") for t in times[i:i + 3]] for i in range(0, len(times), 3)])


async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    parts = q.data.split(":")
    act = parts[1]
    chat = q.message.chat_id
    du = await db.day_user(uid)

    if act == "open":
        return await open_day(chat, uid, ctx)
    if act == "agree":
        if not du:
            await db.day_create(uid)
        ctx.user_data["den_st"] = "name"
        return await ctx.bot.send_message(chat, "Как к вам обращаться?")
    if act in ("mt", "et"):
        t = parts[2] + ":" + parts[3]
        if act == "mt":
            await db.day_set(uid, morning=t)
            if ctx.user_data.get("den_edit"):
                return await ctx.bot.send_message(chat, "Время вечера:", reply_markup=time_kb("et", EVENING_TIMES))
            return await ctx.bot.send_message(chat, "Во сколько присылать вечернее сообщение (отметки и план на завтра)?",
                                              reply_markup=time_kb("et", EVENING_TIMES))
        await db.day_set(uid, evening=t)
        if ctx.user_data.pop("den_edit", None):
            du = await db.day_user(uid)
            schedule(ctx.application, du)
            return await ctx.bot.send_message(chat, f"Готово: утро {du['morning']}, вечер {du['evening']} ✅")
        return await ctx.bot.send_message(chat, "Когда писать благодарности?",
                                          reply_markup=Kb([[Btn("Утром", callback_data="den:g:am"), Btn("Вечером", callback_data="den:g:pm")]]))
    if act == "g":
        await db.day_set(uid, grat=parts[2])
        du = await db.day_user(uid)
        if not du["trial_until"] and not du["paid_until"]:
            await db.day_set(uid, trial_until=today() + timedelta(days=TRIAL_DAYS - 1), active=True)
            du = await db.day_user(uid)
            schedule(ctx.application, du)
            await notify_admins(ctx, f"🌿 «Мой день»: новая участница {du['name']} (ID {uid}), пробная неделя до {du['trial_until']:%d.%m}")
            await ctx.bot.send_message(chat, f"Готово! 🎉 Пробная неделя до {du['trial_until']:%d.%m.%Y}.\n"
                                             f"Утром в {du['morning']} энергия дня и аффирмация, вечером в {du['evening']} отметки и план на завтра.")
            await ctx.bot.send_message(chat, year_text(du), parse_mode="HTML")
            ctx.user_data.pop("den_st", None)
            n, p = day_energy(du["bd"], today())
            pre = (ZTXT + "\n\n") if today().day in ZERO else ""
            return await ctx.bot.send_message(chat, pre + f"Сегодня день <b>«{PLAIN[n]}»</b>: {DD.PD[p][0]}.\n\nХотите записать до 3 дел на сегодня? Я спрошу по одному.",
                                              parse_mode="HTML", reply_markup=Kb([[Btn("✍️ Записать сейчас", callback_data="den:now"),
                                                                                   Btn("⏰ Напишу позже", callback_data="den:later")]]))
        if du["paid_until"] and du["bd"] and not du["last_plan"] and is_paid(du):
            # оплатили до регистрации: после регистрации сразу план
            schedule(ctx.application, du)
            await ctx.bot.send_message(chat, year_text(du), parse_mode="HTML")
            return await send_period_plan(ctx, uid, du, period_start(du))
        schedule(ctx.application, du)
        return await ctx.bot.send_message(chat, "Благодарности теперь " + ("утром" if parts[2] == "am" else "вечером") + " ✅")

    # отзыв (работает и без доступа)
    if act == "fb1":
        lab = dict(LIKED).get(parts[2], parts[2])
        await db.day_fb_set(uid, liked=lab)
        ctx.user_data["den_st"] = "fb2"
        await q.edit_message_text(f"<b>1. Что понравилось больше всего?</b>\n{lab}", parse_mode="HTML")
        return await ctx.bot.send_message(chat, "<b>2. Что было неудобно или лишним?</b> Напишите одной фразой.", parse_mode="HTML",
                                          reply_markup=Kb([[Btn("Всё удобно", callback_data="den:fb2ok")]]))
    if act == "fb2ok":
        ctx.user_data.pop("den_st", None)
        await db.day_fb_set(uid, inconvenient="всё удобно")
        await q.edit_message_text("<b>2. Что было неудобно или лишним?</b>\nВсё удобно", parse_mode="HTML")
        return await ask_fb3(ctx, chat)
    if act == "fb3":
        return await finish_fb(ctx, q, uid, parts[2])

    if act == "subinfo":
        return await ctx.bot.send_message(chat, SUB_INFO.format(price=OPT["den_msub"]["price"]), parse_mode="HTML",
                                          reply_markup=Kb([[Btn(f"Оформить · {OPT['den_msub']['price']} € в месяц", url=pay_url(OPT["den_msub"]["url"], uid))],
                                                           [Btn("◀️ Назад", callback_data="den:term:m")]]))
    if act in ("sub", "subok", "subcancel", "subyes", "subresume") and du:
        if act == "subok":
            return await q.edit_message_reply_markup(None)
        if act == "sub":
            kb = None
            if du["sub_id"]:
                kb = Kb([[Btn("Возобновить продление", callback_data="den:subresume")]] if du["sub_cancel"]
                        else [[Btn("Отменить продление", callback_data="den:subcancel")]])
            return await ctx.bot.send_message(chat, sub_status_text(du), reply_markup=kb)
        if act == "subcancel":
            return await ctx.bot.send_message(chat, f"Точно отменить продление? «Мой день» останется до {du['paid_until']:%d.%m.%Y}, дальше списаний не будет.",
                                              reply_markup=Kb([[Btn("Да, отменить", callback_data="den:subyes"), Btn("Нет, оставить", callback_data="den:subok")]]))
        if not du["sub_id"]:
            return await ctx.bot.send_message(chat, sub_status_text(du))
        flag = "true" if act == "subyes" else "false"
        try:
            await stripe("POST", f"subscriptions/{du['sub_id']}", data={"cancel_at_period_end": flag})
        except Exception as e:
            log.warning("Stripe отмена %s: %s", uid, e)
            await notify_admins(ctx, f"⚠️ Не получилось изменить подписку {du['name']} (ID {uid}) в Stripe, проверьте вручную")
            return await ctx.bot.send_message(chat, "Не получилось связаться с оплатой. Мы уже получили сигнал и всё сделаем вручную 💛")
        await db.day_set(uid, sub_cancel=(act == "subyes"))
        if act == "subyes":
            await notify_admins(ctx, f"❌ «Мой день»: {du['name']} (ID {uid}) отменила продление, доступ до {du['paid_until']:%d.%m.%Y}")
            return await q.edit_message_text(f"Продление отменено ✅ Списаний больше не будет. «Мой день» работает до {du['paid_until']:%d.%m.%Y}. Вернуться можно в любой момент 🌿")
        await notify_admins(ctx, f"🔄 «Мой день»: {du['name']} (ID {uid}) возобновила продление")
        return await q.edit_message_text("Продление снова включено ✅")
    if not du or not du["bd"]:
        return await open_day(chat, uid, ctx)
    if act == "year":
        return await ctx.bot.send_message(chat, year_text(du), parse_mode="HTML")
    if act == "offer":
        kb = offer_kb(uid)
        return await ctx.bot.send_message(chat, offer_text() if kb else "Оплата скоро будет доступна 🌿", parse_mode="HTML", reply_markup=kb)
    if act == "term":
        txt, kb = term_msg(parts[2], uid)
        return await ctx.bot.send_message(chat, txt, parse_mode="HTML", reply_markup=kb)
    if act == "goals":
        ctx.user_data["den_st"] = "goals"
        ctx.user_data["den_goals"] = datetime.strptime(parts[2], "%Y%m%d").date().isoformat()
        return await ctx.bot.send_message(chat, "Напишите до 3 целей на месяц, каждую с новой строки.")
    if act == "nogoals":
        ctx.user_data.pop("den_st", None)
        return await q.edit_message_text("Хорошо 🌿 Цели можно добавить позже: «Мой день» → «Мой месяц».")
    if act == "wkskip":
        await q.edit_message_text("Хорошо, итог в другой раз 🌿")
        return await ask_plan(ctx, uid, du)
    if act == "wken":
        wk = today() - timedelta(days=today().weekday())
        await db.day_week_set(uid, wk, energy=parts[2])
        await q.edit_message_text(f"<b>3. Как была энергия недели?</b>\n{parts[2]}", parse_mode="HTML")
        n, done, g = await db.day_week_stats(uid, wk, wk + timedelta(days=6))
        await ctx.bot.send_message(chat, f"Записала 💛 Сделано дел за неделю: {done} из {n}. Благодарностей: {g}.\n"
                                         "Завтра утром пришлю обзор новой недели.")
        return await ask_plan(ctx, uid, du)
    if not has_access(du):
        return await ctx.bot.send_message(chat, "Доступ закончился.\n\n" + offer_text(), parse_mode="HTML", reply_markup=offer_kb(uid))
    if act == "month":
        ps = period_start(du)
        if not ps:
            return await ctx.bot.send_message(chat, "План месяца есть в оплаченном доступе 🌿", reply_markup=offer_kb(uid))
        for m in period_texts(du, ps):
            await ctx.bot.send_message(chat, m, parse_mode="HTML")
        g = await goals_text(uid, ps)
        if g:
            return await ctx.bot.send_message(chat, f"🎯 <b>Ваши цели месяца:</b>\n{g}", parse_mode="HTML")
        return await ctx.bot.send_message(chat, "🎯 Целей на этот месяц пока нет.",
                                          reply_markup=Kb([[Btn("✍️ Записать цели", callback_data=f"den:goals:{ps:%Y%m%d}")]]))
    if act == "week":
        mon = today() - timedelta(days=today().weekday())
        return await ctx.bot.send_message(chat, week_text(du, mon), parse_mode="HTML")
    if act == "today":
        ctx.user_data.pop("den_st", None)
        rows = await db.day_tasks(uid, today())
        if rows:
            await ctx.bot.send_message(chat, "📝 Дела на сегодня. Нажмите на дело, чтобы отметить:", reply_markup=await done_kb(uid, today()))
        if len(rows) < 3:
            return await ctx.bot.send_message(chat, "Записать дела на сегодня? Я спрошу по одному." if not rows else "Можно добавить ещё дело.",
                                              reply_markup=Kb([[Btn("✍️ Записать сейчас", callback_data="den:now"),
                                                                Btn("⏰ Напишу позже", callback_data="den:later")]]))
        return
    if act == "done":
        d = datetime.strptime(parts[2], "%Y%m%d").date()
        await db.day_toggle(uid, d, int(parts[3]))
        return await q.edit_message_reply_markup(await done_kb(uid, d))
    if act == "donefin":
        rows = await db.day_tasks(uid, today())
        n = sum(1 for r in rows if r["done"])
        return await q.edit_message_text(f"Сделано {n} из {len(rows)}. " + ("Отлично! 🌟" if n == len(rows) else "Хорошо, что вы честно отметили 💛 Остальное можно перенести на завтра."))
    if act == "gskip":
        st = ctx.user_data.pop("den_st", None)
        await q.edit_message_text("Хорошо, в другой раз 🌿")
        if st == "grat_then_plan":
            return await after_grat(ctx, uid, du)
        return
    if act in ("now", "plan"):
        await q.edit_message_reply_markup(None)
        return await ask_task(ctx, uid, today() + timedelta(days=1 if act == "plan" else 0), 1)
    if act in ("tskip", "tdone"):
        ud = ctx.user_data
        d = date.fromisoformat(ud.get("den_td") or today().isoformat())
        await q.edit_message_reply_markup(None)
        if act == "tdone":
            ud.pop("den_st", None)
            return await task_summary(ctx, uid, d)
        return await ask_task(ctx, uid, d, int(ud.get("den_q", 1)) + 1)
    if act == "later":
        ctx.user_data.pop("den_st", None)
        await q.edit_message_reply_markup(None)
        return await ctx.bot.send_message(chat, "Хорошо 🌿 Записать дела можно в любой момент: «🌿 Мой день» → «📝 Дела на сегодня».\n"
                                                "А вечером я сама спрошу про дела на завтра.")
    if act == "noplan":
        ctx.user_data.pop("den_st", None)
        return await q.edit_message_text("Хорошо 🌿 Отдыхайте!")
    if act == "time":
        ctx.user_data["den_edit"] = True
        return await ctx.bot.send_message(chat, "Время утреннего сообщения:", reply_markup=time_kb("mt", MORNING_TIMES))
    if act == "grat":
        return await ctx.bot.send_message(chat, "Когда писать благодарности?",
                                          reply_markup=Kb([[Btn("Утром", callback_data="den:g:am"), Btn("Вечером", callback_data="den:g:pm")]]))
    if act == "pause":
        await db.day_set(uid, active=not du["active"])
        du = await db.day_user(uid)
        schedule(ctx.application, du)
        return await ctx.bot.send_message(chat, "⏸ Сообщения на паузе: утром и вечером я ничего не присылаю. Включить снова можно в «🌿 Мой день» → «▶️ Включить сообщения снова»." if not du["active"] else "Сообщения снова включены ✅")


async def ask_fb3(ctx, chat):
    await ctx.bot.send_message(chat, "<b>3. Порекомендуете «Мой день» подруге?</b>", parse_mode="HTML",
                               reply_markup=Kb([[Btn(v, callback_data=f"den:fb3:{k}") for k, v in REC.items()]]))


async def finish_fb(ctx, q, uid, rec):
    await db.day_fb_set(uid, recommend=REC.get(rec, rec))
    await db.day_set(uid, fb_done=True)
    await q.edit_message_text(f"<b>3. Порекомендуете «Мой день» подруге?</b>\n{REC.get(rec, rec)}", parse_mode="HTML")
    chat = q.message.chat_id
    await ctx.bot.send_message(chat, f"Спасибо! 🌿 Если захотите, поделитесь ссылкой с подругой: у неё тоже будет неделя в подарок.\n{BOT_LINK}",
                               disable_web_page_preview=True)
    fb = await db.day_fb(uid)
    du = await db.day_user(uid)
    await notify_admins(ctx, f"⭐ Отзыв о пробной неделе: {du['name'] if du else ''} (ID {uid})\n"
                             f"Понравилось: {fb['liked']}\nНеудобно: {fb['inconvenient']}\nПорекомендует: {fb['recommend']}")
    if not is_paid(du):
        await ctx.bot.send_message(chat, offer_text(), parse_mode="HTML", reply_markup=offer_kb(uid))


BD_RE = re.compile(r"^\s*(\d{1,2})[./-](\d{1,2})[./-](\d{4})\s*$")


def split_items(text):
    return [l.strip(" -•–\t")[:80] for l in text.splitlines() if l.strip(" -•–\t")]


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Возвращает True, если сообщение обработано «Мой день»."""
    st = ctx.user_data.get("den_st")
    if not st:
        return False
    uid = update.effective_user.id
    text = (update.effective_message.text or "").strip()
    msg = update.effective_message
    if not text:
        return False
    if st == "name":
        await db.day_set(uid, name=text[:40])
        ctx.user_data["den_st"] = "bd"
        await msg.reply_text("Ваша дата рождения? Напишите в формате дд.мм.гггг")
        return True
    if st == "bd":
        m = BD_RE.match(text)
        try:
            bd = date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None
        except ValueError:
            bd = None
        if not bd or not (1920 <= bd.year <= today().year - 14):
            await msg.reply_text("Не поняла дату. Напишите в формате дд.мм.гггг")
            return True
        await db.day_set(uid, bd=bd)
        ctx.user_data.pop("den_st")
        await msg.reply_text("Во сколько присылать утреннее сообщение (энергия дня и аффирмация)?", reply_markup=time_kb("mt", MORNING_TIMES))
        return True
    if st == "pdf_bd":
        m = BD_RE.match(text)
        try:
            bd = date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None
        except ValueError:
            bd = None
        if not bd:
            await msg.reply_text("Не поняла дату. Напишите в формате дд.мм.гггг")
            return True
        ctx.user_data.pop("den_st", None)
        await notify_admins(ctx, f"📄 Дата рождения для PDF: {update.effective_user.first_name} (ID {uid}): {bd:%d.%m.%Y}")
        await msg.reply_text("Спасибо! Записала 💛 PDF пришлём сюда в течение 3 рабочих дней.")
        return True
    if st == "fb2":
        await db.day_fb_set(uid, inconvenient=text[:300])
        ctx.user_data.pop("den_st", None)
        await ask_fb3(ctx, msg.chat_id)
        return True
    du = await db.day_user(uid)
    if not has_access(du):
        ctx.user_data.pop("den_st", None)
        return False
    if st in ("grat", "grat_then_plan"):
        n = max(1, min(3, len([l for l in re.split(r"[\n;]+", text) if l.strip()])))
        await db.day_grat(uid, today(), n)
        await msg.reply_text("Спасибо 🙏 Благодарность меняет фокус на хорошее.")
        if st == "grat_then_plan":
            await after_grat(ctx, uid, du)
        else:
            ctx.user_data.pop("den_st", None)
        return True
    if st == "wk_good":
        wk = today() - timedelta(days=today().weekday())
        await db.day_week_set(uid, wk, good=text[:300])
        ctx.user_data["den_st"] = "wk_improve"
        await msg.reply_text("<b>2. Что улучшу на следующей неделе (одна вещь)?</b>", parse_mode="HTML")
        return True
    if st == "wk_improve":
        wk = today() - timedelta(days=today().weekday())
        await db.day_week_set(uid, wk, improve=text[:300])
        ctx.user_data.pop("den_st", None)
        await msg.reply_text("<b>3. Как была энергия недели?</b>", parse_mode="HTML",
                             reply_markup=Kb([[Btn(v, callback_data=f"den:wken:{v}") for v in ("1–3", "4–6", "7–8", "9–10")]]))
        return True
    if st == "goals":
        period = date.fromisoformat(ctx.user_data.get("den_goals") or today().isoformat())
        have = await db.day_goals(uid, period)
        items = split_items(text)[:max(3 - len(have), 0)]
        for i, t in enumerate(items):
            await db.day_add_goal(uid, period, len(have) + i + 1, t)
        ctx.user_data.pop("den_st", None)
        if not items:
            await msg.reply_text("На этот месяц уже есть 3 цели 🙂")
            return True
        await msg.reply_text("Записала цели месяца 🎯\n" + "\n".join(f"▫️ {t}" for t in items) + "\n\nНапомню о них в понедельник.")
        return True
    if st == "task":
        d = date.fromisoformat(ctx.user_data.get("den_td") or today().isoformat())
        q = int(ctx.user_data.get("den_q", 1))
        have = await db.day_tasks(uid, d)
        if len(have) < 3:
            await db.day_add_task(uid, d, len(have) + 1, text.strip()[:80])
        await ask_task(ctx, uid, d, q + 1)
        return True
    if st in ("plan", "today_add"):
        d = today() + timedelta(days=1) if st == "plan" else today()
        items = split_items(text)
        have = await db.day_tasks(uid, d)
        items = items[:max(3 - len(have), 0)]
        for i, t in enumerate(items):
            await db.day_add_task(uid, d, len(have) + i + 1, t)
        ctx.user_data.pop("den_st", None)
        if not items:
            await msg.reply_text("На этот день уже есть 3 дела 🙂 Этого достаточно.")
            return True
        when = "завтра" if st == "plan" else "сегодня"
        lst = "\n".join(f"▫️ {t}" for t in items)
        await msg.reply_text(f"Записала на {when}:\n{lst}\n\n" + ("Утром напомню 🌿" if st == "plan" else "Вечером отметим, что получилось 🌿"))
        return True
    return False


# ---------- админ ----------
async def admin_access(update: Update, ctx: ContextTypes.DEFAULT_TYPE, is_admin):
    """/den_access ID дни — выдать или продлить доступ вручную."""
    if not is_admin(update):
        return
    try:
        uid, days = int(ctx.args[0]), int(ctx.args[1])
    except Exception:
        return await update.message.reply_text("Формат: /den_access 123456789 31")
    du = await db.day_user(uid)
    if not du:
        try:
            await db.day_create(uid)
        except Exception:
            return await update.message.reply_text("Этот человек ещё не заходил в бот.")
        du = await db.day_user(uid)
    d = today()
    cont = du["paid_until"] and du["paid_until"] >= d
    base = du["paid_until"] if cont else d - timedelta(days=1)
    until = base + timedelta(days=days)
    await db.day_set(uid, paid_until=until, paid_from=(du["paid_from"] if cont and du["paid_from"] else d), active=True)
    du = await db.day_user(uid)
    if du["bd"]:
        schedule(ctx.application, du)
    await update.message.reply_text(f"Доступ для {uid} до {until:%d.%m.%Y} ✅")


async def admin_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE, is_admin):
    """/den_reset — начать «Мой день» заново (себе), /den_reset ID — другому человеку. Только для админов."""
    if not is_admin(update):
        return
    try:
        uid = int(ctx.args[0]) if ctx.args else update.effective_user.id
    except ValueError:
        return await update.message.reply_text("Формат: /den_reset или /den_reset 123456789")
    for k in ("m", "e"):
        for j in ctx.application.job_queue.get_jobs_by_name(f"den{k}{uid}"):
            j.schedule_removal()
    await db.day_reset(uid)
    ctx.application.user_data.get(uid, {}).pop("den_st", None)
    await update.message.reply_text(f"«Мой день» для {uid} сброшен ✅ Можно заново: /start → «🌿 Мой день».")


async def admin_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE, is_admin):
    if not is_admin(update):
        return
    rows = await db.day_all_users()
    d = today()
    reg = [r for r in rows if r["bd"]]
    trial = [r for r in reg if r["trial_until"] and r["trial_until"] >= d and not (r["paid_until"] and r["paid_until"] >= d)]
    paid = [r for r in reg if r["paid_until"] and r["paid_until"] >= d]
    lines = [f"🌿 «Мой день»: зарегистрированы {len(reg)} · на пробной неделе {len(trial)} · оплатили {len(paid)}"]
    for r in reg:
        if r in paid:
            st = f"{OPT[r['plan_key']]['label'] if r['plan_key'] in OPT else 'оплачено'} до {r['paid_until']:%d.%m.%Y}"
        elif r in trial:
            st = f"проба до {r['trial_until']:%d.%m}"
        else:
            st = "без доступа"
        lines.append(f"• {r['name']} (ID {r['user_id']}) · {st}" + (" · 📄 нужен PDF" if r["plan_pdf"] else ""))
    pdf_only = [r for r in rows if r["plan_pdf"] and not r["bd"]]
    for r in pdf_only:
        lines.append(f"• ID {r['user_id']} · 📄 только PDF (дата рождения в уведомлении)")
    fbs = await db.day_fb_all()
    if fbs:
        cnt = {v: sum(1 for f in fbs if f["recommend"] == v) for v in REC.values()}
        lines.append("\n⭐ Отзывы: " + " · ".join(f"{k.lower()} {v}" for k, v in cnt.items()))
        for f in fbs[-10:]:
            lines.append(f"• {f['name'] or f['user_id']}: понравилось {f['liked'] or '–'}; неудобно: {f['inconvenient'] or '–'}; "
                         f"порекомендует: {f['recommend'] or '–'}")
    await update.message.reply_text("\n".join(lines)[:4000])
