import json
import sqlite3
from contextlib import closing

import pytest

from app.db import connect, initialize
from seed import seed


def test_exact_schema_and_integrity(db_path):
    with closing(connect(db_path)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert tables == {"locations", "users", "shifts", "decisions"}
        indexes = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")}
        assert indexes == {"idx_users_location", "idx_decisions_location_evaluated"}
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_seed_counts_assignments_and_flags(db_path):
    with closing(connect(db_path)) as db:
        assert db.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM users WHERE is_manager = 1").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM users WHERE is_manager = 0").fetchone()[0] == 12
        assert db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
        for location_id in (1, 2):
            staff = db.execute("SELECT * FROM users WHERE location_id = ? AND is_manager = 0", (location_id,)).fetchall()
            assert len(staff) == 6
            assert {row["shift_id"] for row in staff} == {1, 2, 3}
            assert sum(row["is_essential"] for row in staff) == 2
            assert sum(row["is_teleworking"] for row in staff) == 2
        assert all(row[0].endswith("@example.com") for row in db.execute("SELECT email FROM users"))
        for row in db.execute("SELECT weekly_hours_json FROM shifts"):
            hours = json.loads(row[0])
            assert set(hours) == {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
            assert all(value is None or value[0] < value[1] for value in hours.values())


def test_seed_and_schema_are_repeatable_without_resetting_edits(db_path):
    with closing(connect(db_path)) as db, db:
        db.execute("UPDATE locations SET name = 'Manager edit' WHERE id = 1")
    initialize(db_path)
    assert seed(db_path) is False
    with closing(connect(db_path)) as db:
        assert db.execute("SELECT name FROM locations WHERE id = 1").fetchone()[0] == "Manager edit"
        assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 14


@pytest.mark.parametrize("sql", [
    "UPDATE users SET is_essential = 2 WHERE emp_id = 1101",
    "UPDATE users SET is_manager = -1 WHERE emp_id = 1101",
    "UPDATE users SET is_teleworking = 2 WHERE emp_id = 1101",
    "UPDATE users SET location_id = 999 WHERE emp_id = 1101",
    "UPDATE users SET shift_id = 999 WHERE emp_id = 1101",
    "UPDATE users SET email = 'alex.manager@example.com' WHERE emp_id = 1101",
    "UPDATE locations SET latitude = 91 WHERE id = 1",
    "UPDATE locations SET longitude = -181 WHERE id = 1",
    "DELETE FROM locations WHERE id = 1",
    "DELETE FROM shifts WHERE id = 1",
])
def test_database_rejects_invalid_data(db_path, sql):
    with closing(connect(db_path)) as db:
        with pytest.raises(sqlite3.IntegrityError), db:
            db.execute(sql)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
