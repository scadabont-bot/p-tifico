from __future__ import annotations

import datetime as _datetime
import hashlib
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable


EVENT_COLUMNS = (
    "event_id", "source_time", "receive_time", "source_ip", "asdu", "ioa",
    "type_id", "cause", "point_key", "info_name", "physical_name", "bay_name",
    "event_kind", "previous_value", "value", "raw_value", "quality",
    "quality_text", "state_text", "severity", "alarm_enabled",
    "open_alert_enabled", "close_alert_enabled", "annunciate", "sound_path", "origin",
)


def _iso_milliseconds(value: _datetime.datetime | str | None = None) -> str:
    if isinstance(value, _datetime.datetime):
        return value.isoformat(timespec="milliseconds")
    if value:
        return str(value)
    return _datetime.datetime.now().isoformat(timespec="milliseconds")


def quality_text(quality: int) -> str:
    quality = int(quality or 0) & 0xF0
    labels = []
    if quality & 0x10:
        labels.append("BLOCKED")
    if quality & 0x20:
        labels.append("SUBSTITUTED")
    if quality & 0x40:
        labels.append("NOT TOPICAL")
    if quality & 0x80:
        labels.append("INVALID")
    return ", ".join(labels) if labels else "GOOD"


def parse_cp56_time2a(payload: bytes, fallback: _datetime.datetime | None = None) -> tuple[str, bool]:
    """Decode IEC-60870 CP56Time2a to local ISO time and validity."""
    fallback = fallback or _datetime.datetime.now()
    if len(payload) < 7:
        return _iso_milliseconds(fallback), False
    try:
        milliseconds = int(payload[0]) | (int(payload[1]) << 8)
        minute_byte = int(payload[2])
        hour_byte = int(payload[3])
        day_byte = int(payload[4])
        month_byte = int(payload[5])
        year_byte = int(payload[6])
        invalid = bool(minute_byte & 0x80)
        second, millis = divmod(milliseconds, 1000)
        value = _datetime.datetime(
            2000 + (year_byte & 0x7F),
            month_byte & 0x0F,
            day_byte & 0x1F,
            hour_byte & 0x1F,
            minute_byte & 0x3F,
            second,
            millis * 1000,
        )
        return _iso_milliseconds(value), not invalid
    except (TypeError, ValueError, OverflowError):
        return _iso_milliseconds(fallback), False


def parse_cp24_time2a(payload: bytes, fallback: _datetime.datetime | None = None) -> tuple[str, bool]:
    """Decode CP24Time2a by selecting the hour closest to receive time."""
    fallback = fallback or _datetime.datetime.now()
    if len(payload) < 3:
        return _iso_milliseconds(fallback), False
    try:
        milliseconds = int(payload[0]) | (int(payload[1]) << 8)
        minute_byte = int(payload[2])
        invalid = bool(minute_byte & 0x80)
        second, millis = divmod(milliseconds, 1000)
        minute = minute_byte & 0x3F
        candidate = fallback.replace(minute=minute, second=second, microsecond=millis * 1000)
        alternatives = (
            candidate - _datetime.timedelta(hours=1),
            candidate,
            candidate + _datetime.timedelta(hours=1),
        )
        value = min(alternatives, key=lambda item: abs((item - fallback).total_seconds()))
        return _iso_milliseconds(value), not invalid
    except (TypeError, ValueError, OverflowError):
        return _iso_milliseconds(fallback), False


class EventStore:
    """Thread-safe durable SOE, digital-alarm, and acknowledgement store."""

    def __init__(self, database_path: str | os.PathLike[str]):
        self.database_path = str(database_path)
        self._schema_lock = threading.RLock()
        self._schema_ready = False

    def _connect(self) -> sqlite3.Connection:
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS soe_events (
                        event_id TEXT PRIMARY KEY,
                        source_time TEXT NOT NULL,
                        receive_time TEXT NOT NULL,
                        source_ip TEXT NOT NULL,
                        asdu INTEGER NOT NULL,
                        ioa INTEGER NOT NULL,
                        type_id INTEGER NOT NULL,
                        cause INTEGER NOT NULL,
                        point_key TEXT NOT NULL,
                        info_name TEXT NOT NULL DEFAULT '',
                        physical_name TEXT NOT NULL DEFAULT '',
                        bay_name TEXT NOT NULL DEFAULT '',
                        event_kind TEXT NOT NULL,
                        previous_value INTEGER,
                        value INTEGER,
                        raw_value INTEGER,
                        quality INTEGER NOT NULL DEFAULT 0,
                        quality_text TEXT NOT NULL DEFAULT 'GOOD',
                        state_text TEXT NOT NULL DEFAULT '',
                        severity TEXT NOT NULL DEFAULT 'NORMAL',
                        alarm_enabled INTEGER NOT NULL DEFAULT 0,
                        open_alert_enabled INTEGER NOT NULL DEFAULT 0,
                        close_alert_enabled INTEGER NOT NULL DEFAULT 0,
                        annunciate INTEGER NOT NULL DEFAULT 0,
                        sound_path TEXT NOT NULL DEFAULT '',
                        origin TEXT NOT NULL DEFAULT 'LOCAL'
                    );
                    CREATE INDEX IF NOT EXISTS idx_soe_source_time
                        ON soe_events(source_time DESC);
                    CREATE INDEX IF NOT EXISTS idx_soe_point
                        ON soe_events(source_ip, asdu, ioa, point_key, source_time DESC);
                    CREATE INDEX IF NOT EXISTS idx_soe_bay
                        ON soe_events(bay_name, source_time DESC);

                    CREATE TABLE IF NOT EXISTS point_states (
                        source_ip TEXT NOT NULL,
                        asdu INTEGER NOT NULL,
                        ioa INTEGER NOT NULL,
                        point_key TEXT NOT NULL,
                        value INTEGER,
                        quality INTEGER NOT NULL DEFAULT 0,
                        source_time TEXT NOT NULL,
                        receive_time TEXT NOT NULL,
                        event_id TEXT NOT NULL DEFAULT '',
                        PRIMARY KEY (source_ip, asdu, ioa, point_key)
                    );

                    CREATE TABLE IF NOT EXISTS status_alarms (
                        alarm_key TEXT PRIMARY KEY,
                        event_id TEXT NOT NULL,
                        source_time TEXT NOT NULL,
                        receive_time TEXT NOT NULL,
                        source_ip TEXT NOT NULL,
                        asdu INTEGER NOT NULL,
                        ioa INTEGER NOT NULL,
                        device TEXT NOT NULL,
                        description TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        state_text TEXT NOT NULL,
                        annunciate INTEGER NOT NULL DEFAULT 0,
                        sound_path TEXT NOT NULL DEFAULT '',
                        active INTEGER NOT NULL DEFAULT 1
                    );
                    CREATE INDEX IF NOT EXISTS idx_status_alarm_active
                        ON status_alarms(active, severity);

                    CREATE TABLE IF NOT EXISTS alarm_acknowledgements (
                        alarm_key TEXT PRIMARY KEY,
                        cycle_id TEXT NOT NULL,
                        acknowledged INTEGER NOT NULL DEFAULT 0,
                        acknowledged_at TEXT NOT NULL DEFAULT '',
                        acknowledged_by TEXT NOT NULL DEFAULT ''
                    );
                    """
                )
            self._schema_ready = True

    @staticmethod
    def _event_id(event: dict[str, Any]) -> str:
        identity = "|".join(
            str(event.get(name, ""))
            for name in (
                "source_ip", "asdu", "ioa", "point_key", "source_time",
                "receive_time" if not event.get("source_timestamp_valid") else "source_time",
                "raw_value", "value", "quality", "type_id", "cause",
            )
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _normalized_event(event: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(event)
        normalized["source_time"] = _iso_milliseconds(normalized.get("source_time"))
        normalized["receive_time"] = _iso_milliseconds(normalized.get("receive_time"))
        normalized["source_ip"] = str(normalized.get("source_ip", ""))
        normalized["asdu"] = int(normalized.get("asdu", 0) or 0)
        normalized["ioa"] = int(normalized.get("ioa", 0) or 0)
        normalized["type_id"] = int(normalized.get("type_id", 0) or 0)
        normalized["cause"] = int(normalized.get("cause", 0) or 0)
        normalized["point_key"] = str(normalized.get("point_key", ""))
        normalized["info_name"] = str(normalized.get("info_name", ""))
        normalized["physical_name"] = str(normalized.get("physical_name", ""))
        normalized["bay_name"] = str(normalized.get("bay_name", ""))
        normalized["event_kind"] = str(normalized.get("event_kind", "CHANGE")).upper()
        for name in ("previous_value", "value", "raw_value"):
            value = normalized.get(name)
            normalized[name] = None if value is None else int(value)
        normalized["quality"] = int(normalized.get("quality", 0) or 0) & 0xF0
        normalized["quality_text"] = str(
            normalized.get("quality_text") or quality_text(normalized["quality"])
        )
        normalized["state_text"] = str(normalized.get("state_text", ""))
        normalized["severity"] = str(normalized.get("severity", "NORMAL")).upper()
        for name in (
            "alarm_enabled", "open_alert_enabled", "close_alert_enabled", "annunciate",
        ):
            normalized[name] = int(bool(normalized.get(name, False)))
        normalized["sound_path"] = str(normalized.get("sound_path", ""))
        normalized["origin"] = str(normalized.get("origin", "LOCAL"))
        normalized["event_id"] = str(
            normalized.get("event_id") or EventStore._event_id(normalized)
        )
        return normalized

    @staticmethod
    def _insert_event(connection: sqlite3.Connection, event: dict[str, Any]) -> bool:
        placeholders = ",".join("?" for _ in EVENT_COLUMNS)
        cursor = connection.execute(
            f"INSERT OR IGNORE INTO soe_events ({','.join(EVENT_COLUMNS)}) VALUES ({placeholders})",
            tuple(event.get(name) for name in EVENT_COLUMNS),
        )
        return cursor.rowcount > 0

    def record_observation(
        self, event: dict[str, Any], *, force_event: bool = False,
    ) -> dict[str, Any] | None:
        """Atomically update point state and persist only new observations."""
        self._ensure_schema()
        current = self._normalized_event(event)
        key = (
            current["source_ip"], current["asdu"], current["ioa"], current["point_key"],
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous_row = connection.execute(
                """
                SELECT value, quality FROM point_states
                WHERE source_ip=? AND asdu=? AND ioa=? AND point_key=?
                """,
                key,
            ).fetchone()
            previous_value = previous_row["value"] if previous_row is not None else None
            previous_quality = previous_row["quality"] if previous_row is not None else None
            current["previous_value"] = previous_value

            valid_value = current["value"] is not None and not (current["quality"] & 0x80)
            changed = (
                previous_row is None
                or previous_value != current["value"]
                or previous_quality != current["quality"]
            )
            if previous_row is None:
                current["event_kind"] = "INITIAL"
            elif previous_value != current["value"]:
                current["event_kind"] = "CHANGE"
            elif previous_quality != current["quality"]:
                current["event_kind"] = "QUALITY"
            else:
                current["event_kind"] = "EVENT"

            current["event_id"] = self._event_id(current)
            if valid_value:
                connection.execute(
                    """
                    INSERT INTO point_states (
                        source_ip, asdu, ioa, point_key, value, quality,
                        source_time, receive_time, event_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_ip, asdu, ioa, point_key) DO UPDATE SET
                        value=excluded.value,
                        quality=excluded.quality,
                        source_time=excluded.source_time,
                        receive_time=excluded.receive_time,
                        event_id=excluded.event_id
                    """,
                    key + (
                        current["value"], current["quality"], current["source_time"],
                        current["receive_time"], current["event_id"],
                    ),
                )
            if not changed and not force_event:
                return None
            if not self._insert_event(connection, current):
                return None
        return current

    def insert_remote_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Insert one relayed event exactly once and advance its durable point state."""
        self._ensure_schema()
        remote = dict(event)
        remote["origin"] = str(remote.get("origin") or "RELAY")
        normalized = self._normalized_event(remote)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self._insert_event(connection, normalized):
                return None
            if normalized["value"] is not None and not (normalized["quality"] & 0x80):
                connection.execute(
                    """
                    INSERT INTO point_states (
                        source_ip, asdu, ioa, point_key, value, quality,
                        source_time, receive_time, event_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_ip, asdu, ioa, point_key) DO UPDATE SET
                        value=excluded.value,
                        quality=excluded.quality,
                        source_time=excluded.source_time,
                        receive_time=excluded.receive_time,
                        event_id=excluded.event_id
                    WHERE excluded.source_time >= point_states.source_time
                    """,
                    (
                        normalized["source_ip"], normalized["asdu"], normalized["ioa"],
                        normalized["point_key"], normalized["value"], normalized["quality"],
                        normalized["source_time"], normalized["receive_time"],
                        normalized["event_id"],
                    ),
                )
        return normalized

    @staticmethod
    def _start_alarm_cycle_conn(
        connection: sqlite3.Connection, alarm_key: str, cycle_id: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO alarm_acknowledgements (
                alarm_key, cycle_id, acknowledged, acknowledged_at, acknowledged_by
            ) VALUES (?, ?, 0, '', '')
            ON CONFLICT(alarm_key) DO UPDATE SET
                cycle_id=excluded.cycle_id,
                acknowledged=CASE
                    WHEN alarm_acknowledgements.cycle_id=excluded.cycle_id
                    THEN alarm_acknowledgements.acknowledged ELSE 0 END,
                acknowledged_at=CASE
                    WHEN alarm_acknowledgements.cycle_id=excluded.cycle_id
                    THEN alarm_acknowledgements.acknowledged_at ELSE '' END,
                acknowledged_by=CASE
                    WHEN alarm_acknowledgements.cycle_id=excluded.cycle_id
                    THEN alarm_acknowledgements.acknowledged_by ELSE '' END
            """,
            (str(alarm_key), str(cycle_id)),
        )

    def start_alarm_cycle(self, alarm_key: str, cycle_id: str) -> None:
        self._ensure_schema()
        with self._connect() as connection:
            self._start_alarm_cycle_conn(connection, alarm_key, cycle_id)

    def clear_alarm_cycle(self, alarm_key: str) -> None:
        self._ensure_schema()
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM alarm_acknowledgements WHERE alarm_key=?", (str(alarm_key),),
            )

    def apply_status_alarm(
        self, event: dict[str, Any], alarm_key: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """Apply OPEN/CLOSE alert flags; return ACTIVE, UPDATED, CLEARED, or NONE."""
        self._ensure_schema()
        if event.get("value") not in (0, 1):
            return "NONE", None
        normalized = self._normalized_event(event)
        enabled = bool(
            normalized["close_alert_enabled"]
            if normalized["value"] == 1
            else normalized["open_alert_enabled"]
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT * FROM status_alarms WHERE alarm_key=?", (str(alarm_key),),
            ).fetchone()
            previous_dict = dict(previous) if previous is not None else None
            if enabled:
                action = "ACTIVE" if previous is None or not previous["active"] else "UPDATED"
                connection.execute(
                    """
                    INSERT INTO status_alarms (
                        alarm_key, event_id, source_time, receive_time, source_ip,
                        asdu, ioa, device, description, severity, state_text,
                        annunciate, sound_path, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(alarm_key) DO UPDATE SET
                        event_id=excluded.event_id,
                        source_time=excluded.source_time,
                        receive_time=excluded.receive_time,
                        source_ip=excluded.source_ip,
                        asdu=excluded.asdu,
                        ioa=excluded.ioa,
                        device=excluded.device,
                        description=excluded.description,
                        severity=excluded.severity,
                        state_text=excluded.state_text,
                        annunciate=excluded.annunciate,
                        sound_path=excluded.sound_path,
                        active=1
                    """,
                    (
                        str(alarm_key), normalized["event_id"], normalized["source_time"],
                        normalized["receive_time"], normalized["source_ip"],
                        normalized["asdu"], normalized["ioa"],
                        normalized["bay_name"] or normalized["source_ip"],
                        str(normalized.get("alarm_description") or normalized["physical_name"]),
                        normalized["severity"], normalized["state_text"],
                        normalized["annunciate"], normalized["sound_path"],
                    ),
                )
                self._start_alarm_cycle_conn(connection, str(alarm_key), normalized["event_id"])
                return action, normalized

            if previous is not None and previous["active"]:
                connection.execute(
                    "UPDATE status_alarms SET active=0 WHERE alarm_key=?", (str(alarm_key),),
                )
                connection.execute(
                    "DELETE FROM alarm_acknowledgements WHERE alarm_key=?", (str(alarm_key),),
                )
                return "CLEARED", previous_dict
        return "NONE", None

    def active_status_alarms(self) -> list[dict[str, Any]]:
        self._ensure_schema()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT a.*, COALESCE(k.acknowledged, 0) AS acknowledged,
                       COALESCE(k.acknowledged_at, '') AS acknowledged_at,
                       COALESCE(k.acknowledged_by, '') AS acknowledged_by
                FROM status_alarms a
                LEFT JOIN alarm_acknowledgements k ON k.alarm_key=a.alarm_key
                WHERE a.active=1
                ORDER BY a.source_time DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def sync_remote_status_alarms(
        self, alarms: Iterable[dict[str, Any]], remote_source_ips: Iterable[str],
    ) -> int:
        """Converge active digital alarms from the paired SCADA node."""
        self._ensure_schema()
        rows = [dict(row) for row in alarms if isinstance(row, dict)]
        remote_ips = tuple(
            dict.fromkeys(str(ip) for ip in remote_source_ips if str(ip))
        )
        incoming_keys = {
            str(row.get("alarm_key", ""))
            for row in rows
            if str(row.get("alarm_key", ""))
        }
        changed = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if remote_ips:
                existing = connection.execute(
                    f"SELECT alarm_key FROM status_alarms WHERE active=1 "
                    f"AND source_ip IN ({','.join('?' for _ in remote_ips)})",
                    remote_ips,
                ).fetchall()
                for item in existing:
                    if item["alarm_key"] not in incoming_keys:
                        connection.execute(
                            "UPDATE status_alarms SET active=0 WHERE alarm_key=?",
                            (item["alarm_key"],),
                        )
                        connection.execute(
                            "DELETE FROM alarm_acknowledgements WHERE alarm_key=?",
                            (item["alarm_key"],),
                        )
                        changed += 1
            for row in rows:
                alarm_key = str(row.get("alarm_key", ""))
                source_ip = str(row.get("source_ip", ""))
                if not alarm_key or (remote_ips and source_ip not in remote_ips):
                    continue
                connection.execute(
                    """
                    INSERT INTO status_alarms (
                        alarm_key, event_id, source_time, receive_time, source_ip,
                        asdu, ioa, device, description, severity, state_text,
                        annunciate, sound_path, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(alarm_key) DO UPDATE SET
                        event_id=excluded.event_id,
                        source_time=excluded.source_time,
                        receive_time=excluded.receive_time,
                        source_ip=excluded.source_ip,
                        asdu=excluded.asdu,
                        ioa=excluded.ioa,
                        device=excluded.device,
                        description=excluded.description,
                        severity=excluded.severity,
                        state_text=excluded.state_text,
                        annunciate=excluded.annunciate,
                        sound_path=excluded.sound_path,
                        active=1
                    """,
                    (
                        alarm_key, str(row.get("event_id", "")),
                        str(row.get("source_time", "")), str(row.get("receive_time", "")),
                        source_ip, int(row.get("asdu", 0) or 0),
                        int(row.get("ioa", 0) or 0), str(row.get("device", source_ip)),
                        str(row.get("description", "")),
                        str(row.get("severity", "WARNING")).upper(),
                        str(row.get("state_text", "")),
                        int(bool(row.get("annunciate", False))),
                        str(row.get("sound_path", "")),
                    ),
                )
                self._start_alarm_cycle_conn(
                    connection, alarm_key, str(row.get("event_id", ""))
                )
                if row.get("acknowledged"):
                    connection.execute(
                        """
                        UPDATE alarm_acknowledgements
                        SET acknowledged=1, acknowledged_at=?, acknowledged_by=?
                        WHERE alarm_key=?
                        """,
                        (
                            str(row.get("acknowledged_at", "")),
                            str(row.get("acknowledged_by", "")),
                            alarm_key,
                        ),
                    )
                changed += 1
        return changed

    def acknowledge(self, alarm_keys: Iterable[str], operator: str) -> int:
        self._ensure_schema()
        keys = tuple(dict.fromkeys(str(key) for key in alarm_keys if str(key)))
        if not keys:
            return 0
        stamp = _iso_milliseconds()
        count = 0
        with self._connect() as connection:
            for key in keys:
                cursor = connection.execute(
                    """
                    UPDATE alarm_acknowledgements
                    SET acknowledged=1, acknowledged_at=?, acknowledged_by=?
                    WHERE alarm_key=?
                    """,
                    (stamp, str(operator or "Operator"), key),
                )
                count += cursor.rowcount
        return count

    def acknowledgement(self, alarm_key: str) -> dict[str, Any]:
        self._ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM alarm_acknowledgements WHERE alarm_key=?", (str(alarm_key),),
            ).fetchone()
        return dict(row) if row is not None else {
            "alarm_key": str(alarm_key), "cycle_id": "", "acknowledged": 0,
            "acknowledged_at": "", "acknowledged_by": "",
        }

    def recent_events(
        self, limit: int = 500, source_ips: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        self._ensure_schema()
        limit = max(1, min(int(limit), 5000))
        parameters: list[Any] = []
        where = ""
        ips = tuple(dict.fromkeys(str(ip) for ip in (source_ips or ()) if str(ip)))
        if ips:
            where = f"WHERE source_ip IN ({','.join('?' for _ in ips)})"
            parameters.extend(ips)
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM soe_events {where} ORDER BY source_time DESC, event_id DESC LIMIT ?",
                tuple(parameters),
            ).fetchall()
        return [dict(row) for row in rows]

    def query_events(
        self, *, start_date: str = "", end_date: str = "", device: str = "",
        state: str = "", severity: str = "", search: str = "", limit: int = 5000,
    ) -> list[dict[str, Any]]:
        self._ensure_schema()
        clauses = []
        parameters: list[Any] = []
        if start_date:
            clauses.append("substr(source_time, 1, 10) >= ?")
            parameters.append(str(start_date))
        if end_date:
            clauses.append("substr(source_time, 1, 10) <= ?")
            parameters.append(str(end_date))
        if device:
            clauses.append("(source_ip=? OR bay_name=?)")
            parameters.extend((str(device), str(device)))
        if state:
            clauses.append("state_text=?")
            parameters.append(str(state))
        if severity:
            clauses.append("severity=?")
            parameters.append(str(severity).upper())
        if search:
            token = f"%{str(search).casefold()}%"
            clauses.append(
                "(lower(info_name) LIKE ? OR lower(physical_name) LIKE ? "
                "OR lower(bay_name) LIKE ? OR lower(event_id) LIKE ?)"
            )
            parameters.extend((token, token, token, token))
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.append(max(1, min(int(limit), 50000)))
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM soe_events {where} "
                "ORDER BY source_time DESC, receive_time DESC, event_id DESC LIMIT ?",
                tuple(parameters),
            ).fetchall()
        return [dict(row) for row in rows]

    def annunciation_for_event_ids(self, event_ids: Iterable[str]) -> list[dict[str, Any]]:
        self._ensure_schema()
        ids = tuple(dict.fromkeys(str(item) for item in event_ids if str(item)))
        if not ids:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT event_id, annunciate, sound_path FROM soe_events "
                f"WHERE event_id IN ({','.join('?' for _ in ids)})",
                ids,
            ).fetchall()
        return [dict(row) for row in rows]

    def health_check(self) -> dict[str, int]:
        self._ensure_schema()
        with self._connect() as connection:
            event_count = connection.execute("SELECT count(*) FROM soe_events").fetchone()[0]
            active_count = connection.execute(
                "SELECT count(*) FROM status_alarms WHERE active=1"
            ).fetchone()[0]
        return {"events": int(event_count), "active_status_alarms": int(active_count)}
