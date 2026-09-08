from __future__ import annotations
import asyncio, logging, os, re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from .config import load_config
from .db import DB
from .keyboards import *
from .telethon_manager import TelethonManager
from .automation import Automation

logging.basicConfig(level=logging.INFO)
log=logging.getLogger("selfbot-panel")
cfg=load_config()
cfg.data_dir.mkdir(parents=True,exist_ok=True)
db=DB(cfg.data_dir/"panel.sqlite3")
tg=TelethonManager(cfg,db)
auto=Automation(cfg,db,tg)
bot=Bot(cfg.bot_token)
dp=Dispatcher()

# Temporary UI state; durable account/subscription data remains in SQLite.
ui_state: dict[int,dict]={}

def admin(uid): return uid in cfg.admin_ids
async def entitled(uid): return admin(uid) or await auto.active(uid)

async def edit(cq,text,markup=None):
    try: await cq.message.edit_text(text,reply_markup=markup)
    except Exception:
        try: await cq.message.edit_reply_markup(reply_markup=markup)
        except Exception: pass
    await cq.answer()

async def membership_ok(uid):
    for ch in cfg.force_join:
        try:
            m=await bot.get_chat_member(ch.chat_id,uid)
            if m.status in ("left","kicked"): return False
        except Exception:
            return False
    return True

async def show_gate(m):
    await m.answer("برای استفاده از ربات، ابتدا در همه کانال‌های زیر عضو شوید و سپس «بررسی عضویت» را بزنید.",reply_markup=force_join(cfg.force_join))

async def home_message(target,uid):
    ready=await tg.is_ready(uid)
    text=("🏠 منوی اصلی\n\n"
          "از دکمه‌های زیر استفاده کنید.")
    markup=main_kb(ready)
    if isinstance(target,CallbackQuery): await edit(target,text,markup)
    else: await target.answer(text,reply_markup=markup)

@dp.message(CommandStart())
async def start(m:Message):
    uid=m.from_user.id; await db.ensure_user(uid)
    if cfg.force_join and not await membership_ok(uid):
        await show_gate(m); return
    await home_message(m,uid)

@dp.message(Command("help"))
async def help_cmd(m:Message):
    if cfg.force_join and not await membership_ok(m.from_user.id):
        await show_gate(m); return
    text="""📚 راهنمای استفاده

What can this robot do?
این ربات مدیریت اشتراک، اتصال سلف و اجرای تنظیمات خودکار روی گپ‌های انتخابی شما را انجام می‌دهد.

• اشتراک: وضعیت اعتبار را ببینید و یکی از پلن‌های ۳۰، ۶۰ یا ۹۰ روزه را بخرید.
• راه‌اندازی سلف: شماره خودتان را با دکمه ارسال شماره بدهید، سپس کد ورود و در صورت نیاز رمز دومرحله‌ای را وارد کنید.
• لیست گپ‌ها: گپ‌های قابل استفاده اکانت را می‌بینید و می‌توانید یک یا چند مورد را انتخاب کنید.
• هاپ: فاصله ارسال «هاپ» را انتخاب و آن را روشن/خاموش کنید.
• ماهی: دریافت ماهی را فعال کرده و برای فروش یا غذا دادن شرط ارزش غذایی تعیین کنید؛ ماهی‌ای که هیچ شرطی را نداشته باشد به یخچال می‌رود.
• برداشت هاپو: تعداد دقیقه را وارد کنید تا پیام برداشت به‌صورت دوره‌ای ارسال شود.
• بازی: فقط با یک گپ فعال می‌شود و تعداد ۱۰۰/۲۰۰/۳۰۰ را انتخاب می‌کنید.
• نجات خودکار: در صورت مشاهده پیام مربوط به هاپوی خیابانی ترسیده، سه تلاش نجات انجام می‌شود.
• تنظیمات: اتصال مجدد یا خروج/حذف سلف.

همه زمان‌ها تقریبی و وابسته به محدودیت‌های تلگرام هستند. /start برای بازگشت به منوی اصلی است."""
    await m.answer(text)

@dp.message(F.contact)
async def contact(m:Message):
    uid=m.from_user.id
    if cfg.force_join and not await membership_ok(uid): await show_gate(m); return
    if m.contact.user_id and m.contact.user_id != uid:
        await m.answer("لطفاً فقط شماره خودتان را با دکمه «ارسال شماره تلفن من» ارسال کنید."); return
    phone=m.contact.phone_number
    await db.set_phone(uid,phone)
    r=await tg.begin(uid,phone)
    if r.status=="code":
        await db.set_state(uid,"code")
        await m.answer("کد ورود تلگرام ارسال شد. کد را همین‌جا به صورت پیام متنی بفرستید.")
    else: await m.answer(r.message)

@dp.message()
async def text_input(m:Message):
    uid=m.from_user.id; state=await db.get_state(uid)
    if state=="code":
        r=await tg.verify_code(uid,m.text.strip())
        if r.status=="password":
            await db.set_state(uid,"password")
            await m.answer("رمز دومرحله‌ای تلگرام را وارد کنید.")
        elif r.status=="ready":
            await db.set_state(uid,"idle"); await home_message(m,uid); await auto.restart(uid)
        else: await m.answer(r.message)
        return
    if state=="password":
        r=await tg.verify_password(uid,m.text)
        if r.status=="ready":
            await db.set_state(uid,"idle"); await home_message(m,uid); await auto.restart(uid)
        else: await m.answer(r.message)
        return
    if state=="withdraw":
        try:
            n=int(m.text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹","0123456789")))
            if n<1 or n>10080: raise ValueError
            await db.set_setting(uid,"withdraw_minutes",n); await db.set_state(uid,"idle")
            await m.answer(f"⏱ برداشت هاپو روی هر {n} دقیقه تنظیم شد.")
        except ValueError: await m.answer("یک عدد بین ۱ تا ۱۰۰۸۰ دقیقه وارد کنید.")
        return
    await m.answer("از دکمه‌های منو استفاده کنید یا /help را بزنید.")

@dp.callback_query()
async def callbacks(cq:CallbackQuery):
    uid=cq.from_user.id; data=cq.data or ""
    if cfg.force_join and data!="join_check" and not await membership_ok(uid):
        await edit(cq,"ابتدا در همه کانال‌های الزامی عضو شوید.",force_join(cfg.force_join)); return
    if data=="join_check":
        if await membership_ok(uid): await edit(cq,"عضویت تأیید شد.",main_kb(await tg.is_ready(uid)))
        else: await edit(cq,"هنوز عضویت همه کانال‌ها تأیید نشده است.",force_join(cfg.force_join))
        return
    if data=="home": await home_message(cq,uid); return
    if data=="support": await edit(cq,"پشتیبانی\n\nبرای ارتباط با پشتیبانی از دکمه زیر استفاده کنید.",kb([("💬 ورود به پشتیبانی",cfg.support_url),("↩️ بازگشت","home")])); return
    if data=="subscription":
        exp,ok=await db.get_subscription(uid)
        status="فعال" if (admin(uid) or await entitled(uid)) else "غیرفعال"
        expiry=exp.replace("T"," ")[:19] if exp else "—"
        await edit(cq,f"💳 اشتراک\n\nوضعیت: {status}\nپایان اعتبار (UTC): {expiry}\nشناسه حساب: `{uid}`\n\nپلن موردنظر را انتخاب کنید.",sub_kb()); return
    if data.startswith("buy:"):
        _,days,amount=data.split(":")
        days=int(days); amount=int(amount)
        pid=await db.create_payment(uid,days,amount); ui_state[uid]={"payment_id":pid}
        await db.set_state(uid,"receipt")
        await edit(cq,f"💰 پلن {days} روزه\n\nمبلغ: {amount:,} تومان\nشماره کارت:\n`{cfg.card_number}`\n\nپس از واریز، عکس رسید را همین‌جا ارسال کنید.")
        return
    if data=="self":
        if not await entitled(uid): await edit(cq,"سلف شما فعال نیست. ابتدا اشتراک معتبر و اتصال حساب را انجام دهید.",main_kb(await tg.is_ready(uid))); return
        await edit(cq,"🤖 پنل سلف\n\nیکی از بخش‌ها را انتخاب کنید.",self_kb()); return
    if data=="settings":
        await edit(cq,"⚙️ تنظیمات\n\nاز این بخش می‌توانید اتصال را مجدداً انجام دهید یا سلف را حذف/خروج کنید.",settings_kb()); return
    if data=="setup":
        if not admin(uid):
            exp,ok=await db.get_subscription(uid)
            if not ok: await edit(cq,"برای راه‌اندازی سلف، ابتدا یک اشتراک فعال تهیه کنید.",sub_kb()); return
        await db.set_state(uid,"setup")
        await edit(cq,"📱 راه‌اندازی سلف\n\nشماره تلفن همان اکانتی که می‌خواهید متصل شود را با دکمه پایین ارسال کنید.",None)
        # Reply keyboard cannot be attached to an edited message. Send one helper prompt.
        await bot.send_message(uid,"شماره تلفن را ارسال کنید:",reply_markup=phone_kb())
        await cq.answer(); return
    if data=="logout":
        await auto.stop(uid); await tg.logout(uid); await db.set_state(uid,"idle")
        await edit(cq,"از اکانت تلگرام خارج شدید. برای اتصال مجدد از «راه‌اندازی / اتصال مجدد» استفاده کنید.",settings_kb()); return
    if data=="delete_self":
        await auto.stop(uid); await tg.logout(uid)
        await db.set_state(uid,"idle")
        await edit(cq,"سلف حذف شد و نشست این کاربر پاک شد.",main_kb(False)); return
    if data=="groups":
        if not await entitled(uid): await edit(cq,"ابتدا اشتراک معتبر و سلف فعال لازم است.",main_kb(False)); return
        try: groups=await tg.groups(uid)
        except Exception: groups=[]
        selected={x[0] for x in await db.selected_groups(uid)}
        ui_state[uid]={"groups":groups,"selected":selected}
        text=f"👥 لیست گپ‌ها\n\nتعداد انتخاب‌شده: {len(selected)}\nبرای انتخاب/لغو روی گپ‌ها بزنید."
        await edit(cq,text,group_kb(groups,selected)); return
    if data.startswith("g:"):
        st=ui_state.setdefault(uid,{"groups":[],"selected":set()}); gid=int(data.split(":",1)[1])
        sel=st.setdefault("selected",set()); sel.symmetric_difference_update({gid})
        await edit(cq,f"👥 لیست گپ‌ها\n\nتعداد انتخاب‌شده: {len(sel)}",group_kb(st.get("groups",[]),sel)); return
    if data=="gsave":
        st=ui_state.get(uid,{}); sel=st.get("selected",set()); groups=st.get("groups",[])
        await db.replace_groups(uid,[(gid,title) for gid,title in groups if gid in sel])
        if len(sel)==0: await edit(cq,"هیچ گپی انتخاب نشده است.",self_kb()); return
        await auto.restart(uid)
        await edit(cq,f"انتخاب گپ‌ها ذخیره شد: {len(sel)} مورد.",self_kb()); return
    if data=="hop":
        await edit(cq,"🐾 تنظیم هاپ\n\nزمان را انتخاب کنید. هر بازه ۲۰ ثانیه زمان اضافه دارد.",hop_kb(await db.settings(uid))); return
    if data.startswith("hopm:"):
        await db.set_setting(uid,"hop_minutes",int(data.split(":")[1])); await edit(cq,"زمان هاپ ذخیره شد.",hop_kb(await db.settings(uid))); await auto.restart(uid); return
    if data=="hoptoggle":
        s=await db.settings(uid); await db.set_setting(uid,"hop_enabled",0 if s["hop_enabled"] else 1)
        await edit(cq,"وضعیت هاپ تغییر کرد.",hop_kb(await db.settings(uid))); await auto.restart(uid); return
    if data=="fish":
        await edit(cq,"🎣 تنظیمات ماهی\n\nشرط‌ها بر اساس ارزش غذایی ماهی هستند.",fish_kb(await db.settings(uid))); return
    if data=="fishget":
        await auto.fish_once(uid); await edit(cq,"درخواست ماهی انجام شد و در صورت دریافت پاسخ، قانون انتخاب‌شده اعمال می‌شود.",fish_kb(await db.settings(uid))); return
    if data.startswith("fishrule:"):
        await edit(cq,"نوع شرط را انتخاب کنید.",fish_rule_kb(data.split(":")[1])); return
    if data.startswith("fop:"):
        _,kind,op=data.split(":")
        if op=="none":
            await db.set_setting(uid,"fish_"+kind+"_op","none")
            await edit(cq,"شرط حذف شد.",fish_kb(await db.settings(uid)))
        else:
            await edit(cq,"عدد شرط را از ۱ تا ۶ انتخاب کنید.",fish_numbers(kind,op))
        return
    if data.startswith("fval:"):
        _,kind,op,val=data.split(":")
        await db.set_setting(uid,"fish_"+kind+"_op",op); await db.set_setting(uid,"fish_"+kind+"_value",int(val))
        await edit(cq,"شرط ذخیره شد.",fish_kb(await db.settings(uid))); return
    if data=="fishfridge":
        s=await db.settings(uid); await db.set_setting(uid,"fish_fridge",0 if s["fish_fridge"] else 1); await edit(cq,"وضعیت یخچال تغییر کرد.",fish_kb(await db.settings(uid))); return
    if data=="withdraw":
        await db.set_state(uid,"withdraw"); await edit(cq,"⏱ تعداد دقیقه را به صورت یک عدد بفرستید. پس از این مدت، پیام «برداشت هاپو» ارسال می‌شود."); return
    if data=="game":
        await edit(cq,"🎮 بازی\n\nتعداد ارسال را انتخاب کنید. این حالت فقط وقتی دقیقاً یک گپ انتخاب شده باشد فعال می‌شود.",game_kb(await db.settings(uid))); return
    if data.startswith("gamecount:"):
        n=int(data.split(":")[1]); groups=await db.selected_groups(uid)
        if len(groups)!=1: await edit(cq,"برای فعال‌سازی بازی باید دقیقاً یک گپ انتخاب شده باشد.",game_kb(await db.settings(uid))); return
        await db.set_setting(uid,"game_count",n); await edit(cq,"تعداد بازی ذخیره شد.",game_kb(await db.settings(uid))); return
    if data=="gametoggle":
        groups=await db.selected_groups(uid)
        if len(groups)!=1: await edit(cq,"بازی فقط با یک گپ قابل فعال‌سازی است.",game_kb(await db.settings(uid))); return
        s=await db.settings(uid); await db.set_setting(uid,"game_enabled",0 if s["game_enabled"] else 1); await edit(cq,"وضعیت بازی تغییر کرد.",game_kb(await db.settings(uid))); await auto.restart(uid); return
    await cq.answer("گزینه ناشناخته است.")

@dp.message(F.photo)
async def receipt(m:Message):
    uid=m.from_user.id
    if await db.get_state(uid)!="receipt": return
    p=await db.pending_payment(uid)
    if not p: await m.answer("درخواست پرداخت فعالی ندارید."); return
    pid,days,amount=p
    await db.set_receipt(pid,m.photo[-1].file_id); await db.set_state(uid,"idle")
    caption=f"🧾 رسید پرداخت\nکاربر: `{uid}`\nپلن: {days} روز\nمبلغ: {amount:,} تومان\nPayment ID: `{pid}`"
    kb_admin=InlineKeyboardBuilder(); kb_admin.button(text="تأیید پرداخت ✅",callback_data=f"payok:{pid}"); kb_admin.button(text="رد پرداخت ❌",callback_data=f"payno:{pid}"); kb_admin.adjust(2)
    admins=cfg.payment_admin_ids or cfg.admin_ids
    for aid in admins:
        try: await bot.send_photo(aid,m.photo[-1].file_id,caption=caption,reply_markup=kb_admin.as_markup())
        except Exception: log.exception("payment admin send failed")
    await m.answer("رسید برای پشتیبانی ارسال شد. پس از بررسی، نتیجه برای شما ارسال می‌شود.")

@dp.callback_query(F.data.startswith("payok:"))
async def payment_ok(cq:CallbackQuery):
    if cq.from_user.id not in (cfg.payment_admin_ids|cfg.admin_ids): await cq.answer("دسترسی ندارید.",show_alert=True); return
    pid=int(cq.data.split(":")[1]); p=await db.payment(pid)
    if not p or p[4]!="pending": await cq.answer("این پرداخت قبلاً بررسی شده است.",show_alert=True); return
    _,uid,days,amount,_,_=p
    exp,active=await db.get_subscription(uid); now=datetime.now(timezone.utc)
    base=now
    if exp:
        try: base=max(base,datetime.fromisoformat(exp))
        except ValueError: pass
    newexp=(base+timedelta(days=days)).isoformat()
    await db.set_subscription(uid,newexp,1); await db.review_payment(pid,"approved")
    await cq.message.edit_reply_markup(reply_markup=None); await cq.answer("پرداخت تأیید شد.")
    await bot.send_message(uid,f"✅ پرداخت شما تأیید شد.\nاشتراک {days} روزه فعال شد.\nپایان اعتبار: {newexp[:19].replace('T',' ')} UTC")
    if await tg.is_ready(uid): await auto.restart(uid)

@dp.callback_query(F.data.startswith("payno:"))
async def payment_no(cq:CallbackQuery):
    if cq.from_user.id not in (cfg.payment_admin_ids|cfg.admin_ids): await cq.answer("دسترسی ندارید.",show_alert=True); return
    pid=int(cq.data.split(":")[1]); p=await db.payment(pid)
    if not p or p[4]!="pending": await cq.answer("این پرداخت قبلاً بررسی شده است.",show_alert=True); return
    uid=p[1]; await db.review_payment(pid,"rejected"); await cq.message.edit_reply_markup(reply_markup=None); await cq.answer("رد شد.")
    await bot.send_message(uid,"❌ رسید پرداخت تأیید نشد. در صورت اشتباه، دوباره از بخش اشتراک اقدام کنید.")

async def health(_):
    return web.json_response({"ok":True,"service":"telegram-self-panel"})

async def run_http():
    app=web.Application(); app.router.add_get("/",health); app.router.add_get("/health",health)
    runner=web.AppRunner(app); await runner.setup()
    site=web.TCPSite(runner,"0.0.0.0",cfg.port); await site.start()
    log.info("HTTP health server on %s",cfg.port)

async def main():
    await db.init()
    await run_http()
    await dp.start_polling(bot,allowed_updates=dp.resolve_used_update_types())

if __name__=="__main__":
    asyncio.run(main())
