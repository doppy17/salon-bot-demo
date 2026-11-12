"""Все клавиатуры и callback-фабрики в одном месте."""
from datetime import date

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .db import fmt_day, fmt_price, SalonDB, Slot


class ServiceCb(CallbackData, prefix="svc"):
    id: int


class MasterCb(CallbackData, prefix="mst"):
    id: int


class DayCb(CallbackData, prefix="day"):
    d: str  # YYYY-MM-DD


class TimeCb(CallbackData, prefix="tim"):
    d: str
    m: int  # минуты от полуночи


class ConfirmCb(CallbackData, prefix="ok"):
    yes: int  # 1 — подтвердить, 0 — отменить


class CancelCb(CallbackData, prefix="cnl"):
    id: int


def services_kb(db: SalonDB) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in db.services():
        kb.button(
            text=f"{s['title']} · {fmt_price(s['price'])} · {s['minutes']} мин",
            callback_data=ServiceCb(id=s["id"]),
        )
    kb.adjust(1)
    return kb.as_markup()


def masters_kb(db: SalonDB) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for m in db.masters():
        kb.button(text=f"💇 {m['name']}", callback_data=MasterCb(id=m["id"]))
    kb.button(text="Любой свободный", callback_data=MasterCb(id=0))
    kb.adjust(2, 1)
    return kb.as_markup()


def days_kb(db: SalonDB, master_id: int, duration: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for d in db.available_days(master_id, duration):
        kb.button(text=fmt_day(d), callback_data=DayCb(d=d.isoformat()))
    kb.button(text="⬅️ Назад", callback_data=MasterCb(id=-1))
    kb.adjust(2, 2, 2, 1, 1)
    return kb.as_markup()


def times_kb(slots: list[Slot], day: date) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in slots:
        kb.button(text=s.label, callback_data=TimeCb(d=day.isoformat(), m=s.start_min))
    kb.button(text="⬅️ Другая дата", callback_data=DayCb(d="back"))
    kb.adjust(4, 4, 4, 4, 4, 1)
    return kb.as_markup()


def confirm_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить", callback_data=ConfirmCb(yes=1))
    kb.button(text="✖️ Начать заново", callback_data=ConfirmCb(yes=0))
    kb.adjust(1, 1)
    return kb.as_markup()


def appointments_kb(rows) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for r in rows:
        kb.button(text=f"❌ Отменить {r['day'][5:]} {r['start_min'] // 60:02d}:{r['start_min'] % 60:02d}",
                  callback_data=CancelCb(id=r["id"]))
    kb.adjust(1)
    return kb.as_markup()


def phone_kb() -> InlineKeyboardMarkup:
    """Кнопка «отправить свой номер» — клиенту не надо печатать руками."""
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить мой номер", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )
