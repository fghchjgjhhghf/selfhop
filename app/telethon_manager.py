from __future__ import annotations
import asyncio, re
from pathlib import Path
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneNumberInvalidError

# App credentials used by this Telethon client.
# Users never enter or see these values; account login is still phone + code + optional 2FA.
API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"

class LoginResult:
    def __init__(self,status,message=""): self.status=status; self.message=message

class TelethonManager:
    def __init__(self,cfg,db):
        self.cfg=cfg; self.db=db
        self.clients: dict[int,TelegramClient]={}
        self.locks: dict[int,asyncio.Lock]={}
        self.pending: dict[int,str]={}
        self.command_handler=None
    def lock(self,uid): return self.locks.setdefault(uid,asyncio.Lock())
    def path(self,uid): return str(self.cfg.session_dir/f"{uid}")
    async def client(self,uid):
        c=self.clients.get(uid)
        if c is None:
            c=TelegramClient(self.path(uid),API_ID,API_HASH)
            await c.connect()
            self.clients[uid]=c

            async def outgoing_commands(event):
                if not self.command_handler:
                    return
                text=(event.raw_text or "").strip()
                m=re.match(r"^/(play|fish)(?:@\w+)?(?:\s+(.+))?$", text, re.I)
                if not m:
                    return
                try:
                    await self.command_handler(uid,int(event.chat_id),m.group(1).lower(),m.group(2))
                except Exception:
                    return
            c.add_event_handler(outgoing_commands, events.NewMessage(outgoing=True))
        return c
    async def is_ready(self,uid):
        try:
            c=await self.client(uid)
            return await c.is_user_authorized()
        except Exception: return False
    async def begin(self,uid,phone):
        async with self.lock(uid):
            c=await self.client(uid)
            try:
                sent = await c.send_code_request(phone)
                self.pending[uid]={"phone": phone, "phone_code_hash": sent.phone_code_hash}
                return LoginResult("code")
            except PhoneNumberInvalidError: return LoginResult("error","شماره تلفن معتبر نیست.")
            except Exception as e: return LoginResult("error",f"خطا در ارسال کد: {type(e).__name__}")
    async def verify_code(self,uid,code):
        async with self.lock(uid):
            c=await self.client(uid)
            pending=self.pending.get(uid)
            phone=(pending.get("phone") if isinstance(pending, dict) else pending) or await self.db.get_phone(uid)
            phone_code_hash=(pending.get("phone_code_hash") if isinstance(pending, dict) else None)
            if not phone: return LoginResult("error","شماره تلفن پیدا نشد؛ دوباره راه‌اندازی کنید.")
            try:
                kwargs={"phone": phone, "code": code}
                if phone_code_hash:
                    kwargs["phone_code_hash"] = phone_code_hash
                await c.sign_in(**kwargs)
                self.pending.pop(uid,None)
                return LoginResult("ready","حساب با موفقیت متصل شد.")
            except SessionPasswordNeededError:
                return LoginResult("password")
            except PhoneCodeInvalidError:
                return LoginResult("error","کد ورود اشتباه است.")
            except Exception as e:
                return LoginResult("error",f"ورود ناموفق بود: {type(e).__name__}")
    async def verify_password(self,uid,password):
        async with self.lock(uid):
            c=await self.client(uid)
            try:
                await c.sign_in(password=password)
                self.pending.pop(uid,None)
                return LoginResult("ready","تأیید دومرحله‌ای انجام شد و حساب متصل است.")
            except Exception:
                return LoginResult("error","رمز دومرحله‌ای نادرست است.")
    async def logout(self,uid):
        async with self.lock(uid):
            c=self.clients.get(uid)
            if c:
                try: await c.log_out()
                except Exception: pass
                try: await c.disconnect()
                except Exception: pass
                self.clients.pop(uid,None)
            p=Path(self.path(uid)+".session")
            for x in [p,Path(str(p)+"-journal")]:
                try: x.unlink()
                except FileNotFoundError: pass
    async def groups(self,uid):
        c=await self.client(uid)
        dialogs=[]
        async for d in c.iter_dialogs():
            if d.is_group or d.is_channel:
                # Avoid broadcast channels when possible; user can still select them if they have write access.
                if getattr(d.entity,"megagroup",False) or d.is_group:
                    dialogs.append((int(d.id),d.name or str(d.id)))
        return dialogs
    async def send_text(self,uid,chat_id,text):
        c=await self.client(uid); return await c.send_message(chat_id,text)
    async def recent_message(self,uid,chat_id,after_id=0,timeout=20):
        c=await self.client(uid)
        deadline=asyncio.get_running_loop().time()+timeout
        while asyncio.get_running_loop().time()<deadline:
            msgs=await c.get_messages(chat_id,limit=8)
            for m in msgs:
                if m.id<=after_id or not m.message or m.out:
                    continue
                # Prefer replies from the configured game bot, but accept any incoming
                # message when no username is configured.
                if self.cfg.game_bot_username:
                    try:
                        sender=await m.get_sender()
                        username=getattr(sender,"username",None)
                        if username and username.lower()==self.cfg.game_bot_username.lstrip("@").lower():
                            return m
                        continue
                    except Exception:
                        continue
                return m
            await asyncio.sleep(1)
        return None
    async def withdraw_points(self,uid,chat_id):
        """Send the trigger word first, then press the inline withdrawal button in its reply."""
        c=await self.client(uid)
        msgs=await c.get_messages(chat_id,limit=1)
        before=msgs[0].id if msgs else 0
        await self.send_text(uid,chat_id,"هاپو")
        m=await self.recent_message(uid,chat_id,before,max(5,self.cfg.fish_reply_timeout))
        if not m or not m.buttons:
            return False
        for i,row in enumerate(m.buttons):
            for j,button in enumerate(row):
                text=str(getattr(button,"text","") or "").strip()
                if "برداشت" not in text:
                    continue
                # Inline callback buttons have `data`; URL/reply buttons should not be clicked here.
                if getattr(button,"data",None) is None:
                    continue
                try:
                    await m.click(i=i,j=j)
                    return True
                except Exception:
                    continue
        return False

    async def parse_fish(self,uid,chat_id,after_id):
        m=await self.recent_message(uid,chat_id,after_id,self.cfg.fish_reply_timeout)
        if not m: return None
        text=m.message or ""
        # Persian/Latin digits and common labels.
        trans=str.maketrans("۰۱۲۳۴۵۶۷۸۹","0123456789")
        text=text.translate(trans)
        pats=[r"ارزش\s*غذایی\s*[:：]\s*([0-9]+)",r"ارزش\s*[:：]\s*([0-9]+)",r"غذایی\s*[:：]\s*([0-9]+)"]
        for p in pats:
            z=re.search(p,text)
            if z: return int(z.group(1)),m.id,text
        return None
