"""Additive, repeatable demo expansion; never reset existing facilities or history."""

from app.db import connect, initialize, utc_now
from email.headerregistry import Address
from seed import DAYS, weekly_hours

FACILITIES = [
    (51, "Panama City Beach Operations", 30.1766, -85.8055, "America/Chicago", 51001, "Maya Patel"),
    (52, "Jacksonville Operations", 30.3322, -81.6557, "America/New_York", 52001, "Daniel Brooks"),
    (53, "Charleston Operations", 32.7765, -79.9311, "America/New_York", 53001, "Sofia Ramirez"),
    (54, "Savannah Operations", 32.0809, -81.0912, "America/New_York", 54001, "Ethan Chen"),
    (55, "Tampa Operations", 27.9506, -82.4572, "America/New_York", 55001, "Olivia Bennett"),
]
SHIFTS = [
    (51, "Daily midnight–morning", "00:00", "06:00", DAYS),
    (52, "Daily morning", "06:00", "12:00", DAYS),
    (53, "Daily afternoon", "12:00", "18:00", DAYS),
    (54, "Daily evening", "18:00", "23:59", DAYS),
    (55, "Weekday office", "08:00", "16:00", DAYS[:5]),
    (56, "Weekend support", "09:00", "17:00", DAYS[5:]),
]
FIRST_NAMES = ("Amelia", "Noah", "Isabella", "Liam", "Charlotte", "James", "Harper", "Benjamin",
               "Evelyn", "Lucas", "Abigail", "Henry", "Emily", "Alexander", "Elizabeth", "Samuel")
LAST_NAMES = ("Wilson", "Anderson", "Martinez", "Thompson", "Nguyen")


def insert_if_missing(db, table: str, key: str, values: dict, identity: tuple[str, ...]) -> int:
    """Detect reserved-ID collisions instead of overwriting unrelated demo data."""
    existing = db.execute(f"SELECT * FROM {table} WHERE {key} = ?", (values[key],)).fetchone()
    if existing:
        if any(existing[field] != values[field] for field in identity):
            raise ValueError(f"Demo ID collision in {table}: {values[key]}; no data changed")
        return 0
    columns = ', '.join(values)
    placeholders = ', '.join('?' for _ in values)
    db.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(values.values()))
    return 1


def configure_inbox(db, inbox: str) -> None:
    """Give staff unique numbered Gmail aliases; all deliver to the user's own inbox."""
    mailbox = Address(addr_spec=inbox)
    if mailbox.domain.lower() not in ('gmail.com', 'googlemail.com') or not mailbox.username or '+' in mailbox.username:
        raise ValueError('Use your base Gmail address for --demo-inbox')
    people = db.execute('SELECT emp_id,is_manager FROM users ORDER BY emp_id').fetchall()
    employees = 0
    managers = 0
    addresses = []
    for row in people:
        if row['is_manager']:
            managers += 1
            suffix = '' if managers == 1 else f"+manager{row['emp_id']}"
        else:
            employees += 1
            suffix = f'+{employees}'
        addresses.append((f'{mailbox.username}{suffix}@{mailbox.domain}', row['emp_id']))
    # A temporary unique address avoids UNIQUE collisions while existing aliases move.
    db.execute("UPDATE users SET email='demo-migrate-' || emp_id || '@example.invalid'")
    db.executemany('UPDATE users SET email=? WHERE emp_id=?', addresses)


def expand_demo(path: str, inbox: str | None = None) -> dict:
    """Five independent managers with 16 staff each, including daily holiday coverage."""
    initialize(path)
    db = connect(path)
    counts = {"locations": 0, "shifts": 0, "users": 0}
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            for shift_id, name, start, end, days in SHIFTS:
                counts["shifts"] += insert_if_missing(db, "shifts", "id", {
                    "id": shift_id, "name": name, "weekly_hours_json": weekly_hours(start, end, days)}, ("name",))
            for index, (location_id, name, lat, lng, zone, manager_id, manager_name) in enumerate(FACILITIES):
                if (db.execute('SELECT 1 FROM locations WHERE id=?', (location_id,)).fetchone()
                        and not db.execute('SELECT 1 FROM users WHERE emp_id=? AND is_manager=1', (manager_id,)).fetchone()):
                    raise ValueError(f"Demo facility ID collision: {location_id}; no data changed")
                counts["locations"] += insert_if_missing(db, "locations", "id", {
                    "id": location_id, "name": name, "latitude": lat, "longitude": lng, "timezone": zone,
                    "open_time": "00:00", "close_time": "23:59", "updated_at": utc_now()}, ())
                people = [(manager_id, manager_name, 1, 0, 0, 55)]
                for number, first in enumerate(FIRST_NAMES):
                    shift = 51 + number // 3 if number < 12 else 55 if number < 14 else 56
                    people.append((location_id * 1000 + 101 + number, f"{first} {LAST_NAMES[index]}",
                                   0, int(number < 12 and number % 3 == 1),
                                   int(number < 12 and number % 3 == 2), shift))
                for emp_id, person_name, manager, essential, remote, shift in people:
                    email = person_name.lower().replace(' ', '.') + '@example.com'
                    existing = db.execute('SELECT email FROM users WHERE emp_id=?', (emp_id,)).fetchone()
                    if existing and inbox:
                        mailbox = Address(addr_spec=inbox)
                        actual = Address(addr_spec=existing['email'])
                        if actual.domain == mailbox.domain and actual.username.split('+')[0] == mailbox.username:
                            email = existing['email']
                    counts["users"] += insert_if_missing(db, "users", "emp_id", {
                        "emp_id": emp_id, "name": person_name, "email": email, "location_id": location_id,
                        "is_manager": manager, "is_essential": essential, "is_teleworking": remote,
                        "shift_id": shift}, ("email", "is_manager"))
            if inbox:
                configure_inbox(db, inbox)
        return counts
    finally:
        db.close()
