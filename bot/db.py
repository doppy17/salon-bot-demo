"""Хранилище и бизнес-логика записи: услуги, мастера, свободные слоты, записи.

Всё на sqlite3 из стандартной библиотеки — клиенту не нужно ставить БД.
"""
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS services (
    id        INTEGER PRIMARY KEY,
    title     TEXT NOT NULL,
    price     INTEGER NOT NULL,
    minutes   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS masters (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS appointments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    username    TEXT,
    client_name TEXT NOT NULL,
    phone       TEXT NOT NULL,
    service_id  INTEGER NOT NULL REFERENCES services(id),
    master_id   INTEGER NOT NULL REFERENCES masters(id),
    day         TEXT NOT NULL,          -- YYYY-MM-DD
    start_min   INTEGER NOT NULL,       -- минуты от полуночи
    status      TEXT NOT NULL DEFAULT 'active',   -- active | cancelled
    reminded    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_appt_day ON appointments(day, master_id, status);
"""

SEED_SERVICES = [
    ("Стрижка женская", 2500, 60),
    ("Стрижка мужская", 1500, 45),
    ("Окрашивание", 5500, 120),
    ("Маникюр", 2000, 60),
    ("Педикюр", 2800, 90),
    ("Брови + ресницы", 1800, 60),
]
SEED_MASTERS = ["Анна", "Марина", "Ирина"]


@dataclass
class Slot:
    day: date
    start_min: int

    @property
    def label(self) -> str:
        return f"{self.start_min // 60:02d}:{self.start_min % 60:02d}"


class SalonDB:
    def __init__(self, path: str | Path, open_hour=10, close_hour=20, slot_minutes=30, days_ahead=7):
        self.path = str(path)
        self.open_hour = open_hour
        self.close_hour = close_hour
        self.slot_minutes = slot_minutes
        self.days_ahead = days_ahead
        self._init()

    # ---------- низкий уровень ----------
    def connect(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def _init(self):
        with self.connect() as con:
            con.executescript(SCHEMA)
            if not con.execute("SELECT 1 FROM services LIMIT 1").fetchone():
                con.executemany("INSERT INTO services(title, price, minutes) VALUES (?,?,?)", SEED_SERVICES)
            if not con.execute("SELECT 1 FROM masters LIMIT 1").fetchone():
                con.executemany("INSERT INTO masters(name) VALUES (?)", [(m,) for m in SEED_MASTERS])

    # ---------- чтение ----------
    def services(self) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute("SELECT * FROM services ORDER BY id").fetchall()

    def service(self, sid: int):
        with self.connect() as con:
            return con.execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()

    def masters(self) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute("SELECT * FROM masters ORDER BY id").fetchall()

    def master(self, mid: int):
        with self.connect() as con:
            return con.execute("SELECT * FROM masters WHERE id=?", (mid,)).fetchone()

    def busy_intervals(self, day: date, master_id: int) -> list[tuple[int, int]]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT a.start_min, s.minutes FROM appointments a "
                "JOIN services s ON s.id = a.service_id "
                "WHERE a.day=? AND a.master_id=? AND a.status='active'",
                (day.isoformat(), master_id),
            ).fetchall()
        return [(r["start_min"], r["start_min"] + r["minutes"]) for r in rows]

    def free_slots(self, day: date, master_id: int, duration_min: int, now: datetime | None = None) -> list[Slot]:
        """Слоты, где услуга целиком влезает в рабочий день и не пересекается с записями."""
        now = now or datetime.now()
        if day < now.date():
            return []
        busy = self.busy_intervals(day, master_id)
        result: list[Slot] = []
        last_start = self.close_hour * 60 - duration_min
        start = self.open_hour * 60
        while start <= last_start:
            end = start + duration_min
            overlaps = any(start < b_end and end > b_start for b_start, b_end in busy)
            if not overlaps:
                if day > now.date() or start > now.hour * 60 + now.minute:
                    result.append(Slot(day=day, start_min=start))
            start += self.slot_minutes
        return result

    def available_days(self, master_id: int, duration_min: int, now: datetime | None = None) -> list[date]:
        now = now or datetime.now()
        days = []
        for i in range(self.days_ahead):
            d = now.date() + timedelta(days=i)
            if self.free_slots(d, master_id, duration_min, now=now):
                days.append(d)
        return days

    # ---------- запись ----------
    def book(self, *, user_id: int, username: str | None, client_name: str, phone: str,
             service_id: int, master_id: int, day: date, start_min: int) -> int:
        svc = self.service(service_id)
        if svc is None:
            raise ValueError("Услуга не найдена")
        # защита от гонки: ещё раз проверяем, что слот свободен
        fresh = self.free_slots(day, master_id, svc["minutes"])
        if not any(s.start_min == start_min for s in fresh):
            raise ValueError("Это время уже заняли — выбери другое")
        with self.connect() as con:
            cur = con.execute(
                "INSERT INTO appointments(user_id, username, client_name, phone, service_id, master_id,"
                " day, start_min, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (user_id, username, client_name, phone, service_id, master_id, day.isoformat(),
                 start_min, datetime.now().isoformat(timespec="seconds")),
            )
            return cur.lastrowid

    def cancel(self, appt_id: int) -> bool:
        with self.connect() as con:
            cur = con.execute("UPDATE appointments SET status='cancelled' WHERE id=? AND status='active'", (appt_id,))
            return cur.rowcount > 0

    def get(self, appt_id: int):
        with self.connect() as con:
            return con.execute(
                "SELECT a.*, s.title AS service, s.price, s.minutes, m.name AS master "
                "FROM appointments a JOIN services s ON s.id=a.service_id JOIN masters m ON m.id=a.master_id "
                "WHERE a.id=?", (appt_id,)
            ).fetchone()

    def upcoming_for_user(self, user_id: int, now: datetime | None = None) -> list[sqlite3.Row]:
        now = now or datetime.now()
        with self.connect() as con:
            return con.execute(
                "SELECT a.*, s.title AS service, s.price, s.minutes, m.name AS master "
                "FROM appointments a JOIN services s ON s.id=a.service_id JOIN masters m ON m.id=a.master_id "
                "WHERE a.user_id=? AND a.status='active' AND a.day>=? ORDER BY a.day, a.start_min",
                (user_id, now.date().isoformat()),
            ).fetchall()

    def day_schedule(self, day: date) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute(
                "SELECT a.*, s.title AS service, s.price, m.name AS master "
                "FROM appointments a JOIN services s ON s.id=a.service_id JOIN masters m ON m.id=a.master_id "
                "WHERE a.day=? AND a.status='active' ORDER BY a.start_min", (day.isoformat(),)
            ).fetchall()

    def due_reminders(self, now: datetime, lead_minutes: int = 120) -> list[sqlite3.Row]:
        """Записи, до которых осталось <= lead_minutes и о которых ещё не напоминали."""
        with self.connect() as con:
            rows = con.execute(
                "SELECT a.*, s.title AS service, s.minutes, m.name AS master "
                "FROM appointments a JOIN services s ON s.id=a.service_id JOIN masters m ON m.id=a.master_id "
                "WHERE a.status='active' AND a.reminded=0"
            ).fetchall()
        due = []
        for r in rows:
            starts = datetime.combine(date.fromisoformat(r["day"]), time(0)) + timedelta(minutes=r["start_min"])
            if timedelta(0) <= starts - now <= timedelta(minutes=lead_minutes):
                due.append(r)
        return due

    def mark_reminded(self, appt_id: int):
        with self.connect() as con:
            con.execute("UPDATE appointments SET reminded=1 WHERE id=?", (appt_id,))


def fmt_day(d: date) -> str:
    days = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    months = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
    return f"{days[d.weekday()]}, {d.day} {months[d.month - 1]}"


def fmt_minutes(m: int) -> str:
    h, mm = divmod(m, 60)
    if h and mm:
        return f"{h} ч {mm} мин"
    return f"{h} ч" if h else f"{mm} мин"


def fmt_price(p: int) -> str:
    return f"{p:,}".replace(",", " ") + " ₽"
