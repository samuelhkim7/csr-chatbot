"""Tests for the booking engine.

Covers:
- Trade alias normalization ("plumber" == "plumbing", etc.)
- Trade + zone matching against technicians
- In-memory double-booking prevention
- Deterministic multi-technician tiebreak (lowest id wins)
- Cascading failure reasons (unknown trade, no zone, all booked)
"""
from datetime import datetime, timedelta

import pytest

from src.booking_engine import (
    BookingEngine,
    BookingLedger,
    BookingStatus,
    normalize_trade,
)
from src.data_loader import load_seed
from src.models import BookingRequest, Booking
from pathlib import Path


SEED_PATH = Path(__file__).parent.parent / "data" / "seed.json"

# A fixed reference time so tests are deterministic
T0 = datetime(2026, 4, 15, 14, 0)
T1 = datetime(2026, 4, 15, 15, 0)


# ---------- fixtures ----------

@pytest.fixture
def seed():
    return load_seed(SEED_PATH)


@pytest.fixture
def engine(seed):
    """Fresh engine (with empty ledger) per test."""
    return BookingEngine(seed=seed, ledger=BookingLedger())


def _req(trade="plumber", zip_code="94115", when=T0, customer_name=None):
    """Tiny builder for BookingRequests in tests."""
    return BookingRequest(
        trade=trade,
        zip_code=zip_code,
        appointment_time=when,
        customer_name=customer_name,
    )


# ---------- trade normalization ----------

@pytest.mark.parametrize("alias,expected", [
    ("plumber", "plumbing"),
    ("Plumber", "plumbing"),
    ("PLUMBING", "plumbing"),
    ("plumbing", "plumbing"),
    ("electrician", "electrical"),
    ("electrical", "electrical"),
    ("electric", "electrical"),
    ("hvac", "hvac"),
    ("HVAC", "hvac"),
    ("ac", "hvac"),
    ("air conditioning", "hvac"),
    ("heating", "hvac"),
    ("heat", "hvac"),
])
def test_normalize_trade_known_aliases(alias, expected):
    assert normalize_trade(alias) == expected


@pytest.mark.parametrize("unknown", ["carpenter", "painter", "roofer", "", "   "])
def test_normalize_trade_unknown_returns_none(unknown):
    assert normalize_trade(unknown) is None


# ---------- BookingLedger in isolation ----------

def test_fresh_ledger_has_no_bookings():
    ledger = BookingLedger()
    assert ledger.is_available(tech_id=1, when=T0) is True


def test_ledger_add_makes_slot_unavailable():
    ledger = BookingLedger()
    booking = Booking(
        technician_id=1, technician_name="X",
        trade="plumbing", zip_code="94115", appointment_time=T0,
    )
    ledger.add(booking)
    assert ledger.is_available(tech_id=1, when=T0) is False


def test_ledger_different_time_still_available():
    ledger = BookingLedger()
    ledger.add(Booking(1, "X", "plumbing", "94115", T0))
    assert ledger.is_available(tech_id=1, when=T1) is True


def test_ledger_different_tech_still_available():
    ledger = BookingLedger()
    ledger.add(Booking(1, "X", "plumbing", "94115", T0))
    assert ledger.is_available(tech_id=2, when=T0) is True


# ---------- happy path booking ----------

def test_book_happy_path(engine):
    """94115 plumber: only Michael Page (id 4697) serves that combination."""
    result = engine.book(_req(trade="plumber", zip_code="94115", when=T0))
    assert result.status is BookingStatus.SUCCESS
    assert result.booking is not None
    assert result.booking.technician_id == 4697
    assert result.booking.technician_name == "Michael Page"
    assert result.booking.trade == "plumbing"  # normalized
    assert result.booking.zip_code == "94115"
    assert result.booking.appointment_time == T0


def test_book_happy_path_electrical(engine):
    """94115 electrical: Michael Page (4697) and Christopher Johnson (6608)."""
    result = engine.book(_req(trade="electrician", zip_code="94115", when=T0))
    assert result.status is BookingStatus.SUCCESS
    assert result.booking.technician_id == 4697  # lowest id wins
    assert result.other_available_count == 1  # Christopher was also available


def test_book_returns_normalized_trade(engine):
    """'electrician' should be stored as 'electrical' in the confirmed booking."""
    result = engine.book(_req(trade="electrician", zip_code="94115", when=T0))
    assert result.booking.trade == "electrical"


def test_book_preserves_customer_name(engine):
    result = engine.book(_req(trade="plumber", zip_code="94115", when=T0, customer_name="Justin Long"))
    assert result.booking.customer_name == "Justin Long"


# ---------- failure modes ----------

def test_book_unknown_trade(engine):
    result = engine.book(_req(trade="carpenter", zip_code="94115", when=T0))
    assert result.status is BookingStatus.UNKNOWN_TRADE
    assert result.booking is None


def test_book_no_zone_match(engine):
    """Nobody serves 94999."""
    result = engine.book(_req(trade="plumber", zip_code="94999", when=T0))
    assert result.status is BookingStatus.NO_ZONE_MATCH
    assert result.booking is None


def test_book_no_trade_match_for_zone(engine):
    """94109 has no technician coverage at all — should surface NO_ZONE_MATCH."""
    result = engine.book(_req(trade="plumber", zip_code="94109", when=T0))
    assert result.status is BookingStatus.NO_ZONE_MATCH


def test_book_hvac_outside_tinas_zones(engine):
    """Tina (8886) is the only HVAC tech, covering 94133/94119. Request in
    a zone no HVAC tech serves should return NO_ZONE_MATCH."""
    result = engine.book(_req(trade="hvac", zip_code="94115", when=T0))
    assert result.status is BookingStatus.NO_ZONE_MATCH


# ---------- double-booking prevention ----------

def test_double_booking_single_eligible_tech_fails(engine):
    """94115 plumber = Michael Page only. Booking twice at T0 → second is ALL_BOOKED."""
    first = engine.book(_req(trade="plumber", zip_code="94115", when=T0))
    assert first.status is BookingStatus.SUCCESS

    second = engine.book(_req(trade="plumber", zip_code="94115", when=T0))
    assert second.status is BookingStatus.ALL_BOOKED
    assert second.booking is None


def test_double_booking_falls_through_to_second_tech(engine):
    """94115 electrical has Michael Page AND Christopher Johnson.
    First booking → Michael. Second at same time → Christopher."""
    first = engine.book(_req(trade="electrical", zip_code="94115", when=T0))
    assert first.booking.technician_id == 4697

    second = engine.book(_req(trade="electrical", zip_code="94115", when=T0))
    assert second.status is BookingStatus.SUCCESS
    assert second.booking.technician_id == 6608  # Christopher
    assert second.other_available_count == 0


def test_triple_booking_exhausts_pool(engine):
    """Third electrical booking at same time/zone → ALL_BOOKED."""
    engine.book(_req(trade="electrical", zip_code="94115", when=T0))
    engine.book(_req(trade="electrical", zip_code="94115", when=T0))
    third = engine.book(_req(trade="electrical", zip_code="94115", when=T0))
    assert third.status is BookingStatus.ALL_BOOKED


def test_same_tech_different_times_both_succeed(engine):
    """Booking the same tech at two different times should both succeed."""
    r1 = engine.book(_req(trade="plumber", zip_code="94115", when=T0))
    r2 = engine.book(_req(trade="plumber", zip_code="94115", when=T1))
    assert r1.status is BookingStatus.SUCCESS
    assert r2.status is BookingStatus.SUCCESS
    assert r1.booking.technician_id == r2.booking.technician_id == 4697


def test_different_zones_same_time_no_conflict(engine):
    """Bookings in different zones at the same time should not conflict."""
    r1 = engine.book(_req(trade="plumber", zip_code="94115", when=T0))  # Michael
    r2 = engine.book(_req(trade="plumber", zip_code="94107", when=T0))  # Gregory (94107)
    assert r1.status is BookingStatus.SUCCESS
    assert r2.status is BookingStatus.SUCCESS
    assert r1.booking.technician_id != r2.booking.technician_id


# ---------- find_eligible_technicians (inspection helper) ----------

def test_find_eligible_deterministic_order(engine):
    """94115 electrical → [Michael Page (4697), Christopher Johnson (6608)]."""
    techs = engine.find_eligible_technicians(_req(trade="electrical", zip_code="94115", when=T0))
    assert [t.id for t in techs] == [4697, 6608]


def test_find_eligible_excludes_booked_techs(engine):
    engine.book(_req(trade="electrical", zip_code="94115", when=T0))  # books 4697
    techs = engine.find_eligible_technicians(_req(trade="electrical", zip_code="94115", when=T0))
    assert [t.id for t in techs] == [6608]  # only Christopher left