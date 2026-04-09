"""Tests for the FAQ handler.

The FAQ handler is a set of pure functions that take `SeedData` and
return formatted strings ready for display. Services and locations are
derived from technician data — there's no separate FAQ content, so the
coverage implicitly reflects whatever the technician pool supports.
"""
from pathlib import Path

import pytest

from src.data_loader import load_seed
from src.faq import (
    answer_locations_question,
    answer_services_question,
    answer_unknown_question,
)
from src.models import SeedData


SEED_PATH = Path(__file__).parent.parent / "data" / "seed.json"


@pytest.fixture
def seed():
    return load_seed(SEED_PATH)


# ---------- locations ----------

def test_locations_response_contains_all_unique_zones(seed):
    """All 11 unique zones from the technician pool should appear."""
    response = answer_locations_question(seed)
    expected_zones = {
        "94101", "94106", "94107", "94111", "94113",
        "94115", "94117", "94118", "94119", "94120", "94133",
    }
    for zone in expected_zones:
        assert zone in response


def test_locations_response_is_nonempty_string(seed):
    response = answer_locations_question(seed)
    assert isinstance(response, str)
    assert len(response) > 0


def test_locations_response_mentions_zone_count(seed):
    """Response should say how many zones are served (11)."""
    response = answer_locations_question(seed)
    assert "11" in response


def test_locations_zones_are_sorted_in_response(seed):
    """Zones should appear in sorted order so the output is deterministic."""
    response = answer_locations_question(seed)
    # Find the positions of each zone — they should be strictly increasing
    zones = ["94101", "94106", "94107", "94111", "94113",
             "94115", "94117", "94118", "94119", "94120", "94133"]
    positions = [response.find(z) for z in zones]
    assert positions == sorted(positions)
    assert all(p >= 0 for p in positions)


def test_locations_empty_seed_returns_graceful_message():
    """No techs → no zones → response should not crash."""
    empty = SeedData()
    response = answer_locations_question(empty)
    assert isinstance(response, str)
    assert len(response) > 0


# ---------- services ----------

def test_services_response_mentions_all_three(seed):
    """The seed has exactly three business_units: plumbing, electrical, hvac."""
    response = answer_services_question(seed)
    response_lower = response.lower()
    assert "plumbing" in response_lower
    assert "electrical" in response_lower
    assert "hvac" in response_lower


def test_services_response_uses_friendly_hvac_name(seed):
    """HVAC should appear as uppercase (it's an acronym), not 'Hvac'."""
    response = answer_services_question(seed)
    assert "HVAC" in response
    assert "Hvac" not in response


def test_services_response_uses_title_case_for_regular_names(seed):
    response = answer_services_question(seed)
    assert "Plumbing" in response
    assert "Electrical" in response


def test_services_empty_seed_returns_graceful_message():
    empty = SeedData()
    response = answer_services_question(empty)
    assert isinstance(response, str)
    assert len(response) > 0


# ---------- unknown fallback ----------

def test_unknown_response_is_helpful_string():
    """The fallback should nudge the user toward something the bot can do."""
    response = answer_unknown_question()
    assert isinstance(response, str)
    assert len(response) > 0
    # It should mention at least one of the bot's capabilities
    response_lower = response.lower()
    assert any(word in response_lower for word in ("book", "appointment", "service", "help"))


# ---------- determinism ----------

def test_locations_response_is_deterministic(seed):
    """Two calls with the same seed should produce identical strings."""
    assert answer_locations_question(seed) == answer_locations_question(seed)


def test_services_response_is_deterministic(seed):
    assert answer_services_question(seed) == answer_services_question(seed)