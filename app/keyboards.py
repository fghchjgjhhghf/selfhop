from aiogram.types import InlineKeyboardButton, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def kb(rows):
    b = InlineKeyboardBuilder()
    for text, data in rows:
        b.button(text=text, callback_data=data)
    b.adjust(1)
    return b.as_markup()


def force_join(urls):
    b = InlineKeyboardBuilder()
    for i, url in enumerate(urls, 1):
        b.button(text=f"عضویت در کانال {i}", url=url)
    b.button(text="بررسی عضویت ✓", callback_data="join_check")
    b.adjust(1)
    return b.as_markup()


def phone_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="ارسال شماره تلفن من", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def code_kb(code=""):
    b = InlineKeyboardBuilder()
    for row in (("1", "2", "3"), ("4", "5", "6"), ("7", "8", "9")):
        for d in row:
            b.button(text=d, callback_data=f"code:{d}")
    b.button(text="⌫ حذف", callback_data="code:back")
    b.button(text="0", callback_data="code:0")
    b.button(text="پاک‌کردن", callback_data="code:clear")
    b.button(text="✅ ورود", callback_data="code:submit")
    b.button(text="↩️ انصراف", callback_data="code:cancel")
    b.adjust(3, 3, 3, 3, 2, 1)
    return b.as_markup()


def main_kb(ready=False):
    # Requested order: setup -> self settings -> subscription -> settings.
    rows = [("🚀 راه‌اندازی سلف", "setup")]
    if ready:
        rows.append(("🤖 تنظیمات سلف", "self"))
    else:
        rows.append(("🤖 تنظیمات سلف", "self"))
    rows += [
        ("💳 اشتراک", "subscription"),
        ("⚙️ تنظیمات", "settings"),
        ("💬 پشتیبانی", "support"),
    ]
    return kb(rows)


def subscription_kb(services):
    rows = []
    for service_id, name, days, price, *_ in services:
        rows.append((f"{name} — {days} روز — {price:,} تومان", f"buy:{service_id}"))
    rows.append(("↩️ بازگشت", "home"))
    return kb(rows)


def self_kb():
    return kb([
        ("👥 لیست گپ‌ها", "groups"),
        ("🐾 تنظیم هاپ", "hop"),
        ("🎣 تنظیم ماهی", "fish"),
        ("💰 برداشت هاپو", "withdraw"),
        ("🎰 تنظیم بازی", "game"),
        ("↩️ بازگشت", "home"),
    ])


def settings_kb():
    return kb([
        ("📱 راه‌اندازی / اتصال مجدد", "setup"),
        ("🚪 خروج از اکانت تلگرام", "logout"),
        ("🗑 حذف سلف", "delete_self"),
        ("↩️ بازگشت", "home"),
    ])


def hop_kb(s):
    b = InlineKeyboardBuilder()
    for n in (5, 10, 15, 20):
        b.button(text=f"{n} دقیقه + ۲۰ ثانیه", callback_data=f"hopm:{n}")
    b.button(text=f"هاپ: {'ON 🟢' if s['hop_enabled'] else 'OFF 🔴'}", callback_data="hoptoggle")
    b.button(text="↩️ بازگشت", callback_data="self")
    b.adjust(2, 1, 1)
    return b.as_markup()


def fish_kb(s):
    def label(name, op, val):
        state = "—" if op == "none" else (f"{'≥' if op == 'gte' else '≤'} {val}")
        return f"{name}: {state}"
    return kb([
        (f"🎣 ماهی: {'ON 🟢' if s['fish_enabled'] else 'OFF 🔴'} | هر {s['fish_minutes']} دقیقه", "fishtoggle"),
        ("🎣 دریافت ماهی همین الان", "fishget"),
        (label("فروش", s["fish_sell_op"], s["fish_sell_value"]), "fishrule:sell"),
        (label("بده هاپو بخوره", s["fish_feed_op"], s["fish_feed_value"]), "fishrule:feed"),
        (f"یخچال: {'ON 🟢' if s['fish_fridge'] else 'OFF 🔴'}", "fishfridge"),
        ("↩️ بازگشت", "self"),
    ])


def fish_rule_kb(kind):
    return kb([
        ("بیشتر یا مساوی ≥", f"fop:{kind}:gte"),
        ("کمتر یا مساوی ≤", f"fop:{kind}:lte"),
        ("بدون شرط", f"fop:{kind}:none"),
        ("↩️ بازگشت", "fish"),
    ])


def fish_numbers(kind, op):
    return kb([(str(i), f"fval:{kind}:{op}:{i}") for i in range(1, 7)] + [("↩️ بازگشت", "fish")])


def game_kb(s):
    return kb([
        (f"تعداد فعلی: {s['game_count']}", "gameinfo"),
        ("۱۰۰", "gamecount:100"),
        ("۲۰۰", "gamecount:200"),
        ("۳۰۰", "gamecount:300"),
        ("↩️ بازگشت", "self"),
    ])


def group_kb(groups, selected):
    b = InlineKeyboardBuilder()
    for gid, title in groups:
        mark = "☑️" if gid in selected else "⬜"
        b.button(text=f"{mark} {title[:35]}", callback_data=f"g:{gid}")
    b.button(text="ذخیره انتخاب‌ها", callback_data="gsave")
    b.button(text="↩️ بازگشت", callback_data="self")
    b.adjust(1)
    return b.as_markup()
