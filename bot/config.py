"""Конфиг бота. Значения берутся из .env (см. .env.example)."""
import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

try:  # .env не обязателен: переменные можно задать и в системе
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except Exception:  # noqa: BLE001
    pass


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


@dataclass
class Config:
    token: str = field(default_factory=lambda: _env("BOT_TOKEN"))
    admin_ids: list[int] = field(
        default_factory=lambda: [int(x) for x in _env("ADMIN_IDS", "").replace(" ", "").split(",") if x]
    )
    db_path: Path = field(default_factory=lambda: Path(_env("DB_PATH", str(BASE_DIR / "salon.sqlite3"))))
    salon_name: str = field(default_factory=lambda: _env("SALON_NAME", "Салон красоты «Лотос»"))
    address: str = field(default_factory=lambda: _env("SALON_ADDRESS", "ул. Ленина, 15"))
    phone: str = field(default_factory=lambda: _env("SALON_PHONE", "+7 999 000-00-00"))
    open_hour: int = 10
    close_hour: int = 20
    slot_minutes: int = 30
    days_ahead: int = 7

    def persist_admin_ids(self) -> None:
        """Запоминает админов в .env, чтобы после перезапуска доступ не терялся."""
        env_path = BASE_DIR / ".env"
        lines = env_path.read_text("utf-8").splitlines() if env_path.exists() else []
        value = ",".join(str(i) for i in self.admin_ids)
        for i, line in enumerate(lines):
            if line.startswith("ADMIN_IDS="):
                lines[i] = f"ADMIN_IDS={value}"
                break
        else:
            lines.append(f"ADMIN_IDS={value}")
        env_path.write_text("\n".join(lines).rstrip() + "\n", "utf-8")

    def require_token(self) -> str:
        if not self.token:
            raise SystemExit("BOT_TOKEN не задан — скопируй .env.example в .env и вставь токен из @BotFather")
        return self.token
