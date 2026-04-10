"""Tests for the booking engine.

Covers:
- Trade alias normalization ("plumber" == "plumbing", etc.)
- Trade + zone matching against technicians
- Business-hours enforcement (9:00-17:00; last valid start is 16:00)
- 1-hour slot overlap checking in the ledger
- Multi-technician selection via MULTIPLE_CHOICES status
- Auto-booking when exactly one tech is eligible
- Deterministic ordering by technician id
- Cascading failure reasons (unknown trade, no zone, all booked, outside hours)
"""
from datetime import datetime
from pathlib import Path

import pytest

from src.booking_engine import (
    BookingEngine,
    BookingLedger,
    BookingStatus,
    normalize_trade,
)
from src.data_loader import load_seed
from src.models import Booking, BookingRequest


SEED_PATH = Path(__file__).parent.parent / "data" / "seed.json"

# Fixed reference times — all within business hours so core tests stay clean.
T0 = datetime(2026, 4, 15, 14, 0)         # 2pm
T_HALF = datetime(2026, 4, 15, 14, 30)    # 2:30pm - overlaps T0
T1 = datetime(2026, 4, 15, 15, 0)         # 3pm - exactly 1hr after T0, no overlap

# Business-hours edges
T_OPEN = datetime(2026, 4, 15, 9, 0)       # valid: start of day
T_LAST_SLOT = datetime(2026, 4, 15, 16, 0) # valid: last slot (ends at 17:00)
T_TOO_EARLY = datetime(2026, 4, 15, 8, 0)  # invalid: before 9
T_AT_CLOSE = datetime(2026, 4, 15, 17, 0)  # invalid: slot would end at 18:00
T_PAST_CLOSE = datetime(2026, 4, 15, 16, 30)  # invalid: would run into 17:30


# ---------- fixtures ----------

@pytest.fixture
def seed():
    return load_seed(SEED_PATH)


@pytest.fixture
def engine(seed):
    """Fresh engine (with empty ledger) per test."""
    return BookingEngine(seed=seed, ledger=BookingLedger())


def _req(trade="plumber", zip_code="94115", when=T0, customer_name=None):
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
    ("plumbers", "plumbing"),  # plural
    ("electrician", "electrical"),
    ("electricians", "electrical"),  # plural
    ("electrical", "electrical"),
    ("electric", "electrical"),
    ("hvac", "hvac"),
    ("HVAC", "hvac"),
    ("ac", "hvac"),
    ("a/c", "hvac"),  # slash form
    ("air conditioning", "hvac"),
    ("air con", "hvac"),  # abbreviation
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
    ledger.add(Booking(1, "X", "plumbing", "94115", T0))
    assert ledger.is_available(tech_id=1, when=T0) is False


def test_ledger_overlap_half_hour_later_conflicts():
    """14:00 booking blocks 14:30 (30min overlap in a 1hr slot)."""
    ledger = BookingLedger()
    ledger.add(Booking(1, "X", "plumbing", "94115", T0))
    assert ledger.is_available(tech_id=1, when=T_HALF) is False


def test_ledger_overlap_half_hour_earlier_conflicts():
    """14:30 booking blocks 14:00 (symmetric overlap check)."""
    ledger = BookingLedger()
    ledger.add(Booking(1, "X", "plumbing", "94115", T_HALF))
    assert ledger.is_available(tech_id=1, when=T0) is False


def test_ledger_exactly_one_hour_later_no_conflict():
    """14:00 and 15:00 are adjacent, not overlapping."""
    ledger = BookingLedger()
    ledger.add(Booking(1, "X", "plumbing", "94115", T0))
    assert ledger.is_available(tech_id=1, when=T1) is True


def test_ledger_different_tech_still_available():
    ledger = BookingLedger()
    ledger.add(Booking(1, "X", "plumbing", "94115", T0))
    assert ledger.is_available(tech_id=2, when=T0) is True


# ---------- business-hours enforcement ----------

def test_book_before_business_hours_rejected(engine):
    result = engine.book(_req(when=T_TOO_EARLY))
    assert result.status is BookingStatus.OUTSIDE_BUSINESS_HOURS
    assert result.booking is None


def test_book_starts_at_close_rejected(engine):
    """17:00 start would push the slot to 18:00 — past closing."""
    result = engine.book(_req(when=T_AT_CLOSE))
    assert result.status is BookingStatus.OUTSIDE_BUSINESS_HOURS


def test_book_would_run_past_close_rejected(engine):
    """16:30 start would end at 17:30 — past closing."""
    result = engine.book(_req(when=T_PAST_CLOSE))
    assert result.status is BookingStatus.OUTSIDE_BUSINESS_HOURS


def test_book_first_slot_of_day_accepted(engine):
    result = engine.book(_req(when=T_OPEN))
    assert result.status is BookingStatus.SUCCESS


def test_book_last_slot_of_day_accepted(engine):
    """16:00-17:00 is the last valid slot of the day."""
    result = engine.book(_req(when=T_LAST_SLOT))
    assert result.status is BookingStatus.SUCCESS


# ---------- happy path: single eligible tech auto-books ----------

def test_book_happy_path_single_eligible(engine):
    """94115 plumber: only Michael Page (id 4697) serves that combination → auto-book."""
    result = engine.book(_req(trade="plumber", zip_code="94115", when=T0))
    assert result.status is BookingStatus.SUCCESS
    assert result.booking is not None
    assert result.booking.technician_id == 4697
    assert result.booking.technician_name == "Michael Page"
    assert result.booking.trade == "plumbing"
    assert result.booking.zip_code == "94115"
    assert result.booking.appointment_time == T0


def test_book_returns_normalized_trade(engine):
    """'plumber' should be stored as 'plumbing' in the confirmed booking."""
    result = engine.book(_req(trade="plumber", zip_code="94115", when=T0))
    assert result.booking.trade == "plumbing"


def test_book_preserves_customer_name(engine):
    result = engine.book(_req(trade="plumber", zip_code="94115", when=T0, customer_name="Justin Long"))
    assert result.booking.customer_name == "Justin Long"


# ---------- multi-tech selection ----------

def test_multi_tech_returns_choices(engine):
    """94115 electrical has 2 eligible techs → MULTIPLE_CHOICES, no auto-book."""
    result = engine.book(_req(trade="electrician", zip_code="94115", when=T0))
    assert result.status is BookingStatus.MULTIPLE_CHOICES
    assert result.booking is None
    assert {t.id for t in result.choices} == {4697, 6608}
    # Choices should be deterministically ordered by id
    assert [t.id for t in result.choices] == [4697, 6608]


def test_multi_tech_pick_specific_christopher(engine):
    """User picks Christopher (6608) explicitly → book him, not Michael."""
    result = engine.book(
        _req(trade="electrician", zip_code="94115", when=T0),
        preferred_technician_id=6608,
    )
    assert result.status is BookingStatus.SUCCESS
    assert result.booking.technician_id == 6608
    assert result.booking.technician_name == "Christopher Johnson"


def test_multi_tech_pick_specific_michael(engine):
    result = engine.book(
        _req(trade="electrician", zip_code="94115", when=T0),
        preferred_technician_id=4697,
    )
    assert result.status is BookingStatus.SUCCESS
    assert result.booking.technician_id == 4697


def test_preferred_tech_not_eligible_returns_all_booked(engine):
    """Picking a tech who doesn't cover this trade+zone → ALL_BOOKED.

    The orchestrator only offers eligible techs so this shouldn't happen
    in practice, but the engine must still be safe against bad input.
    """
    result = engine.book(
        _req(trade="electrician", zip_code="94115", when=T0),
        preferred_technician_id=8886,  # Tina - hvac only, wrong zone
    )
    assert result.status is BookingStatus.ALL_BOOKED


def test_multi_tech_pick_then_autobook_fallback(engine):
    """Full flow: MULTIPLE_CHOICES → user picks → next request auto-books the fallback."""
    # First request: 2 eligible → choices offered
    first = engine.book(_req(trade="electrical", zip_code="94115", when=T0))
    assert first.status is BookingStatus.MULTIPLE_CHOICES

    # User picks Michael
    chosen = engine.book(
        _req(trade="electrical", zip_code="94115", when=T0),
        preferred_technician_id=4697,
    )
    assert chosen.status is BookingStatus.SUCCESS
    assert chosen.booking.technician_id == 4697

    # Second request at same time: only Christopher left → auto-books (no prompt)
    second = engine.book(_req(trade="electrical", zip_code="94115", when=T0))
    assert second.status is BookingStatus.SUCCESS
    assert second.booking.technician_id == 6608

    # Third request: both booked
    third = engine.book(_req(trade="electrical", zip_code="94115", when=T0))
    assert third.status is BookingStatus.ALL_BOOKED


# ---------- failure modes ----------

def test_book_unknown_trade(engine):
    result = engine.book(_req(trade="carpenter", zip_code="94115", when=T0))
    assert result.status is BookingStatus.UNKNOWN_TRADE


def test_book_no_zone_match(engine):
    """Nobody serves 94999."""
    result = engine.book(_req(trade="plumber", zip_code="94999", when=T0))
    assert result.status is BookingStatus.NO_ZONE_MATCH


def test_book_zone_with_no_coverage(engine):
    """94109 has no technician coverage at all."""
    result = engine.book(_req(trade="plumber", zip_code="94109", when=T0))
    assert result.status is BookingStatus.NO_ZONE_MATCH


def test_book_hvac_outside_tinas_zones(engine):
    """Tina is the only HVAC tech. Requesting HVAC in a zone she doesn't serve → NO_ZONE_MATCH."""
    result = engine.book(_req(trade="hvac", zip_code="94115", when=T0))
    assert result.status is BookingStatus.NO_ZONE_MATCH


# ---------- double-booking prevention (with 1-hour overlap semantics) ----------

def test_double_booking_single_eligible_tech_fails(engine):
    """94115 plumber = Michael only. Booking twice at T0 → second is ALL_BOOKED."""
    first = engine.book(_req(trade="plumber", zip_code="94115", when=T0))
    assert first.status is BookingStatus.SUCCESS

    second = engine.book(_req(trade="plumber", zip_code="94115", when=T0))
    assert second.status is BookingStatus.ALL_BOOKED


def test_overlapping_half_hour_booking_fails(engine):
    """Booking Michael at 14:00 should block 14:30."""
    first = engine.book(_req(trade="plumber", zip_code="94115", when=T0))
    assert first.status is BookingStatus.SUCCESS

    second = engine.book(_req(trade="plumber", zip_code="94115", when=T_HALF))
    assert second.status is BookingStatus.ALL_BOOKED


def test_same_tech_adjacent_slots_both_succeed(engine):
    """14:00 and 15:00 are adjacent non-overlapping slots; both should book."""
    r1 = engine.book(_req(trade="plumber", zip_code="94115", when=T0))
    r2 = engine.book(_req(trade="plumber", zip_code="94115", when=T1))
    assert r1.status is BookingStatus.SUCCESS
    assert r2.status is BookingStatus.SUCCESS
    assert r1.booking.technician_id == r2.booking.technician_id == 4697


def test_different_zones_same_time_no_conflict(engine):
    r1 = engine.book(_req(trade="plumber", zip_code="94115", when=T0))  # Michael
    r2 = engine.book(_req(trade="plumber", zip_code="94107", when=T0))  # Gregory
    assert r1.status is BookingStatus.SUCCESS
    assert r2.status is BookingStatus.SUCCESS
    assert r1.booking.technician_id != r2.booking.technician_id


# ---------- find_eligible_technicians (inspection helper) ----------

def test_find_eligible_deterministic_order(engine):
    techs = engine.find_eligible_technicians(_req(trade="electrical", zip_code="94115", when=T0))
    assert [t.id for t in techs] == [4697, 6608]


def test_find_eligible_excludes_booked_techs(engine):
    # Explicitly pick Michael so only Christopher remains eligible
    engine.book(
        _req(trade="electrical", zip_code="94115", when=T0),
        preferred_technician_id=4697,
    )
    techs = engine.find_eligible_technicians(_req(trade="electrical", zip_code="94115", when=T0))
    assert [t.id for t in techs] == [6608]


# ---------- find_next_available_slot ----------

def test_find_next_available_slot_single_tech(engine):
    """94117 plumber = Michael only. Book 14:00, next slot is 15:00."""
    engine.book(_req(trade="plumber", zip_code="94117", when=T0))
    next_slot = engine.find_next_available_slot(
        _req(trade="plumber", zip_code="94117", when=T0)
    )
    assert next_slot == datetime(2026, 4, 15, 15, 0)


def test_find_next_available_slot_multi_tech_returns_same_time(engine):
    """94115 electrical has 2 techs. Book one, the other is still free
    at the same time, so 'next available' should be the requested time."""
    engine.book(
        _req(trade="electrical", zip_code="94115", when=T0),
        preferred_technician_id=4697,
    )
    next_slot = engine.find_next_available_slot(
        _req(trade="electrical", zip_code="94115", when=T0)
    )
    # Christopher is still free at T0 — next available IS T0.
    assert next_slot == T0


def test_find_next_available_slot_rolls_to_next_day(engine):
    """Book every valid slot in a day → next available should be 9am the next day."""
    for hour in range(9, 17):  # 9, 10, ..., 16 = 8 slots
        engine.book(_req(trade="plumber", zip_code="94117",
                         when=datetime(2026, 4, 15, hour, 0)))
    next_slot = engine.find_next_available_slot(
        _req(trade="plumber", zip_code="94117", when=datetime(2026, 4, 15, 14, 0))
    )
    assert next_slot == datetime(2026, 4, 16, 9, 0)


def test_find_next_available_slot_no_matching_techs_returns_none(engine):
    """94999 has no coverage. No suggestion possible."""
    next_slot = engine.find_next_available_slot(
        _req(trade="plumber", zip_code="94999", when=T0)
    )
    assert next_slot is None


def test_find_next_available_slot_unknown_trade_returns_none(engine):
    next_slot = engine.find_next_available_slot(
        _req(trade="carpenter", zip_code="94115", when=T0)
    )
    assert next_slot is None


def test_find_next_available_slot_from_half_hour_rounds_up(engine):
    """If requested time is 14:30, next available should be 15:00 (not 14:30)."""
    engine.book(_req(trade="plumber", zip_code="94117",
                     when=datetime(2026, 4, 15, 14, 0)))
    next_slot = engine.find_next_available_slot(
        _req(trade="plumber", zip_code="94117", when=datetime(2026, 4, 15, 14, 30))
    )
    assert next_slot == datetime(2026, 4, 15, 15, 0)