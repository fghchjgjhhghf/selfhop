from __future__ import annotations
import asyncio, random, re
from datetime import datetime, timezone
from .telethon_manager import TelethonManager

DIGIT_MAP=str.maketrans("۰۱۲۳۴۵۶۷۸۹","0123456789")

class Automation:
    def __init__(self,cfg,db,tg):
        self.cfg=cfg; self.db=db; self.tg=tg
        self.tasks: dict[int,list[asyncio.Task]]={}
    async def active(self,uid):
        if not await self.tg.is_ready(uid): return False
        if uid in self.cfg.admin_ids: return True
        exp,ok=await self.db.get_subscription(uid)
        if not ok or not exp: return False
        try:
            dt=datetime.fromisoformat(exp)
            if dt <= datetime.now(timezone.utc):
                await self.db.disable_subscription(uid); return False
        except ValueError: return False
        return True
    async def restart(self,uid):
        await self.stop(uid)
        if await self.active(uid):
            self.tasks[uid]=[
                asyncio.create_task(self.hop_loop(uid)),
                asyncio.create_task(self.withdraw_loop(uid)),
                asyncio.create_task(self.game_loop(uid)),
                asyncio.create_task(self.rescue_loop(uid)),
            ]
    async def stop(self,uid):
        for t in self.tasks.pop(uid,[]):
            t.cancel()
    async def _sleep_or_cancel(self,seconds):
        await asyncio.sleep(max(1,seconds))
    async def hop_loop(self,uid):
        while True:
            try:
                s=await self.db.settings(uid)
                delay=int(s["hop_minutes"])*60+20
                if not s["hop_enabled"] or not await self.active(uid):
                    await asyncio.sleep(5); continue
                await asyncio.sleep(delay)
                if not await self.active(uid): return
                groups=await self.db.selected_groups(uid)
                if groups:
                    chat=random.choice(groups)[0]
                    await self.tg.send_text(uid,chat,"هاپ")
            except asyncio.CancelledError: return
            except Exception: await asyncio.sleep(5)
    async def withdraw_loop(self,uid):
        last=None
        while True:
            try:
                s=await self.db.settings(uid); mins=s["withdraw_minutes"]
                if not mins or not await self.active(uid):
                    await asyncio.sleep(5); continue
                if last is None: last=asyncio.get_running_loop().time()
                await asyncio.sleep(2)
                if asyncio.get_running_loop().time()-last >= mins*60:
                    for chat,_ in await self.db.selected_groups(uid):
                        try: await self.tg.send_text(uid,chat,"برداشت هاپو")
                        except Exception: pass
                    last=asyncio.get_running_loop().time()
            except asyncio.CancelledError: return
            except Exception: await asyncio.sleep(5)
    async def game_loop(self,uid):
        last=0.0
        while True:
            try:
                if not await self.active(uid):
                    await asyncio.sleep(5); continue
                s=await self.db.settings(uid)
                groups=await self.db.selected_groups(uid)
                if s["game_enabled"] and len(groups)==1 and asyncio.get_running_loop().time()-last>=300:
                    chat=groups[0][0]
                    for _ in range(int(s["game_count"])):
                        try: await self.tg.send_text(uid,chat,self.cfg.game_emoji)
                        except Exception: break
                    last=asyncio.get_running_loop().time()
                await asyncio.sleep(5)
            except asyncio.CancelledError: return
            except Exception: await asyncio.sleep(5)
    async def rescue_loop(self,uid):
        last_ids={}
        phrase_re=re.compile(r"هاپوی خیابونی.*ترسیده.*کنار شهر.*پیدا شد",re.I|re.S)
        while True:
            try:
                if not await self.active(uid):
                    await asyncio.sleep(5); continue
                s=await self.db.settings(uid)
                if not s["rescue_enabled"]:
                    await asyncio.sleep(5); continue
                client=await self.tg.client(uid)
                for chat,_ in await self.db.selected_groups(uid):
                    msgs=await client.get_messages(chat,limit=8)
                    for m in reversed(msgs):
                        if m.id<=last_ids.get(chat,0): continue
                        last_ids[chat]=m.id
                        text=(m.message or "").translate(DIGIT_MAP)
                        if phrase_re.search(text) or ("ترسیده" in text and "نجات" in text):
                            for _ in range(3):
                                try: await self.tg.send_text(uid,chat,"تلاش برای نجات")
                                except Exception: break
                await asyncio.sleep(4)
            except asyncio.CancelledError: return
            except Exception: await asyncio.sleep(5)
    def match(self,value,op,threshold):
        if op=="gte": return value>=threshold
        if op=="lte": return value<=threshold
        return False
    async def fish_once(self,uid):
        if not await self.active(uid): return
        groups=await self.db.selected_groups(uid)
        if not groups: return
        s=await self.db.settings(uid)
        for chat,_ in groups:
            try:
                client=await self.tg.client(uid)
                msgs=await client.get_messages(chat,limit=1)
                before=msgs[0].id if msgs else 0
                await self.tg.send_text(uid,chat,"ماهی")
                result=await self.tg.parse_fish(uid,chat,before)
                if not result: continue
                value,_,_=result
                if self.match(value,s["fish_sell_op"],s["fish_sell_value"]):
                    await self.tg.send_text(uid,chat,"فروش ماهی")
                elif self.match(value,s["fish_feed_op"],s["fish_feed_value"]):
                    await self.tg.send_text(uid,chat,"بده هاپو بخوره")
                elif s["fish_fridge"]:
                    await self.tg.send_text(uid,chat,"بندازش تو یخچال")
            except Exception:
                continue
