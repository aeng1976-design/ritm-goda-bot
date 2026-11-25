# -*- coding: utf-8 -*-
"""Расчёты для «Мой день»: те же формулы, что в личном плане «Баланс в ритме»."""
from datetime import date


def red(n):
    while n > 9:
        n = sum(map(int, str(n)))
    return n


def r22(n):
    while n > 22:
        n = sum(map(int, str(n)))
    return n


def ds(x):
    return sum(int(c) for c in str(x) if c.isdigit())


CONST = {1: 7, 2: 9, 3: 28, 4: 10, 5: 8, 6: 16, 7: 11, 8: 14, 9: 17, 10: 25, 11: 2, 12: 19}


def _ms(num, age):
    q = num / age
    digs = "".join(c for c in f"{q:.6f}" if c.isdigit())[:4]
    return int(digs[0]) + int(digs[1]), int(digs[2]) + int(digs[3])


def _addcode(c, d):
    out = ""
    for ch in c:
        x = int(ch)
        v = red(x + d) if d > 0 else (abs(x - abs(d)) if d < 0 else red(x + 1))
        out += str(v)
    if out[0] == "0":
        out = "1" + out[1:]
    return out


def age_on(bd: date, day: date):
    return day.year - bd.year - ((day.month, day.day) < (bd.month, bd.day))


def last_birthday(bd: date, day: date):
    y = day.year if (day.month, day.day) >= (bd.month, bd.day) else day.year - 1
    try:
        return date(y, bd.month, bd.day)
    except ValueError:  # 29.02
        return date(y, 3, 1)


def personal_year(bd: date, year: int):
    """Личный год (1–9): все цифры дня и месяца рождения + цифры года."""
    return red(ds(f"{bd.day:02d}{bd.month:02d}") + ds(year))


def year_task(bd: date, year: int):
    """Задача года (1–22): день + месяц + сумма цифр года."""
    return r22(bd.day + bd.month + ds(year))


def day_task(bd: date, day: date):
    """Задача дня: задача календарного года + месяц, затем + число."""
    return r22(r22(year_task(bd, day.year) + day.month) + day.day)


def personal_day(bd: date, day: date):
    return red(red(personal_year(bd, day.year) + day.month) + day.day)


def personal_month(bd: date, day: date):
    return red(personal_year(bd, day.year) + day.month)


def energy_code(bd: date):
    return str(int(f"{bd.day:02d}{bd.month:02d}") * bd.year)


def energy(bd: date, age: int):
    c = energy_code(bd)
    return int(c[(age - 17) % len(c)])


def moon_sun(bd: date, day: date):
    """Луна и Солнце: за текущий год жизни и за текущий месяц. Возвращает ((луна, солнце, разница), (…месяц))."""
    age = max(age_on(bd, day), 1)
    s = str(bd.day * bd.month * bd.year)
    s = s + "0" * (6 - len(s))
    yd = red(ds(day.year))
    mod = "".join(str(red(int(ch) + yd)) for ch in s)
    mo, su = _ms(int(s), age)
    df = su - mo
    dbl = mod if su == 0 else _addcode(mod, df)
    mo2, su2 = _ms(int(dbl), CONST[day.month])
    return (mo, su, df), (mo2, su2, su2 - mo2)
