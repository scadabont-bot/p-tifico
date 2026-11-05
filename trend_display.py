"""Persistence and numeric helpers for engineering-value trend displays."""

from __future__ import annotations

import json
import math
import numbers
import os
import sys
import tempfile
from bisect import bisect_left
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, localcontext
from typing import Any


__all__ = [
    "default_preferences",
    "load_preferences",
    "save_preferences",
    "validate_scale",
    "scale_for_values",
    "axis_ticks",
    "format_tick",
    "nearest_sample_index",
]

_SCHEMA = "SCADA-TREND-DISPLAY-1"
_PARAMETERS = ("U", "I", "P", "Q", "f")
_MODES = ("actual", "stacked", "relative")
_MAX_INTERVALS = 100
_MAX_FLOAT_DECIMAL = Decimal.from_float(sys.float_info.max)
_MIN_POSITIVE_FLOAT = math.ulp(0.0)


def default_preferences() -> dict[str, Any]:
    """Return a fresh set of trend display preferences."""
    return {
        "schema": _SCHEMA,
        "mode": "actual",
        "parameter": "U",
        "visible_series": list(_PARAMETERS),
        "scales": {},
    }


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number.")

    decimal_value: Decimal | None = None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"{label} must be a number.")
        if "," in text:
            if "." in text or text.count(",") != 1:
                raise ValueError(
                    f"{label} must use either a comma or a dot as the decimal separator."
                )
            text = text.replace(",", ".")
        try:
            decimal_value = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"{label} must be a number.") from exc
        if not decimal_value.is_finite():
            raise ValueError(f"{label} must be finite.")
        try:
            result = float(decimal_value)
        except (OverflowError, ValueError) as exc:
            raise ValueError(f"{label} is outside the supported numeric range.") from exc
    elif isinstance(value, Decimal):
        decimal_value = value
        if not value.is_finite():
            raise ValueError(f"{label} must be finite.")
        try:
            result = float(value)
        except (OverflowError, ValueError) as exc:
            raise ValueError(f"{label} is outside the supported numeric range.") from exc
    elif isinstance(value, numbers.Real):
        try:
            result = float(value)
        except (OverflowError, ValueError) as exc:
            raise ValueError(f"{label} is outside the supported numeric range.") from exc
    else:
        raise ValueError(f"{label} must be a number.")

    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite.")
    if decimal_value is not None and decimal_value != 0 and result == 0.0:
        raise ValueError(f"{label} is too small to represent.")
    return result


def validate_scale(minimum: Any, maximum: Any, step: Any) -> dict[str, float | str]:
    """Validate manual axis fields and return their canonical numeric form."""
    lower = _number(minimum, "Minimum")
    upper = _number(maximum, "Maximum")
    increment = _number(step, "Step")

    if not lower < upper:
        raise ValueError("Minimum must be less than maximum.")
    if not increment > 0.0:
        raise ValueError("Step must be greater than zero.")

    span = upper - lower
    if not math.isfinite(span):
        raise ValueError("The scale range is too large to represent.")
    if increment > span:
        raise ValueError("Step must not be greater than the scale range.")
    if span / increment > _MAX_INTERVALS:
        raise ValueError("Step must produce no more than 100 intervals.")

    # A positive float can still be too small to move either endpoint. Such a
    # scale would create repeated ticks and labels even though its fields parse.
    if lower + increment <= lower or upper - increment >= upper:
        raise ValueError("Step is too small for this scale's numeric precision.")

    return {
        "mode": "manual",
        "minimum": lower,
        "maximum": upper,
        "step": increment,
    }


def _validate_scale_config(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError("Each scale must be an object.")
    mode = config.get("mode")
    if mode == "auto":
        if set(config) != {"mode"}:
            raise ValueError("An automatic scale may only contain its mode.")
        return {"mode": "auto"}
    if mode == "manual":
        if set(config) != {"mode", "minimum", "maximum", "step"}:
            raise ValueError("A manual scale requires minimum, maximum, and step only.")
        return validate_scale(config["minimum"], config["maximum"], config["step"])
    raise ValueError("Scale mode must be 'auto' or 'manual'.")


def _validated_preferences(preferences: Any) -> dict[str, Any]:
    if not isinstance(preferences, dict):
        raise ValueError("Trend display preferences must be an object.")
    required = {"schema", "mode", "parameter", "visible_series", "scales"}
    if set(preferences) != required:
        raise ValueError("Trend display preferences have missing or unknown fields.")
    if preferences["schema"] != _SCHEMA:
        raise ValueError("Unsupported trend display preference schema.")
    if preferences["mode"] not in _MODES:
        raise ValueError("Trend mode must be 'actual', 'stacked', or 'relative'.")
    if preferences["parameter"] not in _PARAMETERS:
        raise ValueError("Trend parameter must be one of U, I, P, Q, or f.")

    visible = preferences["visible_series"]
    if not isinstance(visible, list) or not visible:
        raise ValueError("At least one visible trend series is required.")
    if any(item not in _PARAMETERS for item in visible):
        raise ValueError("Visible trend series may only contain U, I, P, Q, and f.")
    if len(set(visible)) != len(visible):
        raise ValueError("Visible trend series must be unique.")

    scales = preferences["scales"]
    if not isinstance(scales, dict):
        raise ValueError("Trend scales must be an object.")
    validated_scales: dict[str, dict[str, dict[str, Any]]] = {}
    for equipment, equipment_scales in scales.items():
        if not isinstance(equipment, str) or not equipment:
            raise ValueError("Each scale equipment name must be a nonempty string.")
        if not isinstance(equipment_scales, dict):
            raise ValueError(f"Scales for {equipment!r} must be an object.")
        validated_equipment: dict[str, dict[str, Any]] = {}
        for parameter, config in equipment_scales.items():
            if parameter not in _PARAMETERS:
                raise ValueError("Scale parameters may only be U, I, P, Q, and f.")
            validated_equipment[parameter] = _validate_scale_config(config)
        validated_scales[equipment] = validated_equipment

    return {
        "schema": _SCHEMA,
        "mode": preferences["mode"],
        "parameter": preferences["parameter"],
        "visible_series": list(visible),
        "scales": validated_scales,
    }


def load_preferences(path: os.PathLike[str] | str) -> dict[str, Any]:
    """Load and validate preferences, returning defaults only when absent."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return default_preferences()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Trend display preferences are not valid UTF-8 JSON.") from exc
    return _validated_preferences(data)


def save_preferences(path: os.PathLike[str] | str, preferences: Any) -> None:
    """Validate and atomically replace a UTF-8 JSON preferences file."""
    validated = _validated_preferences(preferences)
    destination = os.path.abspath(os.fspath(path))
    directory = os.path.dirname(destination)
    basename = os.path.basename(destination)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=f".{basename}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = handle.name
            json.dump(validated, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def _nice_step(raw_step: Decimal) -> Decimal:
    exponent = raw_step.adjusted()
    power = Decimal(10) ** exponent
    fraction = raw_step / power
    for candidate in (Decimal("1"), Decimal("2"), Decimal("2.5"), Decimal("5"), Decimal("10")):
        if fraction <= candidate:
            return candidate * power
    return Decimal(10) * power


def _next_nice_step(step: Decimal) -> Decimal:
    return _nice_step(step * Decimal("1.0000000000000000000000000001"))


def _previous_nice_step(step: Decimal) -> Decimal:
    exponent = step.adjusted()
    power = Decimal(10) ** exponent
    fraction = step / power
    candidates = (Decimal("1"), Decimal("2"), Decimal("2.5"), Decimal("5"), Decimal("10"))
    previous = Decimal("0.5")
    for candidate in candidates:
        if candidate >= fraction:
            return previous * power
        previous = candidate
    return Decimal("5") * power


def _decimal_bounds(lower: Decimal, upper: Decimal, step: Decimal) -> tuple[Decimal, Decimal, int]:
    lower_multiple = (lower / step).to_integral_value(rounding=ROUND_FLOOR)
    upper_multiple = (upper / step).to_integral_value(rounding=ROUND_CEILING)
    bound_lower = lower_multiple * step
    bound_upper = upper_multiple * step
    intervals = int((bound_upper - bound_lower) / step)
    return bound_lower, bound_upper, intervals


def _finite_float(value: Decimal) -> float:
    if value > _MAX_FLOAT_DECIMAL:
        return sys.float_info.max
    if value < -_MAX_FLOAT_DECIMAL:
        return -sys.float_info.max
    return float(value)


def _auto_scale(lower_value: float, upper_value: float) -> tuple[float, float, float]:
    if lower_value == 0.0 and upper_value == 0.0:
        return (-1.0, 1.0, 0.5)

    with localcontext() as context:
        context.prec = 80
        lower_decimal = Decimal.from_float(lower_value)
        upper_decimal = Decimal.from_float(upper_value)
        if lower_decimal == upper_decimal:
            padding = abs(lower_decimal) * Decimal("0.05")
        else:
            padding = (upper_decimal - lower_decimal) * Decimal("0.05")
        padded_lower = lower_decimal - padding
        padded_upper = upper_decimal + padding
        raw_step = (padded_upper - padded_lower) / Decimal(5)
        step_decimal = _nice_step(raw_step)
        bound_lower, bound_upper, intervals = _decimal_bounds(
            padded_lower, padded_upper, step_decimal
        )

        while intervals > 6:
            step_decimal = _next_nice_step(step_decimal)
            bound_lower, bound_upper, intervals = _decimal_bounds(
                padded_lower, padded_upper, step_decimal
            )
        if intervals < 4:
            smaller_step = _previous_nice_step(step_decimal)
            small_lower, small_upper, small_intervals = _decimal_bounds(
                padded_lower, padded_upper, smaller_step
            )
            if 4 <= small_intervals <= 6:
                step_decimal = smaller_step
                bound_lower, bound_upper = small_lower, small_upper

        lower = _finite_float(bound_lower)
        upper = _finite_float(bound_upper)
        increment = _finite_float(step_decimal)

    if increment <= 0.0:
        increment = _MIN_POSITIVE_FLOAT

    # Decimal padding can be narrower than one binary-float spacing. Expand in
    # the representable domain so a constant series never produces zero range.
    if lower >= lower_value:
        expanded = math.nextafter(lower_value, -math.inf)
        if math.isfinite(expanded):
            lower = expanded
    if upper <= upper_value:
        expanded = math.nextafter(upper_value, math.inf)
        if math.isfinite(expanded):
            upper = expanded
    if not lower < upper:
        lower, upper = 0.0, 1.0
        increment = 0.2
    if not math.isfinite(upper - lower):
        raise ValueError("Automatic scale range is too large to represent.")

    if lower == 0.0:
        lower = 0.0
    if upper == 0.0:
        upper = 0.0
    return (lower, upper, increment)


def scale_for_values(values: Any, config: Any = None) -> tuple[float, float, float]:
    """Return manual bounds or a padded nice scale for finite actual values."""
    if config is not None:
        validated_config = _validate_scale_config(config)
        if validated_config["mode"] == "manual":
            return (
                validated_config["minimum"],
                validated_config["maximum"],
                validated_config["step"],
            )

    lower_value: float | None = None
    upper_value: float | None = None
    if values is not None:
        try:
            iterator = iter(values)
        except TypeError as exc:
            raise ValueError("Trend values must be an iterable or None.") from exc
        for value in iterator:
            if isinstance(value, bool) or not isinstance(value, (numbers.Real, Decimal)):
                continue
            try:
                numeric = float(value)
            except (OverflowError, ValueError):
                continue
            if not math.isfinite(numeric):
                continue
            if lower_value is None or numeric < lower_value:
                lower_value = numeric
            if upper_value is None or numeric > upper_value:
                upper_value = numeric

    if lower_value is None or upper_value is None:
        return (0.0, 1.0, 0.2)
    return _auto_scale(lower_value, upper_value)


def axis_ticks(minimum: Any, maximum: Any, step: Any) -> tuple[float, ...]:
    """Return bounded ascending ticks while preserving both supplied bounds."""
    lower = _number(minimum, "Minimum")
    upper = _number(maximum, "Maximum")
    increment = _number(step, "Step")
    if not lower < upper:
        raise ValueError("Minimum must be less than maximum.")
    if increment <= 0.0:
        raise ValueError("Step must be greater than zero.")

    lower_decimal = Decimal(str(lower))
    upper_decimal = Decimal(str(upper))
    step_decimal = Decimal(str(increment))
    span_decimal = upper_decimal - lower_decimal
    if step_decimal > span_decimal:
        raise ValueError("Step must not be greater than the scale range.")
    if span_decimal / step_decimal > _MAX_INTERVALS:
        raise ValueError("Step must produce no more than 100 intervals.")
    if lower + increment <= lower or upper - increment >= upper:
        raise ValueError("Step is too small for this scale's numeric precision.")
    ticks = [0.0 if lower == 0.0 else lower]
    for index in range(1, _MAX_INTERVALS + 1):
        candidate_decimal = lower_decimal + step_decimal * index
        if candidate_decimal >= upper_decimal:
            break
        candidate = float(candidate_decimal)
        if candidate == 0.0:
            candidate = 0.0
        if ticks[-1] < candidate < upper:
            ticks.append(candidate)
    endpoint = 0.0 if upper == 0.0 else upper
    if endpoint > ticks[-1]:
        ticks.append(endpoint)
    return tuple(ticks)


def format_tick(value: Any, step: Any) -> str:
    """Format an axis tick at enough precision to distinguish one step."""
    numeric = _number(value, "Tick value")
    increment = _number(step, "Tick step")
    if increment <= 0.0:
        raise ValueError("Tick step must be greater than zero.")
    if numeric == 0.0:
        numeric = 0.0

    step_decimal = Decimal(str(increment)).normalize()
    value_decimal = Decimal(str(abs(numeric))).normalize()
    step_exponent = step_decimal.as_tuple().exponent
    value_quantum_exponent = value_decimal.as_tuple().exponent
    magnitude = abs(numeric)
    scientific = magnitude != 0.0 and (magnitude < 0.0001 or magnitude >= 1_000_000_000.0)

    if scientific:
        value_exponent = value_decimal.adjusted()
        step_places = value_exponent - step_exponent
        offset_places = value_exponent - value_quantum_exponent
        decimal_places = max(0, min(16, max(step_places, offset_places)))
        rendered = f"{numeric:.{decimal_places}e}"
        mantissa, exponent = rendered.split("e")
        mantissa = mantissa.rstrip("0").rstrip(".")
        exponent_value = int(exponent)
        return f"{mantissa}e{exponent_value:+03d}"

    decimal_places = max(
        0,
        min(16, max(-step_exponent, -value_quantum_exponent)),
    )
    rendered = f"{numeric:.{decimal_places}f}"
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"-0", ""} else rendered


def nearest_sample_index(
    epochs: Sequence[int | float], target_epoch: float,
) -> int | None:
    """Find the nearest actual sample in sorted epochs; ties use the earlier one.

    The target time remains independent of the returned sample. Callers can
    move a cursor continuously without inventing interpolated measurements.
    """
    if not epochs:
        return None
    index = bisect_left(epochs, target_epoch)
    if index == len(epochs):
        return index - 1
    if index and target_epoch - epochs[index - 1] <= epochs[index] - target_epoch:
        return index - 1
    return index
