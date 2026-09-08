from __future__ import annotations

import aiosqlite
import json
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DB:
    def __init__(self, path: Path):
        self.path = str(path)
        self.base = Path(self.path).parent
        self.sub_dir = self.base / "subscriptions"
        self.sub_dir.mkdir(parents=True, exist_ok=True)

    def _sub_file(self, uid: int) -> Path:
        return self.sub_dir / f"{uid}.json"

    def _write_sub_file(self, uid: int, expires: str | None, active: bool) -> None:
        self._sub_file(uid).write_text(
            json.dumps(
                {"user_id": uid, "expires_at": expires, "active": bool(active)},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    async def init(self, default_prices: tuple[int, int, int] = (30000, 60000, 90000)) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users(
                  user_id INTEGER PRIMARY KEY,
                  phone TEXT,
                  first_name TEXT DEFAULT '',
                  last_name TEXT DEFAULT '',
                  username TEXT DEFAULT '',
                  state TEXT DEFAULT 'idle',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS subscriptions(
                  user_id INTEGER PRIMARY KEY,
                  expires_at TEXT,
                  active INTEGER NOT NULL DEFAULT 0,
                  FOREIGN KEY(user_id) REFERENCES users(user_id)
                );
                CREATE TABLE IF NOT EXISTS settings(
                  user_id INTEGER PRIMARY KEY,
                  fish_enabled INTEGER DEFAULT 0,
                  fish_minutes INTEGER DEFAULT 5,
                  fish_sell_op TEXT DEFAULT 'none',
                  fish_sell_value INTEGER DEFAULT 3,
                  fish_feed_op TEXT DEFAULT 'none',
                  fish_feed_value INTEGER DEFAULT 3,
                  fish_fridge INTEGER DEFAULT 1,
                  hop_enabled INTEGER DEFAULT 0,
                  hop_minutes INTEGER DEFAULT 5,
                  withdraw_minutes INTEGER,
                  game_enabled INTEGER DEFAULT 0,
                  game_count INTEGER DEFAULT 100,
                  rescue_enabled INTEGER DEFAULT 1,
                  FOREIGN KEY(user_id) REFERENCES users(user_id)
                );
                CREATE TABLE IF NOT EXISTS selected_groups(
                  user_id INTEGER,
                  chat_id INTEGER,
                  title TEXT,
                  PRIMARY KEY(user_id,chat_id),
                  FOREIGN KEY(user_id) REFERENCES users(user_id)
                );
                CREATE TABLE IF NOT EXISTS services(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL,
                  days INTEGER NOT NULL DEFAULT 30,
                  price INTEGER NOT NULL DEFAULT 0,
                  active INTEGER NOT NULL DEFAULT 1,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS payments(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  service_id INTEGER,
                  days INTEGER NOT NULL,
                  amount INTEGER NOT NULL,
                  status TEXT DEFAULT 'pending',
                  receipt_file_id TEXT,
                  created_at TEXT NOT NULL,
                  reviewed_at TEXT,
                  FOREIGN KEY(user_id) REFERENCES users(user_id),
                  FOREIGN KEY(service_id) REFERENCES services(id)
                );
                """
            )

            # Migrations for older builds.
            c = await db.execute("PRAGMA table_info(users)")
            user_cols = {row[1] for row in await c.fetchall()}
            for col in ("first_name", "last_name", "username"):
                if col not in user_cols:
                    await db.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT DEFAULT ''")

            c = await db.execute("PRAGMA table_info(payments)")
            pay_cols = {row[1] for row in await c.fetchall()}
            if "service_id" not in pay_cols:
                await db.execute("ALTER TABLE payments ADD COLUMN service_id INTEGER")
            if "receipt_file_id" not in pay_cols:
                await db.execute("ALTER TABLE payments ADD COLUMN receipt_file_id TEXT")
            if "reviewed_at" not in pay_cols:
                await db.execute("ALTER TABLE payments ADD COLUMN reviewed_at TEXT")

            # Repair/upgrade service tables created by older builds.
            c = await db.execute("PRAGMA table_info(services)")
            service_cols = {row[1] for row in await c.fetchall()}
            required_service_cols = {
                "name": "TEXT NOT NULL DEFAULT ''",
                "days": "INTEGER NOT NULL DEFAULT 30",
                "price": "INTEGER NOT NULL DEFAULT 0",
                "active": "INTEGER NOT NULL DEFAULT 1",
                "created_at": "TEXT NOT NULL DEFAULT ''",
                "updated_at": "TEXT NOT NULL DEFAULT ''",
            }
            for col, decl in required_service_cols.items():
                if col not in service_cols:
                    await db.execute(f"ALTER TABLE services ADD COLUMN {col} {decl}")

            # Repair settings columns used by the current automation UI.
            c = await db.execute("PRAGMA table_info(settings)")
            setting_cols = {row[1] for row in await c.fetchall()}
            required_setting_cols = {
                "fish_enabled": "INTEGER DEFAULT 0",
                "fish_minutes": "INTEGER DEFAULT 5",
                "fish_sell_op": "TEXT DEFAULT 'none'",
                "fish_sell_value": "INTEGER DEFAULT 3",
                "fish_feed_op": "TEXT DEFAULT 'none'",
                "fish_feed_value": "INTEGER DEFAULT 3",
                "fish_fridge": "INTEGER DEFAULT 1",
                "hop_enabled": "INTEGER DEFAULT 0",
                "hop_minutes": "INTEGER DEFAULT 5",
                "withdraw_minutes": "INTEGER",
                "game_enabled": "INTEGER DEFAULT 0",
                "game_count": "INTEGER DEFAULT 100",
                "rescue_enabled": "INTEGER DEFAULT 1",
            }
            for col, decl in required_setting_cols.items():
                if col not in setting_cols:
                    await db.execute(f"ALTER TABLE settings ADD COLUMN {col} {decl}")

            c = await db.execute("SELECT COUNT(*) FROM services")
            service_count = (await c.fetchone())[0]
            if service_count == 0:
                t = now_iso()
                await db.executemany(
                    "INSERT INTO services(name,days,price,active,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    [
                        ("۱ ماهه", 30, int(default_prices[0]), 1, t, t),
                        ("۲ ماهه", 60, int(default_prices[1]), 1, t, t),
                        ("۳ ماهه", 90, int(default_prices[2]), 1, t, t),
                    ],
                )
            await db.commit()

    async def ensure_user(
        self,
        uid: int,
        first_name: str | None = None,
        last_name: str | None = None,
        username: str | None = None,
    ) -> None:
        t = now_iso()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users(user_id,created_at,updated_at) VALUES(?,?,?)",
                (uid, t, t),
            )
            if any(v is not None for v in (first_name, last_name, username)):
                await db.execute(
                    """UPDATE users
                       SET first_name=COALESCE(?,first_name),
                           last_name=COALESCE(?,last_name),
                           username=COALESCE(?,username),
                           updated_at=?
                       WHERE user_id=?""",
                    (first_name, last_name, username, t, uid),
                )
            else:
                await db.execute("UPDATE users SET updated_at=? WHERE user_id=?", (t, uid))
            await db.commit()

    async def set_phone(self, uid: int, phone: str) -> None:
        await self.ensure_user(uid)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE users SET phone=?,state='idle',updated_at=? WHERE user_id=?",
                (phone, now_iso(), uid),
            )
            await db.commit()

    async def get_phone(self, uid: int):
        async with aiosqlite.connect(self.path) as db:
            c = await db.execute("SELECT phone FROM users WHERE user_id=?", (uid,))
            r = await c.fetchone()
            return r[0] if r else None

    async def set_state(self, uid: int, state: str) -> None:
        await self.ensure_user(uid)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE users SET state=?,updated_at=? WHERE user_id=?",
                (state, now_iso(), uid),
            )
            await db.commit()

    async def get_state(self, uid: int) -> str:
        async with aiosqlite.connect(self.path) as db:
            c = await db.execute("SELECT state FROM users WHERE user_id=?", (uid,))
            r = await c.fetchone()
            return r[0] if r else "idle"

    async def set_subscription(self, uid: int, expires: str, active: int = 1) -> None:
        await self.ensure_user(uid)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO subscriptions(user_id,expires_at,active) VALUES(?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at,active=excluded.active""",
                (uid, expires, active),
            )
            await db.commit()
        self._write_sub_file(uid, expires, active)

    async def get_subscription(self, uid: int):
        async with aiosqlite.connect(self.path) as db:
            c = await db.execute(
                "SELECT expires_at,active FROM subscriptions WHERE user_id=?", (uid,)
            )
            r = await c.fetchone()
            return (r[0], bool(r[1])) if r else (None, False)

    async def disable_subscription(self, uid: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE subscriptions SET active=0 WHERE user_id=?", (uid,))
            await db.commit()
        exp, _ = await self.get_subscription(uid)
        self._write_sub_file(uid, exp, False)

    async def list_services(self, active_only: bool = False):
        sql = "SELECT id,name,days,price,active,created_at,updated_at FROM services"
        if active_only:
            sql += " WHERE active=1"
        sql += " ORDER BY id"
        async with aiosqlite.connect(self.path) as db:
            c = await db.execute(sql)
            return await c.fetchall()

    async def service(self, service_id: int):
        async with aiosqlite.connect(self.path) as db:
            c = await db.execute(
                "SELECT id,name,days,price,active,created_at,updated_at FROM services WHERE id=?",
                (service_id,),
            )
            return await c.fetchone()

    async def create_service(self, name: str, days: int = 30, price: int = 0, active: int = 1) -> int:
        t = now_iso()
        async with aiosqlite.connect(self.path) as db:
            c = await db.execute(
                "INSERT INTO services(name,days,price,active,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (name, days, price, active, t, t),
            )
            await db.commit()
            return int(c.lastrowid)

    async def update_service(self, service_id: int, name: str, days: int, price: int, active: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE services SET name=?,days=?,price=?,active=?,updated_at=? WHERE id=?",
                (name, days, price, active, now_iso(), service_id),
            )
            await db.commit()

    async def delete_service(self, service_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE services SET active=0,updated_at=? WHERE id=?", (now_iso(), service_id))
            await db.commit()

    async def create_payment(self, uid: int, service_id: int, days: int, amount: int) -> int:
        async with aiosqlite.connect(self.path) as db:
            c = await db.execute(
                "INSERT INTO payments(user_id,service_id,days,amount,created_at) VALUES(?,?,?,?,?)",
                (uid, service_id, days, amount, now_iso()),
            )
            await db.commit()
            return int(c.lastrowid)

    async def set_receipt(self, pid: int, file_id: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE payments SET receipt_file_id=? WHERE id=?", (file_id, pid))
            await db.commit()

    async def payment(self, pid: int):
        async with aiosqlite.connect(self.path) as db:
            c = await db.execute(
                "SELECT id,user_id,service_id,days,amount,status,receipt_file_id FROM payments WHERE id=?",
                (pid,),
            )
            return await c.fetchone()

    async def review_payment(self, pid: int, status: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            c = await db.execute(
                "UPDATE payments SET status=?,reviewed_at=? WHERE id=? AND status='pending'",
                (status, now_iso(), pid),
            )
            await db.commit()
            return c.rowcount == 1

    async def selected_groups(self, uid: int):
        async with aiosqlite.connect(self.path) as db:
            c = await db.execute(
                "SELECT chat_id,title FROM selected_groups WHERE user_id=? ORDER BY title", (uid,)
            )
            return await c.fetchall()

    async def replace_groups(self, uid: int, groups) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM selected_groups WHERE user_id=?", (uid,))
            await db.executemany(
                "INSERT INTO selected_groups(user_id,chat_id,title) VALUES(?,?,?)",
                [(uid, a, b) for a, b in groups],
            )
            await db.commit()

    async def settings(self, uid: int):
        await self.ensure_user(uid)
        async with aiosqlite.connect(self.path) as db:
            c = await db.execute(
                """SELECT fish_enabled,fish_minutes,fish_sell_op,fish_sell_value,
                          fish_feed_op,fish_feed_value,fish_fridge,hop_enabled,hop_minutes,
                          withdraw_minutes,game_enabled,game_count,rescue_enabled
                   FROM settings WHERE user_id=?""",
                (uid,),
            )
            r = await c.fetchone()
            if not r:
                await db.execute("INSERT INTO settings(user_id) VALUES(?)", (uid,))
                await db.commit()
                return await self.settings(uid)
            keys = [
                "fish_enabled", "fish_minutes", "fish_sell_op", "fish_sell_value",
                "fish_feed_op", "fish_feed_value", "fish_fridge", "hop_enabled",
                "hop_minutes", "withdraw_minutes", "game_enabled", "game_count", "rescue_enabled",
            ]
            return dict(zip(keys, r))

    async def set_setting(self, uid: int, key: str, value) -> None:
        await self.ensure_user(uid)
        allowed = {
            "fish_enabled", "fish_minutes", "fish_sell_op", "fish_sell_value",
            "fish_feed_op", "fish_feed_value", "fish_fridge", "hop_enabled",
            "hop_minutes", "withdraw_minutes", "game_enabled", "game_count", "rescue_enabled",
        }
        if key not in allowed:
            raise ValueError("bad setting")
        async with aiosqlite.connect(self.path) as db:
            await db.execute(f"UPDATE settings SET {key}=? WHERE user_id=?", (value, uid))
            await db.commit()

    async def pending_payment(self, uid: int):
        async with aiosqlite.connect(self.path) as db:
            c = await db.execute(
                "SELECT id,days,amount FROM payments WHERE user_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
                (uid,),
            )
            return await c.fetchone()

    async def list_users_with_sales(self, limit: int = 500):
        async with aiosqlite.connect(self.path) as db:
            c = await db.execute(
                """
                SELECT u.user_id,u.first_name,u.last_name,u.username,u.created_at,
                       COALESCE(SUM(CASE WHEN p.status='approved' THEN p.amount ELSE 0 END),0) AS sales,
                       s.expires_at,s.active
                FROM users u
                LEFT JOIN payments p ON p.user_id=u.user_id
                LEFT JOIN subscriptions s ON s.user_id=u.user_id
                GROUP BY u.user_id
                ORDER BY sales DESC, u.created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return await c.fetchall()

    async def sales_total(self) -> int:
        async with aiosqlite.connect(self.path) as db:
            c = await db.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='approved'")
            r = await c.fetchone()
            return int(r[0] or 0)

    async def counts(self):
        async with aiosqlite.connect(self.path) as db:
            c = await db.execute("SELECT COUNT(*) FROM users")
            users = int((await c.fetchone())[0])
            c = await db.execute("SELECT COUNT(*) FROM subscriptions WHERE active=1")
            active = int((await c.fetchone())[0])
            c = await db.execute("SELECT COUNT(*) FROM payments WHERE status='pending'")
            pending = int((await c.fetchone())[0])
            return users, active, pending
