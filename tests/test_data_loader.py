"""Tests for data loading and domain models.

Written before the implementation (TDD). These tests pin down the contract
of `load_seed()` and the shape of the domain models.
"""
import json
from pathlib import Path

import pytest

from src.data_loader import load_seed, SeedDataError
from src.models import Customer, Location, Technician, SeedData


SEED_PATH = Path(__file__).parent.parent / "data" / "seed.json"


# ---------- fixtures ----------

@pytest.fixture
def seed() -> SeedData:
    """Load the real seed data once per test."""
    return load_seed(SEED_PATH)


@pytest.fixture
def tmp_seed_file(tmp_path):
    """Factory for writing a temporary seed JSON file."""
    def _make(payload: dict) -> Path:
        p = tmp_path / "seed.json"
        p.write_text(json.dumps(payload))
        return p
    return _make


# ---------- counts and types ----------

def test_loads_all_customers(seed):
    assert len(seed.customers) == 10
    assert all(isinstance(c, Customer) for c in seed.customers)


def test_loads_all_locations(seed):
    assert len(seed.locations) == 10
    assert all(isinstance(l, Location) for l in seed.locations)


def test_loads_all_technicians(seed):
    assert len(seed.technicians) == 5
    assert all(isinstance(t, Technician) for t in seed.technicians)


# ---------- field integrity ----------

def test_customer_fields_preserved(seed):
    heather = next(c for c in seed.customers if c.name == "Heather Russell")
    assert heather.id == 6945
    assert heather.contact == "(923)951-0044"


def test_technician_zones_and_units_preserved(seed):
    tina = next(t for t in seed.technicians if t.name == "Tina Orozco")
    assert tina.id == 8886
    assert "94133" in tina.zones
    assert "94119" in tina.zones
    assert "plumbing" in tina.business_units
    assert "hvac" in tina.business_units


def test_location_zip_extracted_from_address(seed):
    airbnb = next(l for l in seed.locations if l.id == 4376)
    assert airbnb.zip_code == "94115"


def test_all_locations_have_valid_zip(seed):
    for loc in seed.locations:
        assert loc.zip_code is not None
        assert len(loc.zip_code) == 5
        assert loc.zip_code.isdigit()


# ---------- models are immutable ----------

def test_models_are_frozen(seed):
    """Dataclasses should be frozen so they're hashable and can't drift."""
    tina = seed.technicians[0]
    with pytest.raises((AttributeError, Exception)):
        tina.name = "Mutated"  # type: ignore


# ---------- helper lookups on SeedData ----------

def test_find_customer_by_name_exact(seed):
    c = seed.find_customer_by_name("Heather Russell")
    assert c is not None
    assert c.id == 6945


def test_find_customer_by_name_case_insensitive(seed):
    c = seed.find_customer_by_name("heather russell")
    assert c is not None
    assert c.id == 6945


def test_find_customer_by_name_partial_match(seed):
    """Users might just type a first name; supporting this makes the demo smoother."""
    c = seed.find_customer_by_name("Heather")
    assert c is not None
    assert c.name == "Heather Russell"


def test_find_customer_by_name_unknown_returns_none(seed):
    assert seed.find_customer_by_name("Nonexistent Person") is None


def test_get_zip_for_customer_by_name(seed):
    # Heather Russell (id 6945) → location 6945 → "95281 Joshua Courts, ..., 94111"
    zip_code = seed.get_zip_for_customer("Heather Russell")
    assert zip_code == "94111"


def test_get_zip_for_unknown_customer_returns_none(seed):
    assert seed.get_zip_for_customer("Nobody") is None


# ---------- error handling ----------

def test_missing_file_raises(tmp_path):
    with pytest.raises(SeedDataError, match="not found"):
        load_seed(tmp_path / "does_not_exist.json")


def test_invalid_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json")
    with pytest.raises(SeedDataError, match="(?i)invalid json"):
        load_seed(p)


def test_missing_top_level_key_raises(tmp_seed_file):
    p = tmp_seed_file({"Customer_Profiles": [], "Location_Profiles": []})  # no Technician_Profiles
    with pytest.raises(SeedDataError, match="Technician_Profiles"):
        load_seed(p)


def test_missing_required_field_in_record_raises(tmp_seed_file):
    p = tmp_seed_file({
        "Customer_Profiles": [{"id": 1, "name": "X"}],  # missing 'contact'
        "Location_Profiles": [],
        "Technician_Profiles": [],
    })
    with pytest.raises(SeedDataError, match="contact"):
        load_seed(p)


def test_empty_profiles_are_allowed(tmp_seed_file):
    """An empty-but-well-formed seed file should load without error."""
    p = tmp_seed_file({
        "Customer_Profiles": [],
        "Location_Profiles": [],
        "Technician_Profiles": [],
    })
    seed = load_seed(p)
    assert seed.customers == ()
    assert seed.locations == ()
    assert seed.technicians == ()