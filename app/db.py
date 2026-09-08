from __future__ import annotations
import aiosqlite
from datetime import datetime, timezone
import json
from pathlib import Path

def now_iso(): return datetime.now(timezone.utc).isoformat()

class DB:
    def __init__(self,path):
        self.path=str(path)
        self.base=Path(self.path).parent
        self.sub_dir=self.base/"subscriptions"
        self.sub_dir.mkdir(parents=True,exist_ok=True)
    def _sub_file(self,uid): return self.sub_dir/f"{uid}.json"
    def _write_sub_file(self,uid,expires,active):
        self._sub_file(uid).write_text(json.dumps({"user_id":uid,"expires_at":expires,"active":bool(active)},ensure_ascii=False,indent=2),encoding="utf-8")
    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users(
              user_id INTEGER PRIMARY KEY, phone TEXT, state TEXT DEFAULT 'idle',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS subscriptions(
              user_id INTEGER PRIMARY KEY, expires_at TEXT, active INTEGER NOT NULL DEFAULT 0,
              FOREIGN KEY(user_id) REFERENCES users(user_id)
            );
            CREATE TABLE IF NOT EXISTS settings(
              user_id INTEGER PRIMARY KEY, fish_enabled INTEGER DEFAULT 0,
              fish_sell_op TEXT DEFAULT 'none', fish_sell_value INTEGER DEFAULT 3,
              fish_feed_op TEXT DEFAULT 'none', fish_feed_value INTEGER DEFAULT 3,
              fish_fridge INTEGER DEFAULT 1,
              hop_enabled INTEGER DEFAULT 0, hop_minutes INTEGER DEFAULT 5,
              withdraw_minutes INTEGER, game_enabled INTEGER DEFAULT 0, game_count INTEGER DEFAULT 100,
              rescue_enabled INTEGER DEFAULT 1,
              FOREIGN KEY(user_id) REFERENCES users(user_id)
            );
            CREATE TABLE IF NOT EXISTS selected_groups(
              user_id INTEGER, chat_id INTEGER, title TEXT,
              PRIMARY KEY(user_id,chat_id), FOREIGN KEY(user_id) REFERENCES users(user_id)
            );
            CREATE TABLE IF NOT EXISTS payments(
              id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
              days INTEGER NOT NULL, amount INTEGER NOT NULL, status TEXT DEFAULT 'pending',
              receipt_file_id TEXT, created_at TEXT NOT NULL, reviewed_at TEXT,
              FOREIGN KEY(user_id) REFERENCES users(user_id)
            );
            """)
            await db.commit()
    async def ensure_user(self,uid):
        t=now_iso()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("INSERT OR IGNORE INTO users(user_id,created_at,updated_at) VALUES(?,?,?)",(uid,t,t))
            await db.execute("UPDATE users SET updated_at=? WHERE user_id=?",(t,uid))
            await db.commit()
    async def set_phone(self,uid,phone):
        await self.ensure_user(uid)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE users SET phone=?,state='idle',updated_at=? WHERE user_id=?",(phone,now_iso(),uid)); await db.commit()
    async def get_phone(self,uid):
        async with aiosqlite.connect(self.path) as db:
            c=await db.execute("SELECT phone FROM users WHERE user_id=?",(uid,)); r=await c.fetchone(); return r[0] if r else None
    async def set_state(self,uid,state):
        await self.ensure_user(uid)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE users SET state=?,updated_at=? WHERE user_id=?",(state,now_iso(),uid)); await db.commit()
    async def get_state(self,uid):
        async with aiosqlite.connect(self.path) as db:
            c=await db.execute("SELECT state FROM users WHERE user_id=?",(uid,)); r=await c.fetchone(); return r[0] if r else "idle"
    async def set_subscription(self,uid,expires,active=1):
        await self.ensure_user(uid)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("INSERT INTO subscriptions(user_id,expires_at,active) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at,active=excluded.active",(uid,expires,active)); await db.commit()
        self._write_sub_file(uid,expires,active)
    async def get_subscription(self,uid):
        async with aiosqlite.connect(self.path) as db:
            c=await db.execute("SELECT expires_at,active FROM subscriptions WHERE user_id=?",(uid,)); r=await c.fetchone()
            return (r[0],bool(r[1])) if r else (None,False)
    async def disable_subscription(self,uid):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE subscriptions SET active=0 WHERE user_id=?",(uid,)); await db.commit()
        exp,_=await self.get_subscription(uid)
        self._write_sub_file(uid,exp,0)
    async def create_payment(self,uid,days,amount):
        async with aiosqlite.connect(self.path) as db:
            c=await db.execute("INSERT INTO payments(user_id,days,amount,created_at) VALUES(?,?,?,?)",(uid,days,amount,now_iso())); await db.commit(); return c.lastrowid
    async def set_receipt(self,pid,file_id):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE payments SET receipt_file_id=? WHERE id=?",(file_id,pid)); await db.commit()
    async def payment(self,pid):
        async with aiosqlite.connect(self.path) as db:
            c=await db.execute("SELECT id,user_id,days,amount,status,receipt_file_id FROM payments WHERE id=?",(pid,)); return await c.fetchone()
    async def review_payment(self,pid,status):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE payments SET status=?,reviewed_at=? WHERE id=?",(status,now_iso(),pid)); await db.commit()
    async def selected_groups(self,uid):
        async with aiosqlite.connect(self.path) as db:
            c=await db.execute("SELECT chat_id,title FROM selected_groups WHERE user_id=? ORDER BY title",(uid,)); return await c.fetchall()
    async def replace_groups(self,uid,groups):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM selected_groups WHERE user_id=?",(uid,))
            await db.executemany("INSERT INTO selected_groups(user_id,chat_id,title) VALUES(?,?,?)",[(uid,a,b) for a,b in groups]); await db.commit()
    async def settings(self,uid):
        await self.ensure_user(uid)
        async with aiosqlite.connect(self.path) as db:
            c=await db.execute("SELECT fish_enabled,fish_sell_op,fish_sell_value,fish_feed_op,fish_feed_value,fish_fridge,hop_enabled,hop_minutes,withdraw_minutes,game_enabled,game_count,rescue_enabled FROM settings WHERE user_id=?",(uid,)); r=await c.fetchone()
            if not r:
                await db.execute("INSERT INTO settings(user_id) VALUES(?)",(uid,)); await db.commit()
                return await self.settings(uid)
            keys=["fish_enabled","fish_sell_op","fish_sell_value","fish_feed_op","fish_feed_value","fish_fridge","hop_enabled","hop_minutes","withdraw_minutes","game_enabled","game_count","rescue_enabled"]
            return dict(zip(keys,r))
    async def set_setting(self,uid,key,value):
        await self.ensure_user(uid)
        allowed={"fish_enabled","fish_sell_op","fish_sell_value","fish_feed_op","fish_feed_value","fish_fridge","hop_enabled","hop_minutes","withdraw_minutes","game_enabled","game_count","rescue_enabled"}
        if key not in allowed: raise ValueError("bad setting")
        async with aiosqlite.connect(self.path) as db:
            await db.execute(f"UPDATE settings SET {key}=? WHERE user_id=?",(value,uid)); await db.commit()
    async def pending_payment(self,uid):
        async with aiosqlite.connect(self.path) as db:
            c=await db.execute("SELECT id,days,amount FROM payments WHERE user_id=? AND status='pending' ORDER BY id DESC LIMIT 1",(uid,)); return await c.fetchone()
