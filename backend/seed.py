"""Seed synthetic demo data once; never replace edited facilities or staff."""

import json
import sqlite3

from app.db import connect, database_path, initialize, utc_now

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def weekly_hours(start: str, end: str, days: tuple[str, ...]) -> str:
    """Serialize all seven days, including explicit unscheduled days."""
    return json.dumps({day: [start, end] if day in days else None for day in DAYS})


def seed(path: str) -> bool:
    """Insert the complete demo atomically, only into an empty database."""
    initialize(path)
    connection = connect(path)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            if any(connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                   for table in ("locations", "shifts", "users", "decisions")):
                return False
            insert_locations(connection)
            insert_staff(connection)
        return True
    finally:
        connection.close()


def insert_locations(connection: sqlite3.Connection) -> None:
    """Create US offices in two timezones and three same-day shift templates."""
    connection.executemany(
        """INSERT INTO locations
           (id, name, latitude, longitude, timezone, open_time, close_time, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [(1, "Chicago Office", 41.8781, -87.6298, "America/Chicago", "08:00", "18:00", utc_now()),
         (2, "Denver Office", 39.7392, -104.9903, "America/Denver", "08:00", "18:00", utc_now())],
    )
    connection.executemany(
        "INSERT INTO shifts (id, name, weekly_hours_json) VALUES (?, ?, ?)",
        [(1, "Weekday early", weekly_hours("08:00", "16:00", DAYS[:5])),
         (2, "Weekday late", weekly_hours("10:00", "18:00", DAYS[:5])),
         (3, "Weekend", weekly_hours("09:00", "17:00", DAYS[5:]))],
    )


def insert_staff(connection: sqlite3.Connection) -> None:
    """Assign six staff and one manager to each office, with mixed eligibility flags."""
    people = [
        (1001, "Alex Morgan", "alex.manager@example.com", 1, 1, 0, 0, 1),
        (2001, "Jordan Lee", "jordan.manager@example.com", 2, 1, 0, 0, 1),
    ]
    names = ("Casey", "Taylor", "Riley", "Avery", "Quinn", "Robin")
    for location_id in (1, 2):
        for index, name in enumerate(names, start=1):
            people.append((location_id * 1000 + 100 + index, f"{name} Demo {location_id}",
                           f"staff.{location_id}.{index}@example.com", location_id, 0,
                           int(index in (2, 5)), int(index in (3, 6)), (index - 1) % 3 + 1))
    connection.executemany(
        """INSERT INTO users
           (emp_id, name, email, location_id, is_manager, is_essential, is_teleworking, shift_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", people,
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expand-demo", action="store_true", help="Add five managed facilities and 80 staff without resetting data")
    parser.add_argument("--demo-inbox", help="Assign numbered Gmail aliases to all synthetic employees")
    args = parser.parse_args()
    if args.expand_demo:
        from demo_data import expand_demo
        print(json.dumps(expand_demo(database_path(), args.demo_inbox)))
    else:
        changed = seed(database_path())
        print("Seeded 2 facilities, 2 managers, 12 staff and 3 shifts."
              if changed else "Database already contains data; nothing changed.")
