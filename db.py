# -*- coding: utf-8 -*-
"""Работа с базой PostgreSQL."""
import asyncpg

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            BIGINT PRIMARY KEY,
    first_name    TEXT,
    username      TEXT,
    source        TEXT,
    news_consent  BOOLEAN,
    stopped       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS webinars (
    id         SERIAL PRIMARY KEY,
    starts_at  TIMESTAMPTZ NOT NULL,
    title      TEXT NOT NULL,
    link       TEXT,
    sent_24h   BOOLEAN NOT NULL DEFAULT FALSE,
    sent_1h    BOOLEAN NOT NULL DEFAULT FALSE,
    sent_10m   BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE IF NOT EXISTS registrations (
    user_id    BIGINT REFERENCES users(id) ON DELETE CASCADE,
    webinar_id INT REFERENCES webinars(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, webinar_id)
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS reviews (
    id         SERIAL PRIMARY KEY,
    user_id    BIGINT REFERENCES users(id) ON DELETE CASCADE,
    text       TEXT NOT NULL,
    consent    TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS attended (
    user_id    BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reminded   BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE IF NOT EXISTS day_users (
    user_id     BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT,
    bd          DATE,
    morning     TEXT NOT NULL DEFAULT '07:00',
    evening     TEXT NOT NULL DEFAULT '20:00',
    grat        TEXT NOT NULL DEFAULT 'pm',
    trial_until DATE,
    paid_until  DATE,
    plan_pdf    BOOLEAN NOT NULL DEFAULT FALSE,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS day_tasks (
    user_id  BIGINT REFERENCES users(id) ON DELETE CASCADE,
    day      DATE NOT NULL,
    idx      INT NOT NULL,
    text     TEXT NOT NULL,
    done     BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (user_id, day, idx)
);
ALTER TABLE day_users ADD COLUMN IF NOT EXISTS paid_from DATE;
ALTER TABLE day_users ADD COLUMN IF NOT EXISTS last_plan DATE;
ALTER TABLE day_users ADD COLUMN IF NOT EXISTS plan_key TEXT;
ALTER TABLE day_users ADD COLUMN IF NOT EXISTS fb_done BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE day_users ADD COLUMN IF NOT EXISTS sub_id TEXT;
ALTER TABLE day_users ADD COLUMN IF NOT EXISTS sub_cancel BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE day_users ADD COLUMN IF NOT EXISTS sub_reminded DATE;
CREATE TABLE IF NOT EXISTS day_goals (
    user_id  BIGINT REFERENCES users(id) ON DELETE CASCADE,
    period   DATE NOT NULL,
    idx      INT NOT NULL,
    text     TEXT NOT NULL,
    done     BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (user_id, period, idx)
);
CREATE TABLE IF NOT EXISTS day_week (
    user_id  BIGINT REFERENCES users(id) ON DELETE CASCADE,
    week     DATE NOT NULL,
    good     TEXT,
    improve  TEXT,
    energy   TEXT,
    PRIMARY KEY (user_id, week)
);
CREATE TABLE IF NOT EXISTS day_feedback (
    user_id     BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    liked       TEXT,
    inconvenient TEXT,
    recommend   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS day_log (
    user_id  BIGINT REFERENCES users(id) ON DELETE CASCADE,
    day      DATE NOT NULL,
    grat     INT NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS events (
    id       BIGSERIAL PRIMARY KEY,
    user_id  BIGINT REFERENCES users(id) ON DELETE CASCADE,
    at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind     TEXT NOT NULL,
    detail   TEXT
);
CREATE INDEX IF NOT EXISTS events_user ON events(user_id, at);
CREATE INDEX IF NOT EXISTS events_at ON events(at);
CREATE TABLE IF NOT EXISTS consult_choice (
    user_id    BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    product    TEXT,
    choice     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class DB:
    def __init__(self, url):
        # Neon добавляет channel_binding=require — asyncpg этот параметр не понимает, убираем
        import re
        url = re.sub(r"[?&]channel_binding=[^&]*", "", url).replace("?&", "?")
        self.url = url
        self.pool = None

    async def connect(self):
        # min_size=0 и закрытие простаивающих соединений через 60 с: база Neon может «заснуть»
        # через 5 минут без запросов, и у бота не остаётся «мёртвых» соединений
        self.pool = await asyncpg.create_pool(self.url, min_size=0, max_size=5,
                                              max_inactive_connection_lifetime=60)
        async with self.pool.acquire() as c:
            await c.execute(SCHEMA)
        if not await self.get_setting("events_since"):
            from datetime import datetime, timezone
            await self.set_setting("events_since", datetime.now(timezone.utc).isoformat())

    # --- журнал событий (для статистики) ---
    async def ev(self, uid, kind, detail=None):
        try:
            await self.pool.execute("INSERT INTO events(user_id,kind,detail) VALUES($1,$2,$3)", uid, kind,
                                    None if detail is None else str(detail)[:300])
        except Exception:
            pass

    async def close(self):
        if self.pool:
            await self.pool.close()

    # --- «Мой день» ---
    async def day_user(self, uid):
        return await self.pool.fetchrow("SELECT * FROM day_users WHERE user_id=$1", uid)

    async def day_create(self, uid):
        r = await self.pool.execute("INSERT INTO day_users(user_id) VALUES($1) ON CONFLICT DO NOTHING", uid)
        if r.endswith("1"):
            await self.ev(uid, "den_open")

    async def day_set(self, uid, **kw):
        keys = list(kw)
        sets = ", ".join(f"{k}=${i+2}" for i, k in enumerate(keys))
        await self.pool.execute(f"UPDATE day_users SET {sets} WHERE user_id=$1", uid, *[kw[k] for k in keys])
        if kw.get("trial_until"):
            await self.ev(uid, "den_trial", f"{kw['trial_until']:%d.%m.%Y}")
        if kw.get("paid_until") and "plan_key" in kw:
            await self.ev(uid, "den_paid", f"{kw.get('plan_key')} до {kw['paid_until']:%d.%m.%Y}")
        if kw.get("sub_cancel") is True:
            await self.ev(uid, "sub_cancel")
        if "sub_id" in kw and kw["sub_id"] is None and "plan_key" in kw:
            await self.ev(uid, "sub_end")

    async def day_active_users(self):
        return await self.pool.fetch("SELECT * FROM day_users WHERE active AND bd IS NOT NULL")

    async def day_all_users(self):
        return await self.pool.fetch("SELECT * FROM day_users ORDER BY created_at")

    async def day_tasks(self, uid, day):
        return await self.pool.fetch("SELECT * FROM day_tasks WHERE user_id=$1 AND day=$2 ORDER BY idx", uid, day)

    async def day_add_task(self, uid, day, idx, text):
        await self.pool.execute("INSERT INTO day_tasks(user_id,day,idx,text) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",
                                uid, day, idx, text)

    async def day_toggle(self, uid, day, idx):
        await self.pool.execute("UPDATE day_tasks SET done = NOT done WHERE user_id=$1 AND day=$2 AND idx=$3", uid, day, idx)

    async def day_reset(self, uid):
        await self.ev(uid, "den_reset")
        for t in ("day_tasks", "day_goals", "day_week", "day_feedback", "day_log", "day_users"):
            await self.pool.execute(f"DELETE FROM {t} WHERE user_id=$1", uid)

    async def day_goals(self, uid, period):
        return await self.pool.fetch("SELECT * FROM day_goals WHERE user_id=$1 AND period=$2 ORDER BY idx", uid, period)

    async def day_add_goal(self, uid, period, idx, text):
        await self.pool.execute("INSERT INTO day_goals(user_id,period,idx,text) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",
                                uid, period, idx, text)

    async def day_week_set(self, uid, week, **kw):
        await self.pool.execute("INSERT INTO day_week(user_id,week) VALUES($1,$2) ON CONFLICT DO NOTHING", uid, week)
        keys = list(kw)
        sets = ", ".join(f"{k}=${i+3}" for i, k in enumerate(keys))
        await self.pool.execute(f"UPDATE day_week SET {sets} WHERE user_id=$1 AND week=$2", uid, week, *[kw[k] for k in keys])

    async def day_week_stats(self, uid, d1, d2):
        t = await self.pool.fetchrow("SELECT count(*) AS n, count(*) FILTER (WHERE done) AS done FROM day_tasks "
                                     "WHERE user_id=$1 AND day BETWEEN $2 AND $3", uid, d1, d2)
        g = await self.pool.fetchval("SELECT coalesce(sum(grat),0) FROM day_log WHERE user_id=$1 AND day BETWEEN $2 AND $3", uid, d1, d2)
        return t["n"], t["done"], g

    async def day_fb_set(self, uid, **kw):
        if kw.get("recommend"):
            await self.ev(uid, "feedback", kw["recommend"])
        await self.pool.execute("INSERT INTO day_feedback(user_id) VALUES($1) ON CONFLICT DO NOTHING", uid)
        keys = list(kw)
        sets = ", ".join(f"{k}=${i+2}" for i, k in enumerate(keys))
        await self.pool.execute(f"UPDATE day_feedback SET {sets} WHERE user_id=$1", uid, *[kw[k] for k in keys])

    async def day_fb(self, uid):
        return await self.pool.fetchrow("SELECT * FROM day_feedback WHERE user_id=$1", uid)

    async def day_fb_all(self):
        return await self.pool.fetch("SELECT f.*, d.name FROM day_feedback f LEFT JOIN day_users d USING(user_id) ORDER BY f.created_at")

    async def day_grat(self, uid, day, n):
        await self.pool.execute("""INSERT INTO day_log(user_id,day,grat) VALUES($1,$2,$3)
                                   ON CONFLICT (user_id,day) DO UPDATE SET grat=EXCLUDED.grat""", uid, day, n)

    # --- пользователи ---
    async def upsert_user(self, uid, first_name, username, source=None):
        new = await self.pool.fetchval(
            """INSERT INTO users (id, first_name, username, source) VALUES ($1,$2,$3,$4)
               ON CONFLICT (id) DO UPDATE SET first_name=$2, username=$3,
                 stopped=FALSE, last_seen=now(),
                 source=COALESCE(users.source, EXCLUDED.source)
               RETURNING (xmax = 0)""",
            uid, first_name, username, source)
        if new:
            await self.ev(uid, "new", source)
        elif source:
            await self.ev(uid, "start", source)

    async def get_user(self, uid):
        return await self.pool.fetchrow("SELECT * FROM users WHERE id=$1", uid)

    async def set_consent(self, uid, value):
        await self.pool.execute("UPDATE users SET news_consent=$2 WHERE id=$1", uid, value)
        await self.ev(uid, "news_yes" if value else "news_no")

    async def stop_user(self, uid):
        r = await self.pool.execute("UPDATE users SET stopped=TRUE WHERE id=$1 AND stopped=FALSE", uid)
        if r.endswith("1"):
            await self.ev(uid, "stop")

    async def delete_user(self, uid):
        await self.pool.execute("DELETE FROM users WHERE id=$1", uid)

    async def news_recipients(self):
        rows = await self.pool.fetch(
            "SELECT id FROM users WHERE news_consent IS TRUE AND stopped IS FALSE")
        return [r["id"] for r in rows]

    async def news_unregistered(self):
        """Согласившиеся на новости, которые не записаны ни на один предстоящий эфир."""
        rows = await self.pool.fetch(
            """SELECT u.id FROM users u
               WHERE u.news_consent IS TRUE AND u.stopped IS FALSE
                 AND NOT EXISTS (SELECT 1 FROM registrations r JOIN webinars w ON w.id=r.webinar_id
                                 WHERE r.user_id=u.id AND w.starts_at > now())""")
        return [r["id"] for r in rows]

    async def user_registrations(self, uid):
        """Предстоящие эфиры, на которые записан пользователь."""
        return await self.pool.fetch(
            """SELECT w.* FROM registrations r JOIN webinars w ON w.id=r.webinar_id
               WHERE r.user_id=$1 AND w.starts_at > now() ORDER BY w.starts_at""", uid)

    async def stats(self):
        return await self.pool.fetchrow(
            """SELECT count(*) AS total,
                      count(*) FILTER (WHERE news_consent IS TRUE AND NOT stopped) AS subscribed,
                      count(*) FILTER (WHERE stopped) AS stopped,
                      count(*) FILTER (WHERE created_at > now() - interval '7 days') AS week
               FROM users""")

    async def sources(self):
        return await self.pool.fetch(
            "SELECT COALESCE(source,'—') AS source, count(*) AS n FROM users GROUP BY 1 ORDER BY 2 DESC")

    # --- эфиры ---
    async def add_webinar(self, starts_at, title):
        return await self.pool.fetchval(
            "INSERT INTO webinars (starts_at, title) VALUES ($1,$2) RETURNING id", starts_at, title)

    async def set_link(self, wid, link):
        r = await self.pool.execute("UPDATE webinars SET link=$2 WHERE id=$1", wid, link)
        return r.endswith("1")

    async def delete_webinar(self, wid):
        r = await self.pool.execute("DELETE FROM webinars WHERE id=$1", wid)
        return r.endswith("1")

    async def webinars(self):
        return await self.pool.fetch(
            """SELECT w.*, (SELECT count(*) FROM registrations r WHERE r.webinar_id=w.id) AS regs
               FROM webinars w ORDER BY starts_at""")

    async def upcoming_webinars(self, limit=8):
        return await self.pool.fetch(
            "SELECT * FROM webinars WHERE starts_at > now() ORDER BY starts_at LIMIT $1", limit)

    async def next_webinar(self):
        return await self.pool.fetchrow(
            "SELECT * FROM webinars WHERE starts_at > now() ORDER BY starts_at LIMIT 1")

    async def get_webinar(self, wid):
        return await self.pool.fetchrow("SELECT * FROM webinars WHERE id=$1", wid)

    async def register(self, uid, wid):
        r = await self.pool.execute(
            "INSERT INTO registrations (user_id, webinar_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
            uid, wid)
        if r.endswith("1"):
            await self.ev(uid, "reg", wid)
        return r.endswith("1")

    async def unregister(self, uid, wid):
        await self.pool.execute(
            "DELETE FROM registrations WHERE user_id=$1 AND webinar_id=$2", uid, wid)
        await self.ev(uid, "unreg", wid)

    async def registrants_full(self, wid):
        return await self.pool.fetch(
            """SELECT u.id, u.first_name, u.username, u.source, u.stopped, r.created_at
               FROM registrations r JOIN users u ON u.id=r.user_id
               WHERE r.webinar_id=$1 ORDER BY r.created_at""", wid)

    async def users_by_ids(self, ids):
        return await self.pool.fetch("SELECT id, first_name, username FROM users WHERE id = ANY($1::bigint[])", list(ids))

    async def registrants(self, wid):
        rows = await self.pool.fetch(
            """SELECT u.id FROM registrations r JOIN users u ON u.id=r.user_id
               WHERE r.webinar_id=$1 AND u.stopped IS FALSE""", wid)
        return [r["id"] for r in rows]

    async def due_reminders(self):
        """Эфиры, по которым пора отправить напоминание: (webinar, kind)."""
        out = []
        for kind, delta in (("sent_24h", "24 hours"), ("sent_1h", "1 hour"), ("sent_10m", "10 minutes")):
            rows = await self.pool.fetch(
                f"""SELECT * FROM webinars WHERE NOT {kind}
                    AND now() >= starts_at - interval '{delta}' AND now() < starts_at""")
            out += [(r, kind) for r in rows]
        return out

    async def mark_sent(self, wid, kind):
        assert kind in ("sent_24h", "sent_1h", "sent_10m")
        # если пропустили более раннее напоминание (бот был выключен) — не шлём его задним числом
        extra = {"sent_1h": ", sent_24h=TRUE", "sent_10m": ", sent_24h=TRUE, sent_1h=TRUE"}.get(kind, "")
        await self.pool.execute(f"UPDATE webinars SET {kind}=TRUE{extra} WHERE id=$1", wid)

    # --- настройки, отзывы, «был на эфире» ---
    async def get_setting(self, key, default=None):
        v = await self.pool.fetchval("SELECT value FROM settings WHERE key=$1", key)
        return v if v is not None else default

    async def set_setting(self, key, value):
        await self.pool.execute(
            "INSERT INTO settings (key, value) VALUES ($1,$2) "
            "ON CONFLICT (key) DO UPDATE SET value=$2", key, value)

    async def add_review(self, uid, text):
        await self.ev(uid, "review")
        return await self.pool.fetchval(
            "INSERT INTO reviews (user_id, text) VALUES ($1,$2) RETURNING id", uid, text)

    async def set_review_consent(self, rid, uid, consent):
        r = await self.pool.execute(
            "UPDATE reviews SET consent=$3 WHERE id=$1 AND user_id=$2", rid, uid, consent)
        return r.endswith("1")

    async def last_reviews(self, n=10):
        return await self.pool.fetch(
            """SELECT r.id, r.text, r.consent, r.created_at, u.first_name, u.username
               FROM reviews r JOIN users u ON u.id=r.user_id ORDER BY r.id DESC LIMIT $1""", n)

    async def mark_attended(self, uid):
        await self.pool.execute(
            "INSERT INTO attended (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)
        await self.ev(uid, "attended")

    async def due_review_reminders(self):
        rows = await self.pool.fetch(
            """SELECT a.user_id FROM attended a JOIN users u ON u.id=a.user_id
               WHERE NOT a.reminded AND NOT u.stopped AND a.created_at < now() - interval '20 hours'
                 AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.user_id=a.user_id)""")
        return [r["user_id"] for r in rows]

    async def mark_review_reminded(self, uids):
        await self.pool.execute("UPDATE attended SET reminded=TRUE WHERE user_id = ANY($1::bigint[])", uids)

    async def counts_extra(self):
        return await self.pool.fetchrow(
            "SELECT (SELECT count(*) FROM attended) AS attended, (SELECT count(*) FROM reviews) AS reviews")

    # --- выбор консультанта после оплаты ---
    async def set_consult(self, uid, product, choice):
        await self.pool.execute(
            """INSERT INTO consult_choice (user_id, product, choice) VALUES ($1,$2,$3)
               ON CONFLICT (user_id) DO UPDATE SET product=$2, choice=$3, created_at=now()""",
            uid, product, choice)
        await self.ev(uid, "consult", f"{product}: {choice}")

    async def last_consults(self, n=20):
        return await self.pool.fetch(
            """SELECT c.user_id, c.product, c.choice, c.created_at, u.first_name, u.username
               FROM consult_choice c JOIN users u ON u.id=c.user_id
               ORDER BY c.created_at DESC LIMIT $1""", n)

    # --- статистика ---
    async def events_period(self, since):
        return await self.pool.fetch("SELECT kind, detail, count(*) AS n FROM events WHERE at >= $1 GROUP BY 1,2", since)

    async def last_events(self, n=30):
        return await self.pool.fetch(
            """SELECT e.*, u.first_name, u.username FROM events e LEFT JOIN users u ON u.id=e.user_id
               ORDER BY e.at DESC LIMIT $1""", n)

    async def user_events(self, uid):
        return await self.pool.fetch("SELECT * FROM events WHERE user_id=$1 ORDER BY at", uid)

    async def user_history(self, uid):
        """Старые записи (до появления журнала): регистрации на эфиры, «был на эфире», отзывы, «Мой день», консультации."""
        return await self.pool.fetch(
            """SELECT r.created_at AS at, 'reg' AS kind, r.webinar_id::text AS detail FROM registrations r WHERE r.user_id=$1
               UNION ALL SELECT created_at, 'attended', NULL FROM attended WHERE user_id=$1
               UNION ALL SELECT created_at, 'review', NULL FROM reviews WHERE user_id=$1
               UNION ALL SELECT created_at, 'den_open', NULL FROM day_users WHERE user_id=$1
               UNION ALL SELECT created_at, 'consult', product || ': ' || choice FROM consult_choice WHERE user_id=$1""", uid)

    async def counts_since(self, since):
        return await self.pool.fetchrow(
            """SELECT (SELECT count(*) FROM users WHERE created_at >= $1) AS new_users,
                      (SELECT count(*) FROM registrations WHERE created_at >= $1) AS regs,
                      (SELECT count(*) FROM attended WHERE created_at >= $1) AS attended,
                      (SELECT count(*) FROM reviews WHERE created_at >= $1) AS reviews,
                      (SELECT count(*) FROM day_users WHERE created_at >= $1 AND bd IS NOT NULL) AS den_users""", since)

    async def export_users(self):
        return await self.pool.fetch(
            """SELECT u.*, d.name AS den_name, d.bd AS den_bd, d.created_at AS den_since, d.trial_until, d.paid_until,
                      d.plan_key, d.plan_pdf, d.sub_id, d.sub_cancel, d.active AS den_active,
                      (SELECT string_agg(to_char(w.starts_at AT TIME ZONE 'Europe/Berlin','DD.MM'), ', ' ORDER BY w.starts_at)
                         FROM registrations r JOIN webinars w ON w.id=r.webinar_id WHERE r.user_id=u.id) AS webinars,
                      EXISTS(SELECT 1 FROM attended a WHERE a.user_id=u.id) AS was_live,
                      (SELECT count(*) FROM reviews r WHERE r.user_id=u.id) AS n_reviews,
                      c.product AS consult_product, c.choice AS consult_choice,
                      f.liked, f.inconvenient, f.recommend
               FROM users u LEFT JOIN day_users d ON d.user_id=u.id
               LEFT JOIN consult_choice c ON c.user_id=u.id LEFT JOIN day_feedback f ON f.user_id=u.id
               ORDER BY u.created_at""")

    async def export_events(self):
        return await self.pool.fetch(
            """SELECT e.at, e.user_id, u.first_name, u.username, e.kind, e.detail
               FROM events e LEFT JOIN users u ON u.id=e.user_id ORDER BY e.at""")
