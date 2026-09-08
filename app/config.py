from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def csv_ints(value: str) -> set[int]:
    out: set[int] = set()
    for x in (value or "").split(","):
        x = x.strip()
        if x:
            try:
                out.add(int(x))
            except ValueError:
                pass
    return out


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Channel:
    url: str


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: set[int]
    payment_admin_ids: set[int]
    support_url: str
    card_number: str
    card_number_name: str
    price_30: int
    price_60: int
    price_90: int
    game_bot_username: str
    game_emoji: str
    fish_reply_timeout: int
    web_admin_password: str
    data_dir: Path
    port: int

    @property
    def session_dir(self) -> Path:
        p = self.data_dir / "sessions"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def upload_dir(self) -> Path:
        p = self.data_dir / "uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p


def load_config() -> Config:
    required = ["BOT_TOKEN"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))

    return Config(
        bot_token=os.environ["BOT_TOKEN"],
        admin_ids=csv_ints(os.getenv("ADMIN_IDS", "")),
        payment_admin_ids=csv_ints(os.getenv("PAYMENT_ADMIN_IDS", "")),
        support_url=os.getenv("SUPPORT_URL", "https://t.me/"),
        card_number=os.getenv("CARD_NUMBER", ""),
        card_number_name=os.getenv("CARD_NUMBER_NAME", ""),
        price_30=env_int("PRICE_30", 30000),
        price_60=env_int("PRICE_60", 60000),
        price_90=env_int("PRICE_90", 90000),
        game_bot_username=os.getenv("GAME_BOT_USERNAME", "@Woofieqbot"),
        game_emoji=os.getenv("GAME_EMOJI", "🎰"),
        fish_reply_timeout=env_int("FISH_REPLY_TIMEOUT", 20),
        web_admin_password=os.getenv("WEB_ADMIN_PASSWORD", "admin"),
        data_dir=Path(os.getenv("DATA_DIR", "/data")),
        port=env_int("PORT", 8080),
    )
