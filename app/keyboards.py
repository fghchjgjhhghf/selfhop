from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

def kb(rows):
    b=InlineKeyboardBuilder()
    for text,data in rows: b.button(text=text,callback_data=data)
    b.adjust(1); return b.as_markup()

def force_join(channels):
    b=InlineKeyboardBuilder()
    for i,ch in enumerate(channels,1): b.button(text=f"عضویت در کانال {i}",url=ch.url)
    b.button(text="بررسی عضویت ✓",callback_data="join_check")
    b.adjust(1); return b.as_markup()

def phone_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="ارسال شماره تلفن من",request_contact=True)]],resize_keyboard=True,one_time_keyboard=True)

def main_kb(ready=False):
    rows=[("پشتیبانی","support"),("اشتراک","subscription")]
    if ready:
        rows += [("My Self / تنظیمات سلف","self"),("⚙️ تنظیمات","settings")]
    else:
        rows += [("راه‌اندازی سلف","setup"),("⚙️ تنظیمات","settings")]
    return kb(rows)

def sub_kb():
    return kb([("۱ ماهه — ۳۰٬۰۰۰ تومان","buy:30:30000"),("۲ ماهه — ۶۰٬۰۰۰ تومان","buy:60:60000"),("۳ ماهه — ۹۰٬۰۰۰ تومان","buy:90:90000"),("↩️ بازگشت","home")])

def self_kb():
    return kb([("لیست گپ‌ها","groups"),("تنظیم هاپ","hop"),("تنظیم ماهی","fish"),("برداشت هاپو","withdraw"),("بازی","game"),("↩️ بازگشت","home")])

def settings_kb():
    return kb([("راه‌اندازی / اتصال مجدد","setup"),("خروج از اکانت تلگرام","logout"),("حذف سلف","delete_self"),("↩️ بازگشت","home")])

def back(data="home"): return kb([("↩️ بازگشت",data)])

def hop_kb(s):
    b=InlineKeyboardBuilder()
    for n in (5,10,15,20): b.button(text=f"{n} دقیقه + ۲۰ ثانیه",callback_data=f"hopm:{n}")
    b.button(text=f"هاپ: {'ON 🟢' if s['hop_enabled'] else 'OFF 🔴'}",callback_data="hoptoggle")
    b.button(text="↩️ بازگشت",callback_data="self")
    b.adjust(2,1,1); return b.as_markup()

def fish_kb(s):
    def label(name,op,val):
        state="—" if op=="none" else (f"{'≥' if op=='gte' else '≤'} {val}")
        return f"{name}: {state}"
    return kb([
        ("🎣 دریافت ماهی","fishget"),
        (label("فروش",s["fish_sell_op"],s["fish_sell_value"]),"fishrule:sell"),
        (label("بده هاپو بخوره",s["fish_feed_op"],s["fish_feed_value"]),"fishrule:feed"),
        (f"یخچال: {'ON' if s['fish_fridge'] else 'OFF'}","fishfridge"),
        ("↩️ بازگشت","self")
    ])

def fish_rule_kb(kind):
    return kb([("بیشتر یا مساوی ≥","fop:"+kind+":gte"),("کمتر یا مساوی ≤","fop:"+kind+":lte"),("بدون شرط","fop:"+kind+":none"),("↩️ بازگشت","fish")])

def fish_numbers(kind,op):
    return kb([(str(i),f"fval:{kind}:{op}:{i}") for i in range(1,7)] + [("↩️ بازگشت","fish")])

def game_kb(s):
    return kb([("۱۰۰","gamecount:100"),("۲۰۰","gamecount:200"),("۳۰۰","gamecount:300"),(f"بازی: {'ON 🟢' if s['game_enabled'] else 'OFF 🔴'}","gametoggle"),("↩️ بازگشت","self")])

def group_kb(groups,selected):
    b=InlineKeyboardBuilder()
    for gid,title in groups:
        mark="☑️" if gid in selected else "⬜"
        b.button(text=f"{mark} {title[:35]}",callback_data=f"g:{gid}")
    b.button(text="ذخیره انتخاب‌ها",callback_data="gsave")
    b.button(text="↩️ بازگشت",callback_data="self")
    b.adjust(1); return b.as_markup()
