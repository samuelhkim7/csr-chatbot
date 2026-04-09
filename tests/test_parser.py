"""Tests for the intent parser.

The parser takes a raw user message and produces a `ParsedIntent` with:
  - an Intent enum (BOOKING, FAQ_LOCATIONS, FAQ_SERVICES, UNKNOWN)
  - an optional BookingRequest (possibly partial) for booking intents
  - a tuple of missing field names for re-prompting

The parser is deterministic, regex + keyword based (no LLM), which means
its behavior is fully testable.
"""
from datetime import datetime
from pathlib import Path

import pytest

from src.data_loader import load_seed
from src.parser import Intent, ParsedIntent, parse


SEED_PATH = Path(__file__).parent.parent / "data" / "seed.json"


@pytest.fixture
def seed():
    return load_seed(SEED_PATH)


# ---------- intent classification: FAQs ----------

@pytest.mark.parametrize("msg", [
    "what services do you offer?",
    "what trades do you cover",
    "Do you offer plumbing?",
    "what kind of work do you do",
    "what are your services",
])
def test_parse_faq_services(seed, msg):
    result = parse(msg, seed)
    assert result.intent is Intent.FAQ_SERVICES


@pytest.mark.parametrize("msg", [
    "what locations do you serve?",
    "what areas do you cover",
    "where do you operate",
    "what zip codes do you serve",
    "what neighborhoods are covered",
])
def test_parse_faq_locations(seed, msg):
    result = parse(msg, seed)
    assert result.intent is Intent.FAQ_LOCATIONS


# ---------- intent classification: booking ----------

def test_parse_full_booking(seed):
    msg = "book a plumber at 94115 for 2026-04-15 14:00"
    result = parse(msg, seed)
    assert result.intent is Intent.BOOKING
    req = result.booking_request
    assert req is not None
    assert req.trade == "plumber"  # parser keeps raw; engine normalizes
    assert req.zip_code == "94115"
    assert req.appointment_time == datetime(2026, 4, 15, 14, 0)
    assert result.missing_fields == ()


def test_parse_booking_with_T_separator(seed):
    msg = "book an electrician at 94117 for 2026-04-15T09:30"
    result = parse(msg, seed)
    assert result.intent is Intent.BOOKING
    assert result.booking_request.appointment_time == datetime(2026, 4, 15, 9, 30)


def test_parse_booking_natural_language_phrasing(seed):
    """The spec's example phrasing should parse cleanly."""
    msg = "Help me find a plumber available on 2026-04-15 14:00 at 94117"
    result = parse(msg, seed)
    assert result.intent is Intent.BOOKING
    assert result.booking_request.trade == "plumber"
    assert result.booking_request.zip_code == "94117"
    assert result.booking_request.appointment_time == datetime(2026, 4, 15, 14, 0)


# ---------- booking with customer name (auto-resolve zip) ----------

def test_parse_booking_with_full_customer_name(seed):
    msg = "book a plumber for Heather Russell at 2026-04-15 14:00"
    result = parse(msg, seed)
    assert result.intent is Intent.BOOKING
    req = result.booking_request
    assert req.customer_name == "Heather Russell"
    assert req.zip_code == "94111"  # auto-resolved from Heather's location
    assert req.appointment_time == datetime(2026, 4, 15, 14, 0)
    assert result.missing_fields == ()


def test_parse_booking_with_first_name_only(seed):
    """First-name only should still resolve via SeedData.find_customer_by_name."""
    msg = "book an electrician for Kristi at 2026-04-15 14:00"
    result = parse(msg, seed)
    assert result.booking_request.customer_name is not None
    assert "Kristi" in result.booking_request.customer_name
    assert result.booking_request.zip_code == "94117"  # Kristi Alvarez's zip


def test_explicit_zip_overrides_customer_name_resolution(seed):
    """If both a zip and a customer name are given, the explicit zip wins."""
    msg = "book a plumber for Heather Russell at 94115 for 2026-04-15 14:00"
    result = parse(msg, seed)
    assert result.booking_request.customer_name == "Heather Russell"
    assert result.booking_request.zip_code == "94115"  # NOT Heather's 94111


# ---------- partial parses: missing fields ----------

def test_parse_booking_missing_zip(seed):
    msg = "book a plumber for 2026-04-15 14:00"
    result = parse(msg, seed)
    assert result.intent is Intent.BOOKING
    assert "zip_code" in result.missing_fields
    assert result.booking_request.zip_code is None


def test_parse_booking_missing_time(seed):
    msg = "book a plumber at 94115"
    result = parse(msg, seed)
    assert result.intent is Intent.BOOKING
    assert "appointment_time" in result.missing_fields


def test_parse_booking_only_trade(seed):
    msg = "I need a plumber"
    result = parse(msg, seed)
    assert result.intent is Intent.BOOKING
    assert result.booking_request.trade == "plumber"
    assert set(result.missing_fields) == {"zip_code", "appointment_time"}


def test_parse_booking_invalid_datetime_not_extracted(seed):
    """Malformed datetime should leave appointment_time None, not crash."""
    msg = "book a plumber at 94115 for 2026-13-45 99:99"
    result = parse(msg, seed)
    assert result.booking_request.appointment_time is None
    assert "appointment_time" in result.missing_fields


# ---------- trade extraction (parametrized) ----------

@pytest.mark.parametrize("msg,expected_trade", [
    ("book a plumber", "plumber"),
    ("I need plumbing", "plumbing"),
    ("schedule an electrician", "electrician"),
    ("book electrical work", "electrical"),
    ("I need HVAC", "hvac"),
    ("need air conditioning service", "air conditioning"),
    ("heating repair please", "heating"),
])
def test_parse_trade_extraction(seed, msg, expected_trade):
    result = parse(msg, seed)
    assert result.booking_request is not None
    assert result.booking_request.trade == expected_trade


# ---------- zip extraction ----------

def test_parse_zip_extraction_basic(seed):
    result = parse("book a plumber at 94115", seed)
    assert result.booking_request.zip_code == "94115"


def test_parse_zip_not_confused_by_datetime_digits(seed):
    """The year 2026 is 4 digits so can't match \\b\\d{5}\\b, but
    we still want to make sure nothing in a datetime ever leaks
    into the zip slot."""
    result = parse("book a plumber at 94115 for 2026-04-15 14:00", seed)
    assert result.booking_request.zip_code == "94115"


# ---------- unknown intent ----------

@pytest.mark.parametrize("msg", [
    "hello",
    "how's the weather",
    "",
    "   ",
])
def test_parse_unknown_intent(seed, msg):
    result = parse(msg, seed)
    assert result.intent is Intent.UNKNOWN


# ---------- ParsedIntent shape ----------

def test_parsed_intent_preserves_raw_message(seed):
    msg = "book a plumber at 94115 for 2026-04-15 14:00"
    result = parse(msg, seed)
    assert result.raw_message == msg


def test_parsed_intent_is_frozen(seed):
    result = parse("hello", seed)
    with pytest.raises((AttributeError, Exception)):
        result.intent = Intent.BOOKING  # type: ignore