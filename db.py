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
"""


class DB:
    def __init__(self, url):
        # Neon добавляет channel_binding=require — asyncpg этот параметр не понимает, убираем
        import re
        url = re.sub(r"[?&]channel_binding=[^&]*", "", url).replace("?&", "?")
        self.url = url
        self.pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(self.url, min_size=1, max_size=5)
        async with self.pool.acquire() as c:
            await c.execute(SCHEMA)

    async def close(self):
        if self.pool:
            await self.pool.close()

    # --- пользователи ---
    async def upsert_user(self, uid, first_name, username, source=None):
        await self.pool.execute(
            """INSERT INTO users (id, first_name, username, source) VALUES ($1,$2,$3,$4)
               ON CONFLICT (id) DO UPDATE SET first_name=$2, username=$3,
                 stopped=FALSE, last_seen=now(),
                 source=COALESCE(users.source, EXCLUDED.source)""",
            uid, first_name, username, source)

    async def get_user(self, uid):
        return await self.pool.fetchrow("SELECT * FROM users WHERE id=$1", uid)

    async def set_consent(self, uid, value):
        await self.pool.execute("UPDATE users SET news_consent=$2 WHERE id=$1", uid, value)

    async def stop_user(self, uid):
        await self.pool.execute("UPDATE users SET stopped=TRUE WHERE id=$1", uid)

    async def delete_user(self, uid):
        await self.pool.execute("DELETE FROM users WHERE id=$1", uid)

    async def news_recipients(self):
        rows = await self.pool.fetch(
            "SELECT id FROM users WHERE news_consent IS TRUE AND stopped IS FALSE")
        return [r["id"] for r in rows]

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
        return r.endswith("1")

    async def unregister(self, uid, wid):
        await self.pool.execute(
            "DELETE FROM registrations WHERE user_id=$1 AND webinar_id=$2", uid, wid)

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
