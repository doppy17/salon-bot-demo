"""Точка входа: python -m bot.main"""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from .config import Config
from .db import SalonDB, fmt_day
from .handlers import router

from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent.parent / "bot.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")],
)
log = logging.getLogger("salon-bot")

_LOCK_HANDLE = None


def acquire_single_instance_lock():
    """Не даёт запустить второй экземпляр: Telegram разрешает только один поллинг.

    Без этого двойной клик по run.bat даёт TelegramConflictError и бот «мигает».
    """
    global _LOCK_HANDLE
    path = LOG_FILE.parent / "bot.lock"
    handle = open(path, "w")
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    handle.write(str(os.getpid()))
    handle.flush()
    _LOCK_HANDLE = handle
    return True

COMMANDS = [
    ("start", "Записаться"),
    ("my", "Мои записи"),
    ("help", "Помощь"),
]


async def reminder_loop(bot: Bot, db: SalonDB, stop: asyncio.Event):
    """Напоминание за 2 часа до визита — главная причина, по которой салоны берут бота."""
    while not stop.is_set():
        try:
            from datetime import datetime
            for appt in db.due_reminders(datetime.now()):
                await bot.send_message(
                    appt["user_id"],
                    f"Напоминаю: сегодня в {appt['start_min'] // 60:02d}:{appt['start_min'] % 60:02d} "
                    f"у вас <b>{appt['service']}</b> у мастера {appt['master']}.\n"
                    f"Если планы изменились — /my, там можно отменить.",
                )
                db.mark_reminded(appt["id"])
        except Exception as e:  # noqa: BLE001
            log.warning("reminder_loop: %s", e)
        try:
            await asyncio.wait_for(stop.wait(), timeout=300)
        except asyncio.TimeoutError:
            pass


async def main():
    # На хостинге платформа ждёт открытый порт — поднимаем health-сервер.
    # Локально PORT не задан, поэтому ничего лишнего не слушаем.
    runner = None
    if os.getenv("PORT"):
        from . import webserver

        runner = await webserver.start()
        log.info("health-сервер слушает порт %s", os.getenv("PORT"))

    if not acquire_single_instance_lock():
        log.error("Бот уже запущен в другом окне — второй экземпляр не нужен, выхожу.")
        return

    cfg = Config()
    db = SalonDB(cfg.db_path, open_hour=cfg.open_hour, close_hour=cfg.close_hour,
                 slot_minutes=cfg.slot_minutes, days_ahead=cfg.days_ahead)
    bot = Bot(cfg.require_token(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMANDS])
    if runner is not None:
        from . import webserver

        webserver.STARTED = True
    stop = asyncio.Event()
    task = asyncio.create_task(reminder_loop(bot, db, stop))
    log.info("%s: бот запущен", cfg.salon_name)
    try:
        await dp.start_polling(bot, cfg=cfg, db=db)
    finally:
        stop.set()
        await task
        await bot.session.close()
        if runner is not None:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
