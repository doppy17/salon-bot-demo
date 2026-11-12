"""Хендлеры: запись в 5 шагов, мои записи, админка."""
import html
import logging
import re
from datetime import date, datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from . import keyboards as kb
from .config import Config
from .db import SalonDB, fmt_day, fmt_minutes, fmt_price

log = logging.getLogger(__name__)
router = Router(name="booking")

PHONE_RE = re.compile(r"^\+?\d[\d\s\-()]{8,18}$")


class Booking(StatesGroup):
    service = State()
    master = State()
    day = State()
    time = State()
    name = State()
    phone = State()
    confirm = State()


# ---------- helpers ----------
def _cfg(data: dict) -> Config:
    return data["cfg"]


def _db(data: dict) -> SalonDB:
    return data["db"]


def _fmt_appt(a) -> str:
    return (f"<b>{html.escape(a['service'])}</b>\n"
            f"{fmt_day(date.fromisoformat(a['day']))} в {a['start_min'] // 60:02d}:{a['start_min'] % 60:02d}\n"
            f"Мастер: {html.escape(a['master'])} · {fmt_minutes(a['minutes'])} · {fmt_price(a['price'])}")


async def _admin_notify(bot: Bot, cfg: Config, text: str):
    for admin_id in cfg.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception as e:  # noqa: BLE001
            log.warning("не смог уведомить админа %s: %s", admin_id, e)


# ---------- старт ----------
@router.message(CommandStart())
async def start(message: Message, state: FSMContext, **data):
    cfg, db = _cfg(data), _db(data)
    await state.clear()
    first_run = not cfg.admin_ids
    if first_run:
        cfg.admin_ids.append(message.from_user.id)
        cfg.persist_admin_ids()
    await message.answer(
        f"Здравствуйте! Это <b>{html.escape(cfg.salon_name)}</b>.\n"
        f"Запишу вас за минуту — выберите услугу 👇\n\n"
        f"📍 {html.escape(cfg.address)}\n☎️ {html.escape(cfg.phone)}",
        reply_markup=kb.services_kb(db),
    )
    if first_run:
        await message.answer(
            "🔑 Вы назначены администратором этого бота — сюда будут приходить "
            "уведомления о новых записях. Команда /today покажет расписание дня."
        )
    await state.set_state(Booking.service)


@router.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "/start — записаться\n/my — мои записи\n/help — подсказка"
    )


# ---------- шаг 1: услуга ----------
@router.callback_query(kb.ServiceCb.filter(), Booking.service)
async def pick_service(call: CallbackQuery, callback_data: kb.ServiceCb, state: FSMContext, **data):
    db = _db(data)
    svc = db.service(callback_data.id)
    if svc is None:
        await call.answer("Услуга не найдена", show_alert=True)
        return
    await state.update_data(service_id=svc["id"], duration=svc["minutes"], service_title=svc["title"])
    await state.set_state(Booking.master)
    await call.message.edit_text(
        f"Выбрали: <b>{html.escape(svc['title'])}</b> — {fmt_price(svc['price'])}, "
        f"{fmt_minutes(svc['minutes'])}.\n\nК какому мастеру?",
        reply_markup=kb.masters_kb(db),
    )
    await call.answer()


# ---------- шаг 2: мастер ----------
@router.callback_query(kb.MasterCb.filter(), Booking.master)
async def pick_master(call: CallbackQuery, callback_data: kb.MasterCb, state: FSMContext, **data):
    db = _db(data)
    st = await state.get_data()
    duration = st["duration"]

    master_id = callback_data.id
    if master_id == -1:  # назад к услугам
        await state.set_state(Booking.service)
        await call.message.edit_text("Выберите услугу 👇", reply_markup=kb.services_kb(db))
        await call.answer()
        return

    if master_id == 0:  # любой свободный: берём первого, у кого есть окна
        for m in db.masters():
            if db.available_days(m["id"], duration):
                master_id = m["id"]
                break
        else:
            await call.answer("На ближайшую неделю окон нет 😔", show_alert=True)
            return

    if not db.available_days(master_id, duration):
        await call.answer("У этого мастера всё занято на неделю вперёд", show_alert=True)
        return

    await state.update_data(master_id=master_id)
    await state.set_state(Booking.day)
    master = db.master(master_id)
    await call.message.edit_text(
        f"Мастер: <b>{html.escape(master['name'])}</b>.\nВыберите день:",
        reply_markup=kb.days_kb(db, master_id, duration),
    )
    await call.answer()


# ---------- шаг 3: дата ----------
@router.callback_query(kb.DayCb.filter(), Booking.day)
async def pick_day(call: CallbackQuery, callback_data: kb.DayCb, state: FSMContext, **data):
    db = _db(data)
    st = await state.get_data()
    if callback_data.d == "back":
        await state.set_state(Booking.master)
        await call.message.edit_text("К какому мастеру?", reply_markup=kb.masters_kb(db))
        await call.answer()
        return

    day = date.fromisoformat(callback_data.d)
    slots = db.free_slots(day, st["master_id"], st["duration"])
    if not slots:
        await call.answer("На этот день всё занято", show_alert=True)
        return
    await state.update_data(day=callback_data.d)
    await state.set_state(Booking.time)
    await call.message.edit_text(
        f"<b>{fmt_day(day)}</b> — свободное время:",
        reply_markup=kb.times_kb(slots, day),
    )
    await call.answer()


# ---------- шаг 4: время ----------
@router.callback_query(kb.TimeCb.filter(), Booking.time)
async def pick_time(call: CallbackQuery, callback_data: kb.TimeCb, state: FSMContext, **data):
    db = _db(data)
    st = await state.get_data()
    day = date.fromisoformat(callback_data.d)
    slots = db.free_slots(day, st["master_id"], st["duration"])
    if not any(s.start_min == callback_data.m for s in slots):
        await call.answer("Это время только что заняли, выберите другое", show_alert=True)
        await call.message.edit_reply_markup(reply_markup=kb.times_kb(slots, day))
        return
    await state.update_data(day=callback_data.d, start_min=callback_data.m)
    await state.set_state(Booking.name)
    await call.message.edit_text(
        f"Записываю на <b>{fmt_day(day)} в {callback_data.m // 60:02d}:{callback_data.m % 60:02d}</b>.\n\n"
        f"Как вас зовут?"
    )
    await call.answer()


# ---------- шаг 5: имя ----------
@router.message(Booking.name, F.text, ~F.text.startswith("/"))
async def enter_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not 2 <= len(name) <= 60:
        await message.answer("Напишите имя (2–60 символов), пожалуйста.")
        return
    await state.update_data(client_name=name)
    await state.set_state(Booking.phone)
    await message.answer(
        "Остался номер телефона — нажмите кнопку ниже или введите вручную:",
        reply_markup=kb.phone_kb(),
    )


# ---------- шаг 6: телефон ----------
@router.message(Booking.phone, F.contact)
async def enter_phone_contact(message: Message, state: FSMContext):
    await _save_phone(message, state, message.contact.phone_number)


@router.message(Booking.phone, F.text, ~F.text.startswith("/"))
async def enter_phone_text(message: Message, state: FSMContext):
    await _save_phone(message, state, (message.text or "").strip())


async def _save_phone(message: Message, state: FSMContext, raw: str):
    phone = raw.strip()
    if not PHONE_RE.match(phone):
        await message.answer("Похоже, в номере опечатка. Пример: +7 999 123-45-67")
        return
    await state.update_data(phone=phone)
    st = await state.get_data()
    day = date.fromisoformat(st["day"])
    await state.set_state(Booking.confirm)
    from aiogram.types import ReplyKeyboardRemove
    await message.answer(
        "Проверьте запись:\n\n"
        f"Услуга: <b>{html.escape(st.get('service_title', '—'))}</b>\n"
        f"Когда: <b>{fmt_day(day)} в {st['start_min'] // 60:02d}:{st['start_min'] % 60:02d}</b>\n"
        f"Имя: {html.escape(st['client_name'])}\nТелефон: {html.escape(phone)}",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer("Всё верно?", reply_markup=kb.confirm_kb())


# ---------- шаг 7: подтверждение ----------
@router.callback_query(kb.ConfirmCb.filter(), Booking.confirm)
async def confirm(call: CallbackQuery, callback_data: kb.ConfirmCb, state: FSMContext, bot: Bot, **data):
    cfg, db = _cfg(data), _db(data)
    if not callback_data.yes:
        await state.set_state(Booking.service)
        await call.message.edit_text("Ок, начнём заново. Выберите услугу 👇", reply_markup=kb.services_kb(db))
        await call.answer()
        return

    st = await state.get_data()
    day = date.fromisoformat(st["day"])
    try:
        appt_id = db.book(
            user_id=call.from_user.id,
            username=call.from_user.username,
            client_name=st["client_name"],
            phone=st["phone"],
            service_id=st["service_id"],
            master_id=st["master_id"],
            day=day,
            start_min=st["start_min"],
        )
    except ValueError as e:
        await call.message.edit_text(f"Не получилось: {e}. Нажмите /start и выберите другое время.")
        await call.answer()
        await state.clear()
        return

    appt = db.get(appt_id)
    await state.clear()
    await call.message.edit_text(
        f"✅ <b>Готово, вы записаны!</b>\n\n{_fmt_appt(appt)}\n\n"
        f"📍 {html.escape(cfg.address)}\n"
        f"Напомню за 2 часа до визита. Отменить или посмотреть — /my"
    )
    await call.answer("Записал!")
    await _admin_notify(
        bot, cfg,
        f"🆕 Новая запись #{appt_id}\n{_fmt_appt(appt)}\n"
        f"Клиент: {html.escape(appt['client_name'])}, {html.escape(appt['phone'])}"
        + (f" (@{appt['username']})" if appt["username"] else ""),
    )


# ---------- мои записи ----------
@router.message(Command("my"))
async def my_appointments(message: Message, **data):
    db = _db(data)
    rows = db.upcoming_for_user(message.from_user.id)
    if not rows:
        await message.answer("Активных записей нет. Записаться — /start")
        return
    await message.answer(
        "Ваши записи:\n\n" + "\n\n".join(_fmt_appt(r) + f"\n#{r['id']}" for r in rows),
        reply_markup=kb.appointments_kb(rows),
    )


@router.callback_query(kb.CancelCb.filter())
async def cancel_appt(call: CallbackQuery, callback_data: kb.CancelCb, bot: Bot, **data):
    cfg, db = _cfg(data), _db(data)
    appt = db.get(callback_data.id)
    if appt is None or appt["user_id"] != call.from_user.id:
        await call.answer("Запись не найдена", show_alert=True)
        return
    if db.cancel(callback_data.id):
        await call.message.edit_text(f"Запись #{callback_data.id} отменена.\nЗаписаться снова — /start")
        await _admin_notify(bot, cfg, f"❌ Клиент отменил запись #{callback_data.id}: {appt['service']}, "
                                     f"{appt['day']} {appt['start_min'] // 60:02d}:{appt['start_min'] % 60:02d}")
    else:
        await call.message.edit_text("Эта запись уже отменена.")
    await call.answer()


# ---------- админка ----------
@router.message(Command("today"))
async def admin_today(message: Message, **data):
    cfg, db = _cfg(data), _db(data)
    if message.from_user.id not in cfg.admin_ids:
        return
    rows = db.day_schedule(datetime.now().date())
    if not rows:
        await message.answer("На сегодня записей нет.")
        return
    total = sum(r["price"] for r in rows)
    lines = [f"{r['start_min'] // 60:02d}:{r['start_min'] % 60:02d} — {html.escape(r['service'])}, "
             f"{html.escape(r['master'])}, {html.escape(r['client_name'])}, {html.escape(r['phone'])}"
             for r in rows]
    await message.answer(f"<b>Сегодня, {fmt_day(datetime.now().date())}</b>\n\n" + "\n".join(lines) +
                         f"\n\nВсего записей: {len(rows)} на {fmt_price(total)}")
