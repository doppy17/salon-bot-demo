"""Проверка логики записи без Telegram: python tests/test_db.py

Покрывает то, что чаще всего ломается в таких ботах: границы рабочего дня,
длительность услуги, пересечения, двойную запись на один слот, напоминания.
"""
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.db import SalonDB, fmt_day, fmt_minutes, fmt_price  # noqa: E402

FAILED = []


def check(name, cond, extra=""):
    if cond:
        print(f"  ok  {name}")
    else:
        print(f"  FAIL {name} {extra}")
        FAILED.append(name)


def main():
    db = SalonDB(Path(tempfile.mkdtemp()) / "t.sqlite3")
    tomorrow = date.today() + timedelta(days=1)
    master = db.masters()[0]["id"]
    haircut = db.service(1)      # 60 минут
    coloring = db.service(3)     # 120 минут

    print("Услуги и мастера")
    check("6 услуг засеяны", len(db.services()) == 6)
    check("3 мастера засеяны", len(db.masters()) == 3)

    print("Слоты")
    slots = db.free_slots(tomorrow, master, haircut["minutes"])
    check("слоты есть", len(slots) > 0)
    check("первый слот 10:00", slots[0].label == "10:00", slots[0].label)
    check("последний слот 19:00 (закрытие 20:00, услуга 60 мин)",
          slots[-1].label == "19:00", slots[-1].label)
    check("шаг 30 минут", all(
        (b.start_min - a.start_min) == 30 for a, b in zip(slots, slots[1:])))

    long_slots = db.free_slots(tomorrow, master, coloring["minutes"])
    check("для 2-часовой услуги последний слот 18:00", long_slots[-1].label == "18:00",
          long_slots[-1].label)
    check("2-часовых слотов меньше, чем часовых", len(long_slots) < len(slots))

    print("Прошедшее время")
    now = datetime.combine(tomorrow, datetime.min.time()).replace(hour=14, minute=10)
    check("на сегодня прошедшие слоты не предлагаются",
          all(s.start_min > 14 * 60 + 10 for s in db.free_slots(tomorrow, master, 60, now=now)))
    check("на вчера слотов нет", db.free_slots(date.today() - timedelta(days=1), master, 60) == [])

    print("Запись и пересечения")
    appt_id = db.book(user_id=111, username="test", client_name="Иван", phone="+79991234567",
                      service_id=1, master_id=master, day=tomorrow, start_min=14 * 60)
    check("запись создана", appt_id > 0)
    after = db.free_slots(tomorrow, master, 60)
    check("занятый слот исчез", all(s.start_min != 14 * 60 for s in after))
    check("середина услуги 14:30 тоже занята", all(s.start_min != 14 * 60 + 30 for s in after))
    check("15:00 свободно — впритык, но не внахлёст", any(s.start_min == 15 * 60 for s in after))
    check("13:30 всё ещё свободно", any(s.start_min == 13 * 60 for s in after))

    print("Защита от двойной записи")
    try:
        db.book(user_id=222, username=None, client_name="Пётр", phone="+79990000000",
                service_id=1, master_id=master, day=tomorrow, start_min=14 * 60)
        check("второй клиент на то же время отклонён", False)
    except ValueError:
        check("второй клиент на то же время отклонён", True)

    print("Второй мастер не блокируется записью первого")
    check("у другого мастера 14:00 свободно",
          any(s.start_min == 14 * 60 for s in db.free_slots(tomorrow, db.masters()[1]["id"], 60)))

    print("Отмена")
    check("отмена работает", db.cancel(appt_id) is True)
    check("повторная отмена возвращает False", db.cancel(appt_id) is False)
    check("слот снова свободен", any(s.start_min == 14 * 60 for s in db.free_slots(tomorrow, master, 60)))
    check("отменённая запись не попадает в расписание", db.day_schedule(tomorrow) == [])

    print("Напоминания")
    a = db.book(user_id=333, username=None, client_name="Аня", phone="+79995555555",
                service_id=1, master_id=master, day=tomorrow, start_min=12 * 60)
    check("не напоминает за сутки",
          [r["id"] for r in db.due_reminders(datetime.combine(tomorrow, datetime.min.time()))] == [])
    in_90 = datetime.combine(tomorrow, datetime.min.time()) + timedelta(hours=10, minutes=30)
    check("напоминает за 90 минут", a in [r["id"] for r in db.due_reminders(in_90)])
    db.mark_reminded(a)
    check("повторно не напоминает", a not in [r["id"] for r in db.due_reminders(in_90)])

    print("Форматирование")
    check("день по-русски", "сб" in fmt_day(date(2026, 11, 14)) or "вс" in fmt_day(date(2026, 11, 14)),
          fmt_day(date(2026, 11, 14)))
    check("минуты", fmt_minutes(90) == "1 ч 30 мин", fmt_minutes(90))
    check("цена с пробелом", fmt_price(5500) == "5 500 ₽", fmt_price(5500))

    print()
    if FAILED:
        print(f"ПРОВАЛЕНО {len(FAILED)}: {', '.join(FAILED)}")
        return 1
    print("Все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
