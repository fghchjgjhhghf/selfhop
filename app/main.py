from __future__ import annotations

import asyncio
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, CallbackQuery, Message, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .automation import Automation
from .config import load_config
from .db import DB
from .keyboards import *
from .telethon_manager import TelethonManager

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("selfbot-panel")

cfg = load_config()
cfg.data_dir.mkdir(parents=True, exist_ok=True)
db = DB(cfg.data_dir / "panel.sqlite3")
tg = TelethonManager(cfg, db)
auto = Automation(cfg, db, tg)
tg.command_handler = auto.handle_self_command
bot = Bot(cfg.bot_token)
dp = Dispatcher()

ui_state: dict[int, dict] = {}
web_sessions: dict[str, float] = {}
payment_lock = asyncio.Lock()


def admin(uid: int) -> bool:
    return uid in cfg.admin_ids


async def entitled(uid: int) -> bool:
    return admin(uid) or await auto.active(uid)


async def edit(cq: CallbackQuery, text: str, markup=None):
    try:
        await cq.message.edit_text(text, reply_markup=markup)
    except Exception:
        try:
            await cq.message.edit_reply_markup(reply_markup=markup)
        except Exception:
            pass
    await cq.answer()


def channel_target(url: str) -> str | None:
    """Turn public t.me channel URLs into a Bot API chat target.

    Private invite links (+hash) do not expose a chat id through the Bot API,
    so they cannot be used for reliable getChatMember checks with only the URL.
    """
    url = url.strip()
    m = re.fullmatch(r"https?://t\.me/([A-Za-z0-9_]{4,})/?(?:\?.*)?", url)
    if m:
        return "@" + m.group(1)
    m = re.fullmatch(r"@([A-Za-z0-9_]{4,})", url)
    return url if m else None


async def membership_ok(uid: int) -> bool:
    for url in cfg.force_join_urls:
        target = channel_target(url)
        if not target:
            log.error("FORCE_JOIN_CHANNELS URL is not a public channel link: %s", url)
            return False
        try:
            me = await bot.get_chat_member(target, uid)
            if me.status in ("left", "kicked"):
                return False
        except Exception:
            log.exception("Force-join membership check failed for %s", target)
            return False
    return True


async def show_gate(m: Message):
    await m.answer(
        "برای استفاده از ربات، ابتدا در همه کانال‌های زیر عضو شوید و سپس «بررسی عضویت» را بزنید.",
        reply_markup=force_join(cfg.force_join_urls),
    )


async def home_message(target, uid: int):
    ready = await tg.is_ready(uid)
    text = "🏠 منوی اصلی\n\nترتیب کار: راه‌اندازی سلف → تنظیمات سلف → اشتراک → تنظیمات"
    markup = main_kb(ready)
    if isinstance(target, CallbackQuery):
        await edit(target, text, markup)
    else:
        await target.answer(text, reply_markup=markup)


@dp.message(CommandStart())
async def start(m: Message):
    uid = m.from_user.id
    await db.ensure_user(uid, m.from_user.first_name, m.from_user.last_name, m.from_user.username)
    if cfg.force_join_urls and not await membership_ok(uid):
        await show_gate(m)
        return
    await home_message(m, uid)


@dp.message(Command("menu"))
async def menu_cmd(m: Message):
    uid = m.from_user.id
    await db.ensure_user(uid, m.from_user.first_name, m.from_user.last_name, m.from_user.username)
    if cfg.force_join_urls and not await membership_ok(uid):
        await show_gate(m)
        return
    await home_message(m, uid)


@dp.message(Command("subscription", "subscribe"))
async def subscription_cmd(m: Message):
    await db.ensure_user(m.from_user.id, m.from_user.first_name, m.from_user.last_name, m.from_user.username)
    uid = m.from_user.id
    if cfg.force_join_urls and not await membership_ok(uid):
        await show_gate(m)
        return
    services = await db.list_services(active_only=True)
    exp, ok = await db.get_subscription(uid)
    expiry = exp.replace("T", " ")[:19] if exp else "—"
    status = "فعال" if (admin(uid) or ok) else "غیرفعال"
    await m.answer(
        f"💳 اشتراک\n\nوضعیت: {status}\nپایان اعتبار: {expiry} UTC\nشناسه خریدار: `{uid}`\n\nسرویس را انتخاب کنید.",
        reply_markup=subscription_kb(services),
    )


@dp.message(Command("setup"))
async def setup_cmd(m: Message):
    uid = m.from_user.id
    if cfg.force_join_urls and not await membership_ok(uid):
        await show_gate(m)
        return
    await db.ensure_user(uid, m.from_user.first_name, m.from_user.last_name, m.from_user.username)
    await begin_setup(m, uid)


@dp.message(Command("settings"))
async def settings_cmd(m: Message):
    uid = m.from_user.id
    if cfg.force_join_urls and not await membership_ok(uid):
        await show_gate(m)
        return
    await db.ensure_user(uid, m.from_user.first_name, m.from_user.last_name, m.from_user.username)
    await m.answer("⚙️ تنظیمات", reply_markup=settings_kb())


@dp.message(Command("fish"))
async def fish_cmd(m: Message):
    uid = m.from_user.id
    if cfg.force_join_urls and not await membership_ok(uid):
        await show_gate(m)
        return
    if not await entitled(uid):
        await m.answer("ابتدا اشتراک معتبر و سلف فعال لازم است.")
        return
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) == 1:
        await db.set_state(uid, "fish_interval")
        await m.answer("🎣 چند دقیقه یک‌بار «ماهی» ارسال شود؟ عددی بین ۱ تا ۱۰۰۸۰ وارد کنید.")
        return
    try:
        minutes = int(parts[1].translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
        if minutes < 1 or minutes > 10080:
            raise ValueError
        await db.set_setting(uid, "fish_minutes", minutes)
        await db.set_setting(uid, "fish_enabled", 1)
        await db.set_state(uid, "idle")
        await auto.restart(uid)
        await m.answer(f"🎣 ماهی فعال شد؛ هر {minutes} دقیقه یک‌بار ارسال می‌شود.")
    except ValueError:
        await m.answer("یک عدد بین ۱ تا ۱۰۰۸۰ دقیقه وارد کنید.")


@dp.message(Command("withdraw"))
async def withdraw_cmd(m: Message):
    uid = m.from_user.id
    if cfg.force_join_urls and not await membership_ok(uid):
        await show_gate(m)
        return
    if not await entitled(uid):
        await m.answer("ابتدا اشتراک معتبر و سلف فعال لازم است.")
        return
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) == 1:
        await db.set_state(uid, "withdraw")
        await m.answer("💰 چند دقیقه یک‌بار برداشت انجام شود؟ عددی بین ۱ تا ۱۰۰۸۰ وارد کنید.")
        return
    try:
        minutes = int(parts[1].translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
        if minutes < 1 or minutes > 10080:
            raise ValueError
        await db.set_setting(uid, "withdraw_minutes", minutes)
        await db.set_state(uid, "idle")
        await auto.restart(uid)
        await m.answer(f"💰 برداشت فعال شد؛ هر {minutes} دقیقه یک‌بار «هاپو» ارسال و دکمه برداشت کلیک می‌شود.")
    except ValueError:
        await m.answer("یک عدد بین ۱ تا ۱۰۰۸۰ دقیقه وارد کنید.")


@dp.message(Command("play"))
async def play_cmd(m: Message):
    uid = m.from_user.id
    if cfg.force_join_urls and not await membership_ok(uid):
        await show_gate(m)
        return
    if not await entitled(uid):
        await m.answer("ابتدا اشتراک معتبر و سلف فعال لازم است.")
        return
    s = await db.settings(uid)
    started = await auto.play(uid, m.chat.id, int(s["game_count"]))
    await m.answer(
        f"🎰 بازی با {s['game_count']} ارسال در همین گپ شروع شد و طی ۶۰ ثانیه پخش می‌شود."
        if started else "شروع بازی ممکن نشد."
    )


@dp.message(Command("help"))
async def help_cmd(m: Message):
    uid = m.from_user.id
    if cfg.force_join_urls and not await membership_ok(uid):
        await show_gate(m)
        return
    text = """📚 راهنمای دستورات

/start — شروع
/menu — باز کردن منوی کامل در هرجا
/setup — راه‌اندازی یا اتصال مجدد سلف
/settings — تنظیمات اتصال
/subscription — خرید/مدیریت اشتراک
/fish [دقیقه] — زمان‌بندی خودکار ماهی
/withdraw [دقیقه] — زمان‌بندی برداشت هاپو
/play — اجرای تعداد انتخاب‌شده 🎰 در همان گپ طی ۶۰ ثانیه
/help — همین راهنما

بعد از ارسال «ماهی»، نتیجه توسط ربات بازی خوانده می‌شود و شرط فروش/غذا/یخچال اعمال می‌شود.
برای برداشت، ابتدا «هاپو» ارسال می‌شود و سپس دکمه شیشه‌ای برداشت روی پاسخ کلیک می‌شود."""
    await m.answer(text)


async def begin_setup(target, uid: int):
    if not admin(uid):
        _, ok = await db.get_subscription(uid)
        if not ok:
            if isinstance(target, CallbackQuery):
                await edit(target, "برای راه‌اندازی سلف، ابتدا یک اشتراک فعال تهیه کنید.", subscription_kb(await db.list_services(True)))
            else:
                await target.answer("برای راه‌اندازی سلف، ابتدا یک اشتراک فعال تهیه کنید.", reply_markup=subscription_kb(await db.list_services(True)))
            return
    await db.set_state(uid, "setup")
    if isinstance(target, CallbackQuery):
        await edit(target, "📱 راه‌اندازی سلف\n\nشماره اکانتی که می‌خواهید وصل شود را با دکمه پایین ارسال کنید.")
        await bot.send_message(uid, "شماره تلفن را ارسال کنید:", reply_markup=phone_kb())
    else:
        await target.answer("📱 راه‌اندازی سلف\n\nشماره اکانت را با دکمه پایین ارسال کنید:", reply_markup=phone_kb())


@dp.message(F.contact)
async def contact(m: Message):
    uid = m.from_user.id
    if cfg.force_join_urls and not await membership_ok(uid):
        await show_gate(m)
        return
    if m.contact.user_id and m.contact.user_id != uid:
        await m.answer("لطفاً فقط شماره خودتان را ارسال کنید.")
        return
    phone = m.contact.phone_number
    await db.ensure_user(uid, m.from_user.first_name, m.from_user.last_name, m.from_user.username)
    await db.set_phone(uid, phone)
    r = await tg.begin(uid, phone)
    if r.status == "code":
        await db.set_state(uid, "code")
        ui_state.setdefault(uid, {})["login_code"] = ""
        await m.answer("🔐 کد ورود تلگرام ارسال شد.", reply_markup=ReplyKeyboardRemove())
        await m.answer("کد واردشده: `—`", reply_markup=code_kb(""))
    else:
        await m.answer(r.message)


@dp.callback_query(F.data.startswith("code:"))
async def code_callbacks(cq: CallbackQuery):
    uid = cq.from_user.id
    if await db.get_state(uid) != "code":
        await cq.answer("درخواست ورود فعالی ندارید.", show_alert=True)
        return
    action = (cq.data or "").split(":", 1)[1]
    st = ui_state.setdefault(uid, {})
    code = str(st.get("login_code", ""))
    if action.isdigit() and len(action) == 1:
        if len(code) < 8:
            code += action
        st["login_code"] = code
        shown = " ".join(code) if code else "—"
        await cq.message.edit_text(f"🔐 کد ورود\n\nکد واردشده: `{shown}`", reply_markup=code_kb(code))
        await cq.answer()
        return
    if action == "back":
        st["login_code"] = code[:-1]
        shown = " ".join(st["login_code"]) if st["login_code"] else "—"
        await cq.message.edit_text(f"🔐 کد ورود\n\nکد واردشده: `{shown}`", reply_markup=code_kb(st["login_code"]))
        await cq.answer()
        return
    if action == "clear":
        st["login_code"] = ""
        await cq.message.edit_text("🔐 کد ورود\n\nکد واردشده: `—`", reply_markup=code_kb(""))
        await cq.answer()
        return
    if action == "cancel":
        await db.set_state(uid, "idle")
        ui_state.pop(uid, None)
        await edit(cq, "ورود لغو شد.", settings_kb())
        return
    if action == "submit":
        if len(code) < 4:
            await cq.answer("کد کامل را وارد کنید.", show_alert=True)
            return
        r = await tg.verify_code(uid, code)
        if r.status == "password":
            await db.set_state(uid, "password")
            ui_state.pop(uid, None)
            await edit(cq, "🔐 کد درست است.\n\nرمز دومرحله‌ای تلگرام را به‌صورت پیام وارد کنید.")
        elif r.status == "ready":
            await db.set_state(uid, "idle")
            ui_state.pop(uid, None)
            await home_message(cq, uid)
            await auto.restart(uid)
        else:
            await cq.answer(r.message, show_alert=True)
        return
    await cq.answer()


@dp.message()
async def text_input(m: Message):
    uid = m.from_user.id
    await db.ensure_user(uid, m.from_user.first_name, m.from_user.last_name, m.from_user.username)
    state = await db.get_state(uid)
    if state == "code":
        r = await tg.verify_code(uid, (m.text or "").strip())
        if r.status == "password":
            await db.set_state(uid, "password")
            await m.answer("رمز دومرحله‌ای تلگرام را وارد کنید.")
        elif r.status == "ready":
            await db.set_state(uid, "idle")
            await home_message(m, uid)
            await auto.restart(uid)
        else:
            await m.answer(r.message)
        return
    if state == "password":
        r = await tg.verify_password(uid, m.text or "")
        if r.status == "ready":
            await db.set_state(uid, "idle")
            await home_message(m, uid)
            await auto.restart(uid)
        else:
            await m.answer(r.message)
        return
    if state == "fish_interval":
        try:
            n = int((m.text or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
            if n < 1 or n > 10080:
                raise ValueError
            await db.set_setting(uid, "fish_minutes", n)
            await db.set_setting(uid, "fish_enabled", 1)
            await db.set_state(uid, "idle")
            await auto.restart(uid)
            await m.answer(f"🎣 ماهی فعال شد؛ هر {n} دقیقه یک‌بار ارسال می‌شود.")
        except ValueError:
            await m.answer("یک عدد بین ۱ تا ۱۰۰۸۰ دقیقه وارد کنید.")
        return
    if state == "withdraw":
        try:
            n = int((m.text or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
            if n < 1 or n > 10080:
                raise ValueError
            await db.set_setting(uid, "withdraw_minutes", n)
            await db.set_state(uid, "idle")
            await auto.restart(uid)
            await m.answer(f"💰 برداشت فعال شد؛ هر {n} دقیقه یک‌بار اجرا می‌شود.")
        except ValueError:
            await m.answer("یک عدد بین ۱ تا ۱۰۰۸۰ دقیقه وارد کنید.")
        return
    await m.answer("از /menu یا دکمه‌های منو استفاده کنید.")


@dp.callback_query()
async def callbacks(cq: CallbackQuery):
    uid = cq.from_user.id
    data = cq.data or ""
    if cfg.force_join_urls and data != "join_check" and not await membership_ok(uid):
        await edit(cq, "ابتدا در همه کانال‌های الزامی عضو شوید.", force_join(cfg.force_join_urls))
        return
    if data == "join_check":
        if await membership_ok(uid):
            await edit(cq, "عضویت تأیید شد ✅", main_kb(await tg.is_ready(uid)))
        else:
            await edit(cq, "هنوز عضویت همه کانال‌ها تأیید نشده است.", force_join(cfg.force_join_urls))
        return
    if data == "home":
        await home_message(cq, uid)
        return
    if data == "support":
        b = InlineKeyboardBuilder()
        b.button(text="💬 ورود به پشتیبانی", url=cfg.support_url)
        b.button(text="↩️ بازگشت", callback_data="home")
        b.adjust(1)
        await edit(cq, "💬 پشتیبانی", b.as_markup())
        return
    if data == "setup":
        await begin_setup(cq, uid)
        return
    if data == "self":
        if not await entitled(uid) or not await tg.is_ready(uid):
            await edit(cq, "سلف شما فعال نیست. ابتدا اشتراک و راه‌اندازی را انجام دهید.", main_kb(await tg.is_ready(uid)))
            return
        await edit(cq, "🤖 تنظیمات سلف\n\nبخش موردنظر را انتخاب کنید.", self_kb())
        return
    if data == "subscription":
        services = await db.list_services(active_only=True)
        exp, ok = await db.get_subscription(uid)
        status = "فعال" if (admin(uid) or ok) else "غیرفعال"
        expiry = exp.replace("T", " ")[:19] if exp else "—"
        await edit(
            cq,
            f"💳 اشتراک\n\nوضعیت: {status}\nپایان اعتبار: {expiry} UTC\nشناسه خریدار: `{uid}`\n\nسرویس را انتخاب کنید.",
            subscription_kb(services),
        )
        return
    if data.startswith("buy:"):
        service_id = int(data.split(":", 1)[1])
        service = await db.service(service_id)
        if not service or not int(service[4]):
            await cq.answer("این سرویس در دسترس نیست.", show_alert=True)
            return
        _, name, days, amount, *_ = service
        pid = await db.create_payment(uid, service_id, int(days), int(amount))
        ui_state[uid] = {"payment_id": pid}
        await db.set_state(uid, "receipt")
        card_name = f"\nبه نام: {cfg.card_number_name}" if cfg.card_number_name else ""
        await edit(
            cq,
            f"💳 {name}\n\nمدت: {days} روز\nمبلغ: {int(amount):,} تومان\nشماره کارت:\n`{cfg.card_number}`{card_name}\n\nپس از واریز، عکس رسید را همین‌جا ارسال کنید.",
        )
        return
    if data == "settings":
        await edit(cq, "⚙️ تنظیمات اتصال و سلف", settings_kb())
        return
    if data == "logout":
        await auto.stop(uid)
        await tg.logout(uid)
        await db.set_state(uid, "idle")
        await edit(cq, "از اکانت تلگرام خارج شدید.", settings_kb())
        return
    if data == "delete_self":
        await auto.stop(uid)
        await tg.logout(uid)
        await db.set_state(uid, "idle")
        await edit(cq, "سلف حذف شد و نشست این کاربر پاک شد.", main_kb(False))
        return
    if data == "groups":
        if not await entitled(uid):
            await edit(cq, "ابتدا اشتراک معتبر و سلف فعال لازم است.", main_kb(False))
            return
        try:
            groups = await tg.groups(uid)
        except Exception:
            groups = []
        selected = {x[0] for x in await db.selected_groups(uid)}
        ui_state[uid] = {"groups": groups, "selected": selected}
        await edit(cq, f"👥 لیست گپ‌ها\n\nانتخاب‌شده: {len(selected)}", group_kb(groups, selected))
        return
    if data.startswith("g:"):
        st = ui_state.setdefault(uid, {"groups": [], "selected": set()})
        gid = int(data.split(":", 1)[1])
        sel = st.setdefault("selected", set())
        sel.symmetric_difference_update({gid})
        await edit(cq, f"👥 لیست گپ‌ها\n\nانتخاب‌شده: {len(sel)}", group_kb(st.get("groups", []), sel))
        return
    if data == "gsave":
        st = ui_state.get(uid, {})
        sel = st.get("selected", set())
        groups = st.get("groups", [])
        await db.replace_groups(uid, [(gid, title) for gid, title in groups if gid in sel])
        await auto.restart(uid)
        await edit(cq, f"انتخاب گپ‌ها ذخیره شد: {len(sel)} مورد.", self_kb())
        return
    if data == "hop":
        await edit(cq, "🐾 تنظیم هاپ", hop_kb(await db.settings(uid)))
        return
    if data.startswith("hopm:"):
        await db.set_setting(uid, "hop_minutes", int(data.split(":")[1]))
        await auto.restart(uid)
        await edit(cq, "زمان هاپ ذخیره شد.", hop_kb(await db.settings(uid)))
        return
    if data == "hoptoggle":
        s = await db.settings(uid)
        await db.set_setting(uid, "hop_enabled", 0 if s["hop_enabled"] else 1)
        await auto.restart(uid)
        await edit(cq, "وضعیت هاپ تغییر کرد.", hop_kb(await db.settings(uid)))
        return
    if data == "fish":
        await edit(cq, "🎣 تنظیمات ماهی\n\nبرای تغییر فاصله دکمه وضعیت را بزنید یا /fish را با دقیقه اجرا کنید.", fish_kb(await db.settings(uid)))
        return
    if data == "fishtoggle":
        s = await db.settings(uid)
        if s["fish_enabled"]:
            await db.set_setting(uid, "fish_enabled", 0)
            await auto.restart(uid)
            await edit(cq, "ماهی خاموش شد.", fish_kb(await db.settings(uid)))
        else:
            await db.set_state(uid, "fish_interval")
            await edit(cq, "⏱ عدد دقیقه را بفرستید (۱ تا ۱۰۰۸۰).", fish_kb(await db.settings(uid)))
        return
    if data == "fishget":
        await auto.fish_once(uid)
        await edit(cq, "درخواست ماهی انجام شد.", fish_kb(await db.settings(uid)))
        return
    if data.startswith("fishrule:"):
        await edit(cq, "نوع شرط را انتخاب کنید.", fish_rule_kb(data.split(":")[1]))
        return
    if data.startswith("fop:"):
        _, kind, op = data.split(":")
        if op == "none":
            await db.set_setting(uid, "fish_" + kind + "_op", "none")
            await edit(cq, "شرط حذف شد.", fish_kb(await db.settings(uid)))
        else:
            await edit(cq, "عدد شرط را از ۱ تا ۶ انتخاب کنید.", fish_numbers(kind, op))
        return
    if data.startswith("fval:"):
        _, kind, op, val = data.split(":")
        await db.set_setting(uid, "fish_" + kind + "_op", op)
        await db.set_setting(uid, "fish_" + kind + "_value", int(val))
        await edit(cq, "شرط ذخیره شد.", fish_kb(await db.settings(uid)))
        return
    if data == "fishfridge":
        s = await db.settings(uid)
        await db.set_setting(uid, "fish_fridge", 0 if s["fish_fridge"] else 1)
        await edit(cq, "وضعیت یخچال تغییر کرد.", fish_kb(await db.settings(uid)))
        return
    if data == "withdraw":
        await db.set_state(uid, "withdraw")
        await edit(cq, "💰 تعداد دقیقه را بفرستید (۱ تا ۱۰۰۸۰). بعد از این فاصله، اول «هاپو» و سپس کلیک دکمه برداشت انجام می‌شود.")
        return
    if data == "game":
        await edit(cq, "🎰 بازی\n\nتعداد ارسال را انتخاب کنید. /play در همان گپی که دستور را می‌زنید اجرا می‌شود و ۶۰ ثانیه طول می‌کشد.", game_kb(await db.settings(uid)))
        return
    if data.startswith("gamecount:"):
        n = int(data.split(":")[1])
        await db.set_setting(uid, "game_count", n)
        await edit(cq, "تعداد بازی ذخیره شد. حالا /play را در هر گپی بفرستید.", game_kb(await db.settings(uid)))
        return
    if data == "gameinfo":
        await cq.answer("تعداد انتخاب‌شده طی ۶۰ ثانیه با ایموجی 🎰 اجرا می‌شود.", show_alert=True)
        return
    await cq.answer("گزینه ناشناخته است.")


@dp.message(F.photo)
async def receipt(m: Message):
    uid = m.from_user.id
    if await db.get_state(uid) != "receipt":
        return
    p = await db.pending_payment(uid)
    if not p:
        await m.answer("درخواست پرداخت فعالی ندارید.")
        return
    pid, days, amount = p
    await db.set_receipt(pid, m.photo[-1].file_id)
    await db.set_state(uid, "idle")
    caption = f"🧾 رسید پرداخت\nکاربر: `{uid}`\nنام: {m.from_user.full_name}\nشناسه پرداخت: `{pid}`\nمدت: {days} روز\nمبلغ: {amount:,} تومان\n\nهر ادمین که تأیید کند، اشتراک فعال می‌شود."
    kb_admin = InlineKeyboardBuilder()
    kb_admin.button(text="✅ تأیید پرداخت", callback_data=f"payok:{pid}")
    kb_admin.button(text="❌ رد پرداخت", callback_data=f"payno:{pid}")
    kb_admin.adjust(2)
    admins = cfg.payment_admin_ids or cfg.admin_ids
    for aid in admins:
        try:
            await bot.send_photo(aid, m.photo[-1].file_id, caption=caption, reply_markup=kb_admin.as_markup())
        except Exception:
            log.exception("payment admin send failed")
    await m.answer("✅ رسید برای هر دو ادمین ارسال شد. بعد از تأیید یکی از آن‌ها، اشتراک فعال می‌شود.")


@dp.callback_query(F.data.startswith("payok:"))
async def payment_ok(cq: CallbackQuery):
    if cq.from_user.id not in (cfg.payment_admin_ids | cfg.admin_ids):
        await cq.answer("دسترسی ندارید.", show_alert=True)
        return
    pid = int(cq.data.split(":", 1)[1])
    async with payment_lock:
        p = await db.payment(pid)
        if not p or p[5] != "pending":
            await cq.answer("این پرداخت قبلاً بررسی شده است.", show_alert=True)
            return
        _, uid, _service_id, days, amount, _status, _receipt = p
        exp, _active = await db.get_subscription(uid)
        now = datetime.now(timezone.utc)
        base = now
        if exp:
            try:
                parsed = datetime.fromisoformat(exp)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                base = max(base, parsed)
            except ValueError:
                pass
        newexp = (base + timedelta(days=int(days))).isoformat()
        await db.set_subscription(uid, newexp, 1)
        changed = await db.review_payment(pid, "approved")
        if not changed:
            await cq.answer("این پرداخت قبلاً بررسی شده است.", show_alert=True)
            return
    await cq.message.edit_reply_markup(reply_markup=None)
    await cq.answer("پرداخت تأیید شد ✅")
    await bot.send_message(uid, f"✅ پرداخت شما تأیید شد.\nاشتراک {days} روزه فعال شد.\nپایان اعتبار: {newexp[:19].replace('T', ' ')} UTC")
    if await tg.is_ready(uid):
        await auto.restart(uid)


@dp.callback_query(F.data.startswith("payno:"))
async def payment_no(cq: CallbackQuery):
    if cq.from_user.id not in (cfg.payment_admin_ids | cfg.admin_ids):
        await cq.answer("دسترسی ندارید.", show_alert=True)
        return
    pid = int(cq.data.split(":", 1)[1])
    changed = await db.review_payment(pid, "rejected")
    if not changed:
        await cq.answer("این پرداخت قبلاً بررسی شده است.", show_alert=True)
        return
    p = await db.payment(pid)
    await cq.message.edit_reply_markup(reply_markup=None)
    await cq.answer("پرداخت رد شد")
    if p:
        await bot.send_message(p[1], "❌ رسید پرداخت تأیید نشد. در صورت اشتباه دوباره اقدام کنید.")


# -------------------------- Web admin panel --------------------------

HTML = r'''<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Woofie Admin</title>
<style>
:root{font-family:Vazirmatn,Inter,system-ui,sans-serif;color:#18202b;background:#f5f7fb}*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#f7f9fc,#edf2f9);min-height:100vh}.wrap{max-width:1180px;margin:0 auto;padding:28px}.card{background:#fff;border:1px solid #e7ebf2;border-radius:24px;box-shadow:0 18px 50px rgba(18,31,53,.10);padding:22px}.login{max-width:420px;margin:12vh auto}.brand{display:flex;gap:12px;align-items:center;margin-bottom:20px}.brand .logo{width:48px;height:48px;border-radius:16px;display:grid;place-items:center;background:#111827;color:#fff;font-size:24px}.brand h1{font-size:23px;margin:0}.muted{color:#718096}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.stat{padding:20px;border-radius:20px;background:#f8fafc;border:1px solid #edf1f6}.stat b{display:block;font-size:28px;margin-top:8px}.toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:18px 0}.btn{border:0;border-radius:14px;padding:11px 16px;cursor:pointer;background:#111827;color:#fff;font-weight:700}.btn.alt{background:#edf2f7;color:#1f2937}.btn.danger{background:#fee2e2;color:#991b1b}.row{display:flex;gap:10px;flex-wrap:wrap}.input{width:100%;padding:12px 14px;border:1px solid #dfe5ee;border-radius:14px;outline:none;background:#fff}.input:focus{border-color:#98a2b3;box-shadow:0 0 0 4px rgba(71,85,105,.08)}table{width:100%;border-collapse:separate;border-spacing:0 8px}th{font-size:13px;color:#718096;text-align:right;padding:8px}td{background:#fafbfc;padding:12px;border-top:1px solid #edf1f6;border-bottom:1px solid #edf1f6}td:first-child{border-right:1px solid #edf1f6;border-radius:0 12px 12px 0}td:last-child{border-left:1px solid #edf1f6;border-radius:12px 0 0 12px}.service{display:grid;grid-template-columns:2fr 1fr 1fr auto;gap:10px;align-items:center;margin-bottom:10px}.pill{display:inline-flex;padding:5px 9px;border-radius:999px;background:#eafaf0;color:#17663a;font-size:12px}.pill.off{background:#f3f4f6;color:#687080}.msg{position:fixed;left:20px;bottom:20px;background:#111827;color:#fff;padding:12px 16px;border-radius:14px;display:none}.top{display:flex;justify-content:space-between;align-items:center;gap:16px}.section{margin-top:20px}.small{font-size:12px}@media(max-width:800px){.grid{grid-template-columns:1fr}.service{grid-template-columns:1fr 1fr}.wrap{padding:14px}.card{border-radius:18px}}
</style></head><body><div id="app"></div><div id="msg" class="msg"></div>
<script>
const fa=n=>Number(n||0).toLocaleString('fa-IR');
function showMsg(t){const e=document.getElementById('msg');e.textContent=t;e.style.display='block';setTimeout(()=>e.style.display='none',2500)}
async function req(url,opt={}){const r=await fetch(url,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});if(r.status===401){renderLogin();throw new Error('unauthorized')}const raw=await r.text();let d;try{d=raw?JSON.parse(raw):{}}catch(e){throw new Error('پاسخ نامعتبر از سرور: '+raw.slice(0,120))}if(!r.ok)throw new Error((d&&d.error)||'خطا');return d}
function renderLogin(){document.getElementById('app').innerHTML=`<div class="wrap"><div class="card login"><div class="brand"><div class="logo">🐾</div><div><h1>Woofie Admin</h1><div class="muted">مدیریت اشتراک و فروش</div></div></div><form onsubmit="login(event)"><input class="input" id="pw" type="password" placeholder="رمز پنل" autofocus><button class="btn" style="width:100%;margin-top:12px">ورود</button></form></div></div>`}
async function login(e){e.preventDefault();try{await req('/login',{method:'POST',body:JSON.stringify({password:document.getElementById('pw').value})});load()}catch(x){showMsg(x.message)}}
async function logout(){await req('/logout',{method:'POST'});renderLogin()}
async function load(){try{const d=await req('/api/dashboard');render(d)}catch(x){if(x.message!=='unauthorized')showMsg(x.message)}}
function render(d){document.getElementById('app').innerHTML=`<div class="wrap"><div class="top"><div class="brand"><div class="logo">🐾</div><div><h1>Woofie Admin</h1><div class="muted">پنل مدرن مدیریت کاربران، سرویس‌ها و فروش</div></div></div><button class="btn alt" onclick="logout()">خروج</button></div><div class="grid"><div class="card stat"><div class="muted">کل کاربران</div><b>${fa(d.counts.users)}</b></div><div class="card stat"><div class="muted">اشتراک فعال</div><b>${fa(d.counts.active)}</b></div><div class="card stat"><div class="muted">فروش کل</div><b>${fa(d.sales_total)} <span class="small">تومان</span></b></div></div><div class="card section"><div class="toolbar"><div><h2 style="margin:0 0 5px">سرویس‌ها</h2><div class="muted">نام، تعداد روز و قیمت را ویرایش کنید؛ سرویس جدید به‌صورت پیش‌فرض ۳۰ روزه است.</div></div><button class="btn" onclick="addService()">+ افزودن سرویس</button></div><div id="services">${d.services.map(serviceRow).join('')}</div></div><div class="card section"><div class="toolbar"><div><h2 style="margin:0 0 5px">کاربران و فروش</h2><div class="muted">مجموع خرید تأییدشده هر کاربر نمایش داده می‌شود.</div></div><button class="btn alt" onclick="load()">↻ بروزرسانی</button></div>${usersTable(d.users)}</div></div>`}
function serviceRow(s){return `<div class="service"><input class="input" id="n${s.id}" value="${esc(s.name)}"><input class="input" id="d${s.id}" type="number" min="1" value="${s.days}"><input class="input" id="p${s.id}" type="number" min="0" value="${s.price}"><div class="row"><button class="btn" onclick="saveService(${s.id})">ذخیره</button><button class="btn danger" onclick="delService(${s.id})">حذف</button></div></div>`}
function usersTable(xs){return `<div style="overflow:auto"><table><thead><tr><th>کاربر</th><th>شناسه</th><th>فروش</th><th>اشتراک</th><th>ثبت</th></tr></thead><tbody>${xs.map(u=>`<tr><td><b>${esc((u.first_name+' '+u.last_name).trim()||u.username||'بدون نام')}</b><div class="muted small">${u.username?'@'+esc(u.username):''}</div></td><td>${u.user_id}</td><td>${fa(u.sales)} تومان</td><td>${u.active?'✅ فعال':'—'}<div class="muted small">${u.expires_at?u.expires_at.slice(0,19).replace('T',' '):''}</div></td><td class="small">${u.created_at.slice(0,19).replace('T',' ')}</td></tr>`).join('')}</tbody></table></div>`}
function esc(s){return String(s??'').replace(/[&<>'"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[m]))}
async function saveService(id){try{await req('/api/services/'+id,{method:'PUT',body:JSON.stringify({name:document.getElementById('n'+id).value,days:+document.getElementById('d'+id).value,price:+document.getElementById('p'+id).value,active:1})});showMsg('سرویس ذخیره شد');load()}catch(x){showMsg(x.message)}}
async function delService(id){if(!confirm('سرویس غیرفعال شود؟'))return;try{await req('/api/services/'+id,{method:'DELETE'});showMsg('سرویس غیرفعال شد');load()}catch(x){showMsg(x.message)}}
async function addService(){const name=prompt('نام سرویس','سرویس جدید');if(!name)return;const price=Number(prompt('قیمت (تومان)','0')||0);try{await req('/api/services',{method:'POST',body:JSON.stringify({name,days:30,price,active:1})});showMsg('سرویس اضافه شد');load()}catch(x){showMsg(x.message)}}
load();
</script></body></html>'''


async def read_json_object(request: web.Request) -> dict:
    """Safely decode a single JSON object from an HTTP request.

    The web UI only sends objects. Reading text first avoids aiohttp's generic
    JSON decoder surfacing confusing parser errors when a proxy/client sends
    an unexpected content type or trailing bytes.
    """
    raw = await request.text()
    if not raw.strip():
        return {}
    try:
        import json as _json
        data = _json.loads(raw)
    except Exception as exc:
        raise ValueError(f"JSON نامعتبر است: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("بدنه درخواست باید یک شیء JSON باشد")
    return data


def is_web_authed(request: web.Request) -> bool:
    token = request.cookies.get("woofie_admin")
    return bool(token and token in web_sessions)


async def web_home(request: web.Request):
    if not is_web_authed(request):
        return web.Response(text=HTML, content_type="text/html", charset="utf-8")
    return web.Response(text=HTML, content_type="text/html", charset="utf-8")


async def web_login(request: web.Request):
    try:
        data = await read_json_object(request)
    except Exception:
        data = {}
    password = str(data.get("password", ""))
    if not hmac.compare_digest(password, cfg.web_admin_password):
        return web.json_response({"error": "رمز اشتباه است"}, status=401)
    token = secrets.token_urlsafe(32)
    web_sessions[token] = asyncio.get_running_loop().time()
    resp = web.json_response({"ok": True})
    resp.set_cookie("woofie_admin", token, httponly=True, samesite="Lax", max_age=86400, secure=False)
    return resp


async def web_logout(request: web.Request):
    token = request.cookies.get("woofie_admin")
    web_sessions.pop(token, None)
    resp = web.json_response({"ok": True})
    resp.del_cookie("woofie_admin")
    return resp


async def web_dashboard(request: web.Request):
    if not is_web_authed(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    users_count, active_count, pending = await db.counts()
    rows = await db.list_users_with_sales()
    services = await db.list_services(active_only=False)
    users = [
        {
            "user_id": r[0], "first_name": r[1] or "", "last_name": r[2] or "", "username": r[3] or "",
            "created_at": r[4], "sales": int(r[5] or 0), "expires_at": r[6], "active": bool(r[7]),
        }
        for r in rows
    ]
    service_data = [
        {"id": r[0], "name": r[1], "days": int(r[2]), "price": int(r[3]), "active": bool(r[4])}
        for r in services
    ]
    return web.json_response({
        "counts": {"users": users_count, "active": active_count, "pending": pending},
        "sales_total": await db.sales_total(),
        "services": service_data,
        "users": users,
    }, ensure_ascii=False)


def validate_service_payload(data):
    name = str(data.get("name", "")).strip()
    days = int(data.get("days", 30))
    price = int(data.get("price", 0))
    active = 1 if bool(data.get("active", True)) else 0
    if not name or len(name) > 80:
        raise ValueError("نام سرویس نامعتبر است")
    if days < 1 or days > 3650:
        raise ValueError("مدت باید بین ۱ تا ۳۶۵۰ روز باشد")
    if price < 0:
        raise ValueError("قیمت نمی‌تواند منفی باشد")
    return name, days, price, active


async def web_create_service(request: web.Request):
    if not is_web_authed(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        name, days, price, active = validate_service_payload(await read_json_object(request))
        sid = await db.create_service(name, days, price, active)
        return web.json_response({"ok": True, "id": sid})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)


async def web_update_service(request: web.Request):
    if not is_web_authed(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        sid = int(request.match_info["service_id"])
        name, days, price, active = validate_service_payload(await read_json_object(request))
        if not await db.service(sid):
            return web.json_response({"error": "سرویس پیدا نشد"}, status=404)
        await db.update_service(sid, name, days, price, active)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)


async def web_delete_service(request: web.Request):
    if not is_web_authed(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        sid = int(request.match_info["service_id"])
        await db.delete_service(sid)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)


async def health(_):
    return web.json_response({"ok": True, "service": "telegram-self-panel"})


async def run_http():
    app = web.Application()
    app.router.add_get("/", web_home)
    app.router.add_get("/health", health)
    app.router.add_post("/login", web_login)
    app.router.add_post("/logout", web_logout)
    app.router.add_get("/api/dashboard", web_dashboard)
    app.router.add_post("/api/services", web_create_service)
    app.router.add_put("/api/services/{service_id}", web_update_service)
    app.router.add_delete("/api/services/{service_id}", web_delete_service)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", cfg.port)
    await site.start()
    log.info("Web admin panel on port %s", cfg.port)
    return runner


async def set_commands():
    commands = [
        BotCommand(command="start", description="شروع و منوی اصلی"),
        BotCommand(command="menu", description="باز کردن منو"),
        BotCommand(command="setup", description="راه‌اندازی سلف"),
        BotCommand(command="settings", description="تنظیمات"),
        BotCommand(command="subscription", description="اشتراک"),
        BotCommand(command="fish", description="زمان‌بندی ماهی"),
        BotCommand(command="withdraw", description="زمان‌بندی برداشت هاپو"),
        BotCommand(command="play", description="بازی ۶۰ ثانیه‌ای 🎰"),
        BotCommand(command="help", description="راهنما"),
    ]
    await bot.set_my_commands(commands)


async def main():
    await db.init((cfg.price_30, cfg.price_60, cfg.price_90))
    if cfg.force_join_urls:
        for url in cfg.force_join_urls:
            if not channel_target(url):
                log.error("Force join link cannot be checked by Bot API unless it is a public channel URL: %s", url)
    await set_commands()
    await run_http()
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
