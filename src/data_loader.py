"""Load and validate seed data from a JSON file.

The loader is strict about its input: missing top-level keys or missing
required fields on a record raise `SeedDataError` with a message that
points at the problem. Failing fast here means the rest of the codebase
can trust that a `SeedData` object is well-formed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.models import Customer, Location, SeedData, Technician


class SeedDataError(Exception):
    """Raised when seed data is missing, malformed, or fails validation."""


_REQUIRED_TOP_LEVEL_KEYS = ("Customer_Profiles", "Location_Profiles", "Technician_Profiles")
_REQUIRED_CUSTOMER_FIELDS = ("id", "name", "contact")
_REQUIRED_LOCATION_FIELDS = ("id", "name", "address")
_REQUIRED_TECHNICIAN_FIELDS = ("id", "name", "zones", "business_units")


def load_seed(path: str | Path) -> SeedData:
    """Load seed data from a JSON file and return a validated SeedData.

    Raises:
        SeedDataError: if the file is missing, invalid JSON, or fails validation.
    """
    path = Path(path)

    if not path.exists():
        raise SeedDataError(f"Seed file not found: {path}")

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise SeedDataError(f"Invalid JSON in {path}: {e}") from e

    _validate_top_level(raw)

    customers = tuple(_build_customer(r) for r in raw["Customer_Profiles"])
    locations = tuple(_build_location(r) for r in raw["Location_Profiles"])
    technicians = tuple(_build_technician(r) for r in raw["Technician_Profiles"])

    return SeedData(customers=customers, locations=locations, technicians=technicians)


# ---------- validators and builders ----------

def _validate_top_level(raw: Any) -> None:
    if not isinstance(raw, dict):
        raise SeedDataError("Seed data root must be a JSON object")
    for key in _REQUIRED_TOP_LEVEL_KEYS:
        if key not in raw:
            raise SeedDataError(f"Missing required top-level key: {key}")
        if not isinstance(raw[key], list):
            raise SeedDataError(f"Top-level key {key} must be a list")


def _require_fields(record: dict, fields: tuple[str, ...], record_type: str) -> None:
    for f in fields:
        if f not in record:
            raise SeedDataError(
                f"{record_type} record missing required field '{f}': {record}"
            )


def _build_customer(record: dict) -> Customer:
    _require_fields(record, _REQUIRED_CUSTOMER_FIELDS, "Customer")
    return Customer(id=int(record["id"]), name=record["name"], contact=record["contact"])


def _build_location(record: dict) -> Location:
    _require_fields(record, _REQUIRED_LOCATION_FIELDS, "Location")
    return Location(id=int(record["id"]), name=record["name"], address=record["address"])


def _build_technician(record: dict) -> Technician:
    _require_fields(record, _REQUIRED_TECHNICIAN_FIELDS, "Technician")
    return Technician(
        id=int(record["id"]),
        name=record["name"],
        zones=tuple(record["zones"]),
        business_units=tuple(record["business_units"]),
    )