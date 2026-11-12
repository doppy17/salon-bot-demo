"""Сквозной прогон сценария записи через настоящий Dispatcher, но без Telegram.

Подменяем только сессию бота: все хендлеры, фильтры, FSM и БД работают по-настоящему.
Так видно текст каждого сообщения и что реально записалось в базу.

    python tests/test_flow.py
"""
import asyncio
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.types import (CallbackQuery, Chat, Contact, Message, Update, User)  # noqa: E402

from bot.config import Config  # noqa: E402
from bot.db import SalonDB  # noqa: E402
from bot.handlers import router  # noqa: E402

FAILED = []
ADMIN_ID = 900001
CLIENT_ID = 900002


def check(name, cond, extra=""):
    if cond:
        print(f"  ok  {name}")
    else:
        print(f"  FAIL {name} {extra}")
        FAILED.append(name)


class FakeSession(BaseSession):
    """Записывает всё, что бот пытался отправить, и возвращает правдоподобные ответы."""

    def __init__(self):
        super().__init__()
        self.sent: list[tuple[str, str, int]] = []   # (метод, текст, chat_id)
        self.buttons: list[str] = []                 # тексты кнопок последней отправки
        self._mid = 1

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        return await self._handle(method)

    async def stream_content(self, *a, **kw):  # pragma: no cover
        yield b""

    async def __call__(self, bot, method, timeout=None):
        return await self._handle(method)

    async def _handle(self, method):
        name = type(method).__name__
        text = getattr(method, "text", None) or getattr(method, "caption", None) or ""
        chat_id = 0
        for attr in ("chat_id",):
            v = getattr(method, attr, None)
            if isinstance(v, int):
                chat_id = v
        if not chat_id:
            ch = getattr(method, "chat", None)
            if ch is not None and hasattr(ch, "id"):
                chat_id = ch.id
        markup = getattr(method, "reply_markup", None)
        if markup is not None and getattr(markup, "inline_keyboard", None):
            for row in markup.inline_keyboard:
                for b in row:
                    self.buttons = [x for x in self.buttons]  # копия не нужна, оставляем историю
                    self.buttons.append(b.text)
        self.sent.append((name, text, chat_id))
        if name in ("SendMessage", "EditMessageText"):
            self._mid += 1
            return Message(
                message_id=self._mid, date=datetime.now(),
                chat=Chat(id=chat_id or CLIENT_ID, type="private"), text=text or "",
            )
        return True

    def texts(self) -> str:
        return "\n".join(f"{n}:{t}" for n, t, _ in self.sent)

    def last_to(self, chat_id: int) -> str:
        for n, t, c in reversed(self.sent):
            if c == chat_id:
                return t
        return ""


def upd(message=None, callback=None) -> Update:
    return Update(update_id=1, message=message, callback_query=callback)


def msg(text: str, uid: int = CLIENT_ID, contact: Contact | None = None) -> Message:
    return Message(
        message_id=1, date=datetime.now(), chat=Chat(id=uid, type="private"),
        from_user=User(id=uid, is_bot=False, first_name="Иван"), text=text, contact=contact,
    )


def cq(data: str, uid: int = CLIENT_ID) -> CallbackQuery:
    return CallbackQuery(
        id="1", from_user=User(id=uid, is_bot=False, first_name="Иван"),
        chat_instance="1", data=data,
        message=Message(message_id=1, date=datetime.now(),
                        chat=Chat(id=uid, type="private"), text="..."),
    )


async def main():
    tmp = Path(tempfile.mkdtemp()) / "flow.sqlite3"
    cfg = Config(token="42:TEST", admin_ids=[ADMIN_ID], db_path=tmp)
    db = SalonDB(tmp)
    session = FakeSession()
    bot = Bot(cfg.token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    async def send(u):
        await dp.feed_update(bot, u, cfg=cfg, db=db)

    print("Старт и меню услуг")
    await send(upd(message=msg("/start")))
    check("поздоровался, назвал салон", "Салон" in session.texts())
    check("показал 6 услуг кнопками", len(session.buttons) == 6, session.buttons)
    check("в кнопках есть цена и длительность", "2 500 ₽" in " ".join(session.buttons))

    print("Шаг 1-2: услуга и мастер")
    await send(upd(callback=cq("svc:1")))
    check("подтвердил выбор услуги", "Выбрали" in session.texts())
    await send(upd(callback=cq("mst:1")))
    check("спросил день", "Выберите день" in session.texts())

    print("Шаг 3-4: день и время")
    day = date.today() + timedelta(days=1)
    await send(upd(callback=cq(f"day:{day.isoformat()}")))
    check("показал свободное время", "свободное время" in session.texts())
    await send(upd(callback=cq(f"tim:{day.isoformat()}:840")))
    check("спросил имя", "Как вас зовут" in session.texts())

    print("Шаг 5-6: имя и телефон")
    await send(upd(message=msg("Иван")))
    check("попросил телефон", "телефон" in session.texts().lower())
    await send(upd(message=msg("", contact=Contact(phone_number="+79991234567", first_name="Иван"))))
    check("показал проверку записи", "Проверьте запись" in session.texts())
    check("в проверке есть имя и телефон", "Иван" in session.last_to(CLIENT_ID) or "7999" in session.texts())

    print("Шаг 7: подтверждение")
    await send(upd(callback=cq("ok:1")))
    check("подтвердил запись клиенту", "вы записаны" in session.texts().lower())

    rows = db.day_schedule(day)
    check("в базе ровно одна запись", len(rows) == 1, len(rows))
    if rows:
        r = rows[0]
        check("время верное (14:00)", r["start_min"] == 840, r["start_min"])
        check("имя сохранено", r["client_name"] == "Иван", r["client_name"])
        check("телефон сохранён", r["phone"] == "+79991234567", r["phone"])
        check("услуга верная", r["service"] == "Стрижка женская", r["service"])

    admin_text = session.last_to(ADMIN_ID)
    check("админу пришло уведомление", "Новая запись" in admin_text, admin_text[:80])
    check("в уведомлении телефон клиента", "79991234567" in admin_text)

    print("Повторная запись на занятое время")
    await send(upd(message=msg("/start")))
    await send(upd(callback=cq("svc:1")))
    await send(upd(callback=cq("mst:1")))
    await send(upd(callback=cq(f"day:{day.isoformat()}")))
    before = len(session.sent)
    await send(upd(callback=cq(f"tim:{day.isoformat()}:840")))
    new_calls = session.sent[before:]
    alerts = [t for n, t, _ in new_calls if n == "AnswerCallbackQuery"]
    check("на занятое время бот ответил алертом", any("заняли" in a for a in alerts), alerts)
    check("дальше по сценарию не пустил",
          not any("Как вас зовут" in t for n, t, _ in new_calls if n in ("SendMessage", "EditMessageText")))
    check("в записи по-прежнему один клиент", len(db.day_schedule(day)) == 1)

    print("Мои записи и отмена")
    await send(upd(message=msg("/my")))
    check("показал мои записи", "Ваши записи" in session.texts())
    appt_id = rows[0]["id"] if rows else 1
    await send(upd(callback=cq(f"cnl:{appt_id}")))
    check("отменил по кнопке", "отменена" in session.texts())
    check("в базе не осталось активных", db.day_schedule(day) == [])

    print("Админка")
    await send(upd(message=msg("/today", uid=ADMIN_ID)))
    check("админ получил расписание", "записей нет" in session.last_to(ADMIN_ID).lower())
    await send(upd(message=msg("/today", uid=CLIENT_ID)))
    check("клиенту админка недоступна", "записей нет" not in session.last_to(CLIENT_ID).lower())

    await bot.session.close()
    print()
    if FAILED:
        print(f"ПРОВАЛЕНО {len(FAILED)}: {', '.join(FAILED)}")
        return 1
    print("Сценарий целиком прошёл.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
