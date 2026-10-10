"""Qt-independent, transactional SQLite editor backend.

Every worker opens its own SQLite connection. The UI never runs these queries.
The dedicated single-writer worker serializes edits, undo, redo and seeding.
"""
from __future__ import annotations

import csv
from contextlib import closing
import io
import json
import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence


@dataclass(frozen=True)
class Column:
    key: str
    title: str
    width: int
    kind: str = "text"


COLUMNS = (
    Column("name", "Signal Name", 180),
    Column("source_protocol", "Source Protocol", 115),
    Column("source_device", "Source Device", 155),
    Column("source_address", "Source Address", 125),
    Column("data_type", "Data Type", 90),
    Column("target_protocol", "Target Protocol", 115),
    Column("target_device", "Target Device", 150),
    Column("target_address", "Target Address / IOA", 145),
    Column("scale", "Scale", 85, "float"),
    Column("offset", "Offset", 80, "float"),
    Column("unit", "Unit", 75),
    Column("enabled", "Enabled", 74, "bool"),
    Column("description", "Description", 230),
)
FIELDS = tuple(c.key for c in COLUMNS)
ALLOWED_SORTS = frozenset(("id",) + FIELDS)
WRITE_CELL_LIMIT = 500_000
COPY_CELL_LIMIT = 100_000


@dataclass(frozen=True)
class View:
    search: str = ""
    protocol: str = "All"
    sort_field: str = "id"
    descending: bool = False

    def clause(self) -> tuple[str, tuple[Any, ...]]:
        parts: list[str] = []
        params: list[Any] = []
        if self.search.strip():
            # Escape LIKE wildcards so search is literal, case-insensitive.
            word = self.search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{word}%"
            parts.append("(name LIKE ? ESCAPE '\\' OR source_device LIKE ? ESCAPE '\\' "
                         "OR target_device LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' "
                         "OR source_address LIKE ? ESCAPE '\\' OR target_address LIKE ? ESCAPE '\\')")
            params.extend([pattern] * 6)
        if self.protocol != "All":
            parts.append("(source_protocol = ? OR target_protocol = ?)")
            params.extend([self.protocol] * 2)
        return (" WHERE " + " AND ".join(parts) if parts else ""), tuple(params)

    def ordering(self) -> str:
        if self.sort_field not in ALLOWED_SORTS:
            raise ValueError("Unsupported sort field")
        return f' ORDER BY "{self.sort_field}" {"DESC" if self.descending else "ASC"}, id ASC'


def normalize(key: str, value: Any) -> Any:
    col = next((c for c in COLUMNS if c.key == key), None)
    if col is None:
        raise ValueError(f"Unknown column: {key}")
    if col.kind == "bool":
        if isinstance(value, bool):
            return int(value)
        if str(value).strip().lower() in ("1", "true", "yes", "on", "enabled"):
            return 1
        if str(value).strip().lower() in ("0", "false", "no", "off", "disabled", ""):
            return 0
        raise ValueError(f"{col.title}: expected true/false")
    if col.kind == "float":
        try:
            out = float(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{col.title}: expected a number") from exc
        if not math.isfinite(out):
            raise ValueError(f"{col.title}: number must be finite")
        return out
    out = str(value).strip()
    if len(out) > 512:
        raise ValueError(f"{col.title}: maximum 512 characters")
    if key == "name" and not out:
        raise ValueError("Signal name cannot be empty")
    return out


def connect(path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=10000")
    return db


def initialize(path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                source_protocol TEXT NOT NULL DEFAULT 'Modbus TCP',
                source_device TEXT NOT NULL DEFAULT '',
                source_address TEXT NOT NULL DEFAULT '',
                data_type TEXT NOT NULL DEFAULT 'Float32',
                target_protocol TEXT NOT NULL DEFAULT 'IEC-104',
                target_device TEXT NOT NULL DEFAULT '',
                target_address TEXT NOT NULL DEFAULT '',
                scale REAL NOT NULL DEFAULT 1.0,
                offset REAL NOT NULL DEFAULT 0.0,
                unit TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                description TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS ix_signals_name ON signals(name COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS ix_signals_src ON signals(source_protocol, source_device);
            CREATE INDEX IF NOT EXISTS ix_signals_dst ON signals(target_protocol, target_device, target_address);
            CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS operation_cells (
                operation_id INTEGER NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
                signal_id INTEGER NOT NULL,
                field TEXT NOT NULL,
                before_json TEXT NOT NULL,
                after_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_history_operation ON operation_cells(operation_id);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT OR IGNORE INTO settings(key, value) VALUES('history_cursor', '0');
        """)


def _start(db: sqlite3.Connection) -> None:
    db.execute("BEGIN IMMEDIATE")


def _sql(view: View, columns: Sequence[str] = ("id",), *, limit: int | None = None, offset: int = 0):
    for col in columns:
        if col not in ALLOWED_SORTS:
            raise ValueError(f"Illegal SQL column: {col}")
    where, params = view.clause()
    selected = ", ".join(f'"{c}"' for c in columns)
    statement = f"SELECT {selected} FROM signals{where}{view.ordering()}"
    if limit is not None:
        statement += " LIMIT ? OFFSET ?"
        params += (int(limit), int(offset))
    return statement, params


def count_rows(path: str | Path, view: View) -> int:
    with closing(connect(path)) as db:
        where, params = view.clause()
        return db.execute("SELECT COUNT(*) FROM signals" + where, params).fetchone()[0]


def load_page(path: str | Path, view: View, page: int, size: int = 256) -> list[tuple[Any, ...]]:
    if page < 0 or size <= 0 or size > 4096:
        raise ValueError("Invalid page")
    with closing(connect(path)) as db:
        statement, params = _sql(view, ("id",) + FIELDS, limit=size, offset=page * size)
        return [tuple(row) for row in db.execute(statement, params)]


def _ids(db: sqlite3.Connection, view: View, top: int, number: int, columns: Sequence[str] = ("id",)) -> list[sqlite3.Row]:
    if top < 0 or number <= 0 or number > 500_000:
        raise ValueError("Invalid selection range")
    sql, args = _sql(view, columns, limit=number, offset=top)
    result = db.execute(sql, args).fetchall()
    if len(result) != number:
        raise ValueError("Selection changed: refresh the table and try again")
    return result


def _cursor(db: sqlite3.Connection) -> int:
    return int(db.execute("SELECT value FROM settings WHERE key='history_cursor'").fetchone()[0])


def _set_cursor(db: sqlite3.Connection, pos: int) -> None:
    db.execute("UPDATE settings SET value=? WHERE key='history_cursor'", (str(pos),))


def _apply_rows(db: sqlite3.Connection, changes: Iterable[tuple[int, dict[str, Any]]], label: str) -> int:
    """Apply one validated operation atomically, with durable cell-level undo log.

    Changes is an iterable of (stable signal id, {column: new value}).
    The caller must already hold BEGIN IMMEDIATE.
    """
    cursor = _cursor(db)
    db.execute("DELETE FROM operations WHERE id > ?", (cursor,))
    operation_id = db.execute("INSERT INTO operations(label) VALUES(?)", (label,)).lastrowid
    changed = 0
    for signal_id, values in changes:
        if not values:
            continue
        keys = tuple(values)
        if any(key not in FIELDS for key in keys):
            raise ValueError("Invalid column edit")
        before_row = db.execute("SELECT " + ", ".join(f'"{k}"' for k in keys) + " FROM signals WHERE id=?", (signal_id,)).fetchone()
        if before_row is None:
            raise ValueError("Signal was deleted during edit")
        delta = {key: val for key, val in values.items() if before_row[key] != val}
        if not delta:
            continue
        db.execute("UPDATE signals SET " + ", ".join(f'"{key}"=?' for key in delta) + " WHERE id=?",
                   (*delta.values(), signal_id))
        db.executemany("INSERT INTO operation_cells(operation_id, signal_id, field, before_json, after_json) "
                       "VALUES(?,?,?,?,?)",
                       ((operation_id, signal_id, key, json.dumps(before_row[key]), json.dumps(val)) for key, val in delta.items()))
        changed += len(delta)
    if changed:
        _set_cursor(db, operation_id)
    else:
        db.execute("DELETE FROM operations WHERE id=?", (operation_id,))
    return changed


def apply_grid(path: str | Path, view: View, top: int, left: int,
               grid: Sequence[Sequence[Any]], label: str = "Paste cells") -> int:
    """Rectangular Excel-style paste; atomic and undoable, accepts no partial success."""
    if not grid or not grid[0] or top < 0 or left < 0:
        raise ValueError("Empty or invalid paste")
    width = len(grid[0])
    if any(len(row) != width for row in grid) or len(grid) * width > WRITE_CELL_LIMIT:
        raise ValueError("Paste is nonrectangular or exceeds 500,000 cells")
    if left + width > len(FIELDS):
        raise ValueError("Paste extends beyond last column")
    columns = FIELDS[left:left + width]
    with closing(connect(path)) as db:
        _start(db)
        try:
            rows = _ids(db, view, top, len(grid))
            new_rows = ((row["id"], {key: normalize(key, cell) for key, cell in zip(columns, cells)})
                        for row, cells in zip(rows, grid))
            updated = _apply_rows(db, new_rows, label)
            db.execute("COMMIT")
            return updated
        except Exception:
            db.execute("ROLLBACK")
            raise


def auto_number(path: str | Path, view: View, top: int, bottom: int,
                field: str, start: int, step: int) -> int:
    if field not in ("source_address", "target_address"):
        raise ValueError("Auto-number is only supported on address columns")
    if bottom < top or bottom - top >= 500_000 or start < 0 or step < 1:
        raise ValueError("Invalid auto-number range")
    if field == "target_address" and start + (bottom - top) * step > 16_777_215:
        raise ValueError("Maximum IEC-104 IOA is 16,777,215; split the range")
    with closing(connect(path)) as db:
        _start(db)
        try:
            rows = _ids(db, view, top, bottom - top + 1)
            changes = ((row["id"], {field: str(start + index * step)}) for index, row in enumerate(rows))
            updated = _apply_rows(db, changes, f"Auto-number {field}")
            db.execute("COMMIT")
            return updated
        except Exception:
            db.execute("ROLLBACK")
            raise


def fill_cells(path: str | Path, view: View, top: int, bottom: int,
               left: int, right: int, direction: str) -> int:
    if direction not in ("down", "right") or bottom < top or right < left:
        raise ValueError("Invalid fill direction or selection")
    if left < 0 or right >= len(FIELDS) or (bottom - top + 1) * (right - left + 1) > WRITE_CELL_LIMIT:
        raise ValueError("Selection too large")
    columns = FIELDS[left:right + 1]
    with closing(connect(path)) as db:
        _start(db)
        try:
            rows = _ids(db, view, top, bottom - top + 1, ("id",) + columns)
            def generate():
                for i, row in enumerate(rows):
                    yield row["id"], {
                        key: normalize(key, (rows[0][key] if direction == "down" else row[columns[0]]))
                        for j, key in enumerate(columns)
                        if (i > 0 if direction == "down" else j > 0)
                    }
            updated = _apply_rows(db, generate(), "Fill " + direction)
            db.execute("COMMIT")
            return updated
        except Exception:
            db.execute("ROLLBACK")
            raise


def history_step(path: str | Path, undo: bool) -> tuple[int, str]:
    with closing(connect(path)) as db:
        _start(db)
        try:
            pos = _cursor(db)
            if undo:
                operation = db.execute("SELECT id, label FROM operations WHERE id=?", (pos,)).fetchone()
            else:
                operation = db.execute("SELECT id, label FROM operations WHERE id>? ORDER BY id LIMIT 1", (pos,)).fetchone()
            if operation is None:
                db.execute("COMMIT")
                return 0, "Nothing to undo" if undo else "Nothing to redo"
            records = db.execute("SELECT signal_id, field, before_json, after_json FROM operation_cells "
                                 "WHERE operation_id=? ORDER BY rowid", (operation["id"],)).fetchall()
            by_signal: dict[int, dict[str, Any]] = {}
            for record in records:
                by_signal.setdefault(record["signal_id"], {})[record["field"]] = json.loads(record["before_json" if undo else "after_json"])
            for signal_id, values in by_signal.items():
                db.execute("UPDATE signals SET " + ", ".join(f'"{key}"=?' for key in values) + " WHERE id=?",
                           (*values.values(), signal_id))
            if undo:
                previous = db.execute("SELECT COALESCE(MAX(id),0) FROM operations WHERE id<?", (operation["id"],)).fetchone()[0]
                _set_cursor(db, previous)
            else:
                _set_cursor(db, operation["id"])
            db.execute("COMMIT")
            return len(records), operation["label"]
        except Exception:
            db.execute("ROLLBACK")
            raise


def _demo_rows(start: int, count: int) -> Iterator[tuple[Any, ...]]:
    units = ("V", "A", "Hz", "kW", "kWh", "", "°C", "")
    kinds = ("Float32", "Float32", "Float32", "Float32", "Float32", "Boolean", "Float32", "Int16")
    tags = ("Voltage", "Current", "Frequency", "ActivePower", "Energy", "Breaker", "Temperature", "Status")
    for i in range(start, start + count):
        k = i % len(tags)
        device = i // 200 + 1
        yield (f"{tags[k]}_{i + 1:06}", "Modbus TCP", f"Meter_{device:04}", str(40001 + i % 200),
               kinds[k], "IEC-104", f"SCADA_{(device - 1) // 100 + 1}", str(1000 + i),
               1.0, 0.0, units[k], 1, "Generated demo signal")


def seed_demo(path: str | Path, count: int = 100_000) -> int:
    if count < 1 or count > 1_000_000:
        raise ValueError("Demo size must be between 1 and 1,000,000")
    cols = ",".join(FIELDS)
    insert = f"INSERT INTO signals({cols}) VALUES({','.join('?' for _ in FIELDS)})"
    with closing(connect(path)) as db:
        _start(db)
        try:
            db.execute("DELETE FROM operation_cells")
            db.execute("DELETE FROM operations")
            _set_cursor(db, 0)
            db.execute("DELETE FROM signals")
            # Stream generated rows in bounded batches, never create a million Python objects.
            for start in range(0, count, 5_000):
                db.executemany(insert, _demo_rows(start, min(5_000, count - start)))
            db.execute("COMMIT")
            return count
        except Exception:
            db.execute("ROLLBACK")
            raise


def add_signals(path: str | Path, count: int = 100) -> int:
    if not 1 <= count <= 10_000:
        raise ValueError("Add between 1 and 10,000 rows")
    with closing(connect(path)) as db:
        _start(db)
        try:
            maximum = db.execute("SELECT COALESCE(MAX(id),0) FROM signals").fetchone()[0]
            db.executemany("INSERT INTO signals(name) VALUES(?)", ((f"New_Signal_{maximum+i+1:07}",) for i in range(count)))
            db.execute("COMMIT")
            return count
        except Exception:
            db.execute("ROLLBACK")
            raise


def copy_tsv(path: str | Path, view: View, top: int, bottom: int, left: int, right: int) -> str:
    if bottom < top or right < left or left < 0 or right >= len(FIELDS):
        raise ValueError("Invalid selection")
    if (bottom - top + 1) * (right - left + 1) > COPY_CELL_LIMIT:
        raise ValueError(f"Copy limit is {COPY_CELL_LIMIT:,} cells (select a smaller range or export CSV)")
    with closing(connect(path)) as db:
        rows = _ids(db, view, top, bottom - top + 1, FIELDS[left:right + 1])
        out = io.StringIO(newline="")
        writer = csv.writer(out, dialect="excel-tab", lineterminator="\r\n")
        writer.writerows(tuple(row) for row in rows)
        return out.getvalue()


def export_csv(path: str | Path, dest: str | Path, view: View) -> int:
    # Writer is streamed; no all-data fetchall, and output is not the live database.
    statement, args = _sql(view, FIELDS)
    exported = 0
    target = Path(dest)
    temporary = target.with_name(target.name + ".part")
    try:
        with closing(connect(path)) as db, temporary.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file)
            writer.writerow(FIELDS)
            cursor = db.execute(statement, args)
            while batch := cursor.fetchmany(5000):
                writer.writerows(tuple(row) for row in batch)
                exported += len(batch)
        temporary.replace(target)
        return exported
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def import_csv(path: str | Path, source: str | Path) -> int:
    """Import spreadsheet CSV into current database in one all-or-nothing transaction."""
    with open(source, "r", newline="", encoding="utf-8-sig") as file, closing(connect(path)) as db:
        reader = csv.DictReader(file)
        if not reader.fieldnames or not set(reader.fieldnames).issubset(FIELDS) or "name" not in reader.fieldnames:
            raise ValueError("CSV requires a 'name' column; other headers must match exported field names")
        headers = reader.fieldnames
        query = f"INSERT INTO signals({','.join(headers)}) VALUES({','.join('?' for _ in headers)})"
        _start(db)
        count = 0
        try:
            batch = []
            for row in reader:
                batch.append(tuple(normalize(key, row[key] or "0" if key in ("scale", "offset") else row[key]) for key in headers))
                if len(batch) == 5000:
                    db.executemany(query, batch)
                    count += len(batch)
                    batch.clear()
            if batch:
                db.executemany(query, batch)
                count += len(batch)
            db.execute("COMMIT")
            return count
        except Exception:
            db.execute("ROLLBACK")
            raise
