from __future__ import annotations

import copy
import json
import math
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA = "SCADA-TIFICO-CBZ8000-APPLICATION-1"
CATEGORIES = ("analog", "status", "pulse", "soe", "telecontrol", "sector")
CATEGORY_LABELS = {
    "device": "Device",
    "analog": "Analog",
    "status": "Status",
    "pulse": "Pulse",
    "soe": "SOE",
    "telecontrol": "Telecontrol",
    "sector": "Sector",
}
DECLARED_COUNT_FIELDS = {
    "analog": "Device_YC_count",
    "status": "Device_YX_count",
    "pulse": "Device_YM_count",
    "soe": "Device_SOE_count",
    "telecontrol": "Device_YK_count",
}
BAY_COUNT_FIELDS = {
    "analog": "Jiange_YC_count",
    "status": "Jiange_YX_count",
    "pulse": "Jiange_YM_count",
    "soe": "Jiange_SOE_count",
    "telecontrol": "Jiange_YK_count",
}


def load_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if config.get("schema") != SCHEMA:
        raise ValueError("Unsupported CBZ8000 application-data schema")
    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    schemas = config.get("table_schemas")
    configured_devices = config.get("device_manager", {}).get("devices")
    if not isinstance(schemas, dict) or not isinstance(configured_devices, list):
        raise ValueError("CBZ8000 application data is incomplete")
    required = {"device", "bay", *CATEGORIES}
    missing = sorted(required.difference(schemas))
    if missing:
        raise ValueError(f"Missing CBZ8000 table schemas: {', '.join(missing)}")

    addresses: set[int] = set()
    bay_ids: set[int] = set()
    for item in configured_devices:
        device = item.get("device", {})
        bay = item.get("bay", {})
        address = int(device.get("Device_addr", 0))
        bay_id = int(bay.get("Jiange_id", 0))
        if address <= 0 or bay_id <= 0:
            raise ValueError("Every CBZ8000 device requires Device_addr and Jiange_id")
        if address in addresses or bay_id in bay_ids:
            raise ValueError(f"Duplicate CBZ8000 device identity: {address}/{bay_id}")
        if int(device.get("Device_jiange", 0)) != bay_id:
            raise ValueError(f"Device {address} does not reference bay {bay_id}")
        addresses.add(address)
        bay_ids.add(bay_id)

        sections = item.get("sections")
        if not isinstance(sections, dict):
            raise ValueError(f"Missing point sections for device {address}")
        for category in CATEGORIES:
            rows = sections.get(category)
            if not isinstance(rows, list):
                raise ValueError(f"Invalid {category} section for device {address}")
            address_field = "Device_Addr" if category == "sector" else "device_addr"
            identity_field = (
                "Wholecall_Sector" if category == "sector" else "Info_index"
            )
            identities: set[int] = set()
            for record in rows:
                if int(record.get(address_field, 0)) != address:
                    raise ValueError(
                        f"{category} record does not reference device {address}"
                    )
                if int(record.get("JianGe_id", 0)) != bay_id:
                    raise ValueError(
                        f"{category} record does not reference bay {bay_id}"
                    )
                identity = record.get(identity_field)
                if identity is None:
                    raise ValueError(
                        f"{category} record requires {identity_field}"
                    )
                numeric_identity = int(identity)
                if numeric_identity in identities:
                    raise ValueError(
                        f"Duplicate {identity_field} {numeric_identity} "
                        f"for device {address}"
                    )
                identities.add(numeric_identity)


def save_config(config: dict[str, Any], path: str | Path, backup: bool = True) -> None:
    validate_config(config)
    target = Path(path)
    if backup and target.exists():
        backup_dir = target.parent / "Backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        shutil.copy2(target, backup_dir / f"CBZ8000AppData-{stamp}.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=target.parent, delete=False, suffix=".tmp"
    ) as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, target)


def devices(config: dict[str, Any]) -> list[dict[str, Any]]:
    return config["device_manager"]["devices"]


def device_label(item: dict[str, Any]) -> str:
    device = item.get("device", {})
    bay = item.get("bay", {})
    address = device.get("Device_addr", "")
    bay_name = str(bay.get("Jiange_name", "")).strip()
    device_name = str(device.get("Device_name", "")).strip()
    return f"{address}. {bay_name} — {device_name}"


def table_definition(config: dict[str, Any], category: str) -> dict[str, Any]:
    key = category.lower()
    if key not in {"device", "bay", *CATEGORIES}:
        raise KeyError(f"Unsupported Device Manager category: {category}")
    return config["table_schemas"][key]


def field_names(config: dict[str, Any], category: str) -> tuple[str, ...]:
    definition = table_definition(config, category)
    return tuple(field["name"] for field in definition["fields"])


def field_types(config: dict[str, Any], category: str) -> dict[str, str]:
    definition = table_definition(config, category)
    return {field["name"]: field["sql_type"] for field in definition["fields"]}


def records(
    config: dict[str, Any], category: str, device_index: int | None = None
) -> list[dict[str, Any]]:
    key = category.lower()
    rows = devices(config)
    if key == "device":
        return [item["device"] for item in rows]
    if key == "bay":
        return [item["bay"] for item in rows]
    if device_index is None or not 0 <= device_index < len(rows):
        return []
    return rows[device_index]["sections"][key]


def format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        nearest_integer = round(value)
        if math.isclose(
            value,
            nearest_integer,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            return str(nearest_integer)
        return format(value, ".12g")
    return str(value)


def record_values(
    config: dict[str, Any], category: str, record: dict[str, Any]
) -> tuple[str, ...]:
    return tuple(
        format_value(record.get(field))
        for field in field_names(config, category)
    )


def _coerce(sql_type: str, value: Any) -> Any:
    if value is None:
        return None
    text = str(value).strip()
    base_type = sql_type.split("(", 1)[0].lower()
    if text == "":
        return "" if base_type in {"char", "varchar", "nchar", "nvarchar"} else None
    if base_type in {"tinyint", "smallint", "int", "bigint"}:
        return int(text, 10)
    if base_type in {"real", "float", "money", "smallmoney", "decimal", "numeric"}:
        return float(text)
    if base_type == "bit":
        normalized = text.casefold()
        if normalized in {"1", "-1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"Invalid bit value: {value}")
    return text


def _coerce_record(
    config: dict[str, Any], category: str, values: dict[str, Any]
) -> dict[str, Any]:
    fields = field_names(config, category)
    types = field_types(config, category)
    return {
        field: _coerce(types[field], values.get(field, ""))
        for field in fields
    }


def _replace_config(
    config: dict[str, Any], candidate: dict[str, Any]
) -> None:
    validate_config(candidate)
    config.clear()
    config.update(candidate)


def _sync_declared_counts(
    item: dict[str, Any], categories: tuple[str, ...] = CATEGORIES
) -> None:
    for category in categories:
        count = len(item["sections"][category])
        device_field = DECLARED_COUNT_FIELDS.get(category)
        bay_field = BAY_COUNT_FIELDS.get(category)
        if device_field:
            item["device"][device_field] = count
        if bay_field:
            item["bay"][bay_field] = count


def _retarget_sections(item: dict[str, Any]) -> None:
    address = int(item["device"]["Device_addr"])
    bay_id = int(item["bay"]["Jiange_id"])
    item["device"]["Device_jiange"] = bay_id
    for category in CATEGORIES:
        address_field = "Device_Addr" if category == "sector" else "device_addr"
        for record in item["sections"][category]:
            record[address_field] = address
            record["JianGe_id"] = bay_id


def next_identity_value(
    config: dict[str, Any], category: str, device_index: int
) -> int:
    key = category.lower()
    identity_field = "Wholecall_Sector" if key == "sector" else "Info_index"
    values = [
        int(record[identity_field])
        for record in records(config, key, device_index)
        if record.get(identity_field) is not None
    ]
    return max(values, default=0) + 1


def update_record(
    config: dict[str, Any], category: str, values: dict[str, Any],
    device_index: int | None, record_index: int | None,
) -> int:
    key = category.lower()
    if key not in CATEGORIES:
        raise ValueError("Only point records can be edited with update_record")
    if device_index is None or not 0 <= device_index < len(devices(config)):
        raise ValueError("Select a device before editing a point")

    candidate = copy.deepcopy(config)
    configured_device = devices(candidate)[device_index]
    rows = records(candidate, key, device_index)
    record = _coerce_record(candidate, key, values)
    address = int(configured_device["device"]["Device_addr"])
    bay_id = int(configured_device["device"]["Device_jiange"])
    address_field = "Device_Addr" if key == "sector" else "device_addr"
    record[address_field] = address
    record["JianGe_id"] = bay_id

    identity_field = "Wholecall_Sector" if key == "sector" else "Info_index"
    identity = record.get(identity_field)
    if identity is None:
        raise ValueError(f"{identity_field} wajib diisi")
    for index, existing in enumerate(rows):
        if index != record_index and existing.get(identity_field) == identity:
            raise ValueError(
                f"{identity_field} {identity} sudah digunakan pada device ini"
            )

    if isinstance(record_index, int) and 0 <= record_index < len(rows):
        rows[record_index] = record
    else:
        rows.append(record)
    rows.sort(
        key=lambda row: (
            row.get(identity_field) is None,
            row.get(identity_field) or 0,
        )
    )
    result = rows.index(record)
    _sync_declared_counts(configured_device, (key,))
    refresh_counts(candidate)
    _replace_config(config, candidate)
    return result


def update_device(
    config: dict[str, Any],
    device_values: dict[str, Any],
    bay_values: dict[str, Any],
    device_index: int | None = None,
    copy_from_index: int | None = None,
) -> int:
    candidate = copy.deepcopy(config)
    configured_devices = devices(candidate)
    device_record = _coerce_record(candidate, "device", device_values)
    bay_record = _coerce_record(candidate, "bay", bay_values)
    address = int(device_record.get("Device_addr") or 0)
    bay_id = int(bay_record.get("Jiange_id") or 0)
    if address <= 0 or bay_id <= 0:
        raise ValueError("Device_addr dan Jiange_id harus lebih dari nol")
    device_record["Device_jiange"] = bay_id

    for index, item in enumerate(configured_devices):
        if index == device_index:
            continue
        if int(item["device"]["Device_addr"]) == address:
            raise ValueError(f"Device_addr {address} sudah digunakan")
        if int(item["bay"]["Jiange_id"]) == bay_id:
            raise ValueError(f"Jiange_id {bay_id} sudah digunakan")

    if device_index is not None:
        if not 0 <= device_index < len(configured_devices):
            raise IndexError("Select a valid device")
        sections = configured_devices[device_index]["sections"]
    elif copy_from_index is not None:
        if not 0 <= copy_from_index < len(configured_devices):
            raise IndexError("Select a valid source device")
        sections = copy.deepcopy(configured_devices[copy_from_index]["sections"])
    else:
        sections = {category: [] for category in CATEGORIES}

    item = {
        "device": device_record,
        "bay": bay_record,
        "sections": sections,
    }
    _retarget_sections(item)
    _sync_declared_counts(item)
    if device_index is None:
        configured_devices.append(item)
    else:
        configured_devices[device_index] = item
    configured_devices.sort(key=lambda entry: int(entry["device"]["Device_addr"]))
    result = configured_devices.index(item)
    refresh_counts(candidate)
    _replace_config(config, candidate)
    return result


def delete_device(config: dict[str, Any], device_index: int) -> None:
    if len(devices(config)) <= 1:
        raise ValueError("At least one device must remain")
    candidate = copy.deepcopy(config)
    configured_devices = devices(candidate)
    if not 0 <= device_index < len(configured_devices):
        raise IndexError("Select a valid device to delete")
    del configured_devices[device_index]
    refresh_counts(candidate)
    _replace_config(config, candidate)


def delete_record(
    config: dict[str, Any], category: str, device_index: int, record_index: int
) -> None:
    key = category.lower()
    if key not in CATEGORIES:
        raise ValueError("Only point records can be deleted")
    candidate = copy.deepcopy(config)
    configured_device = devices(candidate)[device_index]
    rows = records(candidate, key, device_index)
    if not 0 <= record_index < len(rows):
        raise IndexError("Select a valid record to delete")
    del rows[record_index]
    _sync_declared_counts(configured_device, (key,))
    refresh_counts(candidate)
    _replace_config(config, candidate)


def copy_record(
    config: dict[str, Any],
    category: str,
    source_device_index: int,
    source_record_index: int,
    target_device_index: int,
) -> int:
    key = category.lower()
    source_rows = records(config, key, source_device_index)
    if not 0 <= source_record_index < len(source_rows):
        raise IndexError("Select a valid source record")
    cloned = copy.deepcopy(source_rows[source_record_index])
    identity_field = "Wholecall_Sector" if key == "sector" else "Info_index"
    cloned[identity_field] = next_identity_value(
        config, key, target_device_index
    )
    return update_record(config, key, cloned, target_device_index, None)


def apply_template(
    config: dict[str, Any],
    source_device_index: int,
    target_device_index: int,
    categories: tuple[str, ...],
    replace: bool,
) -> None:
    if source_device_index == target_device_index:
        raise ValueError("Source and target devices must be different")
    candidate = copy.deepcopy(config)
    configured_devices = devices(candidate)
    source = configured_devices[source_device_index]
    target = configured_devices[target_device_index]
    for category in categories:
        if category not in CATEGORIES:
            raise ValueError(f"Unsupported template category: {category}")
        incoming = copy.deepcopy(source["sections"][category])
        if replace:
            target["sections"][category] = incoming
        else:
            target_rows = target["sections"][category]
            identity_field = (
                "Wholecall_Sector" if category == "sector" else "Info_index"
            )
            next_identity = max(
                (
                    int(row[identity_field])
                    for row in target_rows
                    if row.get(identity_field) is not None
                ),
                default=0,
            ) + 1
            for record in incoming:
                record[identity_field] = next_identity
                next_identity += 1
                target_rows.append(record)
    _retarget_sections(target)
    _sync_declared_counts(target, categories)
    refresh_counts(candidate)
    _replace_config(config, candidate)


def declared_count(item: dict[str, Any], category: str) -> int | None:
    field = DECLARED_COUNT_FIELDS.get(category.lower())
    if field is None:
        return None
    value = item.get("device", {}).get(field)
    return None if value is None else int(value)


def refresh_counts(config: dict[str, Any]) -> dict[str, int]:
    current = {"devices": len(devices(config)), "bays": len(devices(config))}
    for category in CATEGORIES:
        current[category] = sum(len(item["sections"][category]) for item in devices(config))
    current["point_records"] = sum(current[name] for name in CATEGORIES if name != "sector")
    current["total_records"] = current["devices"] + current["point_records"] + current["sector"]
    config["counts"] = current
    return copy.deepcopy(current)
