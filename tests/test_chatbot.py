"""Tests for the chatbot orchestrator.

The Chatbot class is the glue between the parser, booking engine, and
FAQ handlers. It also owns multi-turn conversation state for:
  * Follow-up answers to missing-field prompts
  * User picks between multiple eligible technicians

These tests exercise the orchestrator at the `handle(message) -> str`
level, which is the same interface the CLI and web UI both use.
"""
from datetime import datetime
from pathlib import Path

import pytest

from src.chatbot import Chatbot
from src.data_loader import load_seed


SEED_PATH = Path(__file__).parent.parent / "data" / "seed.json"


@pytest.fixture
def bot():
    """Fresh chatbot per test (no state leakage)."""
    return Chatbot(load_seed(SEED_PATH))


# ---------- FAQ pass-through ----------

def test_faq_locations(bot):
    response = bot.handle("what locations do you serve?")
    assert "94115" in response  # a real zone
    assert "11" in response  # zone count


def test_faq_services(bot):
    response = bot.handle("what services do you offer?")
    assert "Plumbing" in response
    assert "HVAC" in response


def test_unknown_intent_returns_fallback(bot):
    response = bot.handle("what's the weather")
    assert isinstance(response, str)
    assert len(response) > 0
    # Should nudge toward what the bot can do
    assert any(w in response.lower() for w in ("book", "appointment", "help"))


# ---------- single-turn complete booking ----------

def test_single_turn_booking_success(bot):
    """Complete booking request in one message → confirmation."""
    response = bot.handle("book a plumber at 94115 for 2026-04-15 14:00")
    assert "confirm" in response.lower() or "booked" in response.lower()
    assert "Michael Page" in response
    assert "94115" in response


def test_single_turn_booking_with_customer_name(bot):
    """Name-based booking should auto-resolve the zip."""
    response = bot.handle("book a plumber for Heather Russell at 2026-04-15 14:00")
    assert "Michael Page" in response  # only plumber at 94111 (Heather's zip)
    assert "94111" in response or "Heather" in response


# ---------- multi-turn: missing field re-prompts ----------

def test_multiturn_missing_zip(bot):
    """Spec's example flow: book without location, bot asks for zip."""
    first = bot.handle("Book a plumbing appointment for me on 2026-04-15 14:00")
    assert "zip" in first.lower() or "location" in first.lower()

    second = bot.handle("94115")
    assert "Michael Page" in second
    assert "confirm" in second.lower() or "booked" in second.lower()


def test_multiturn_missing_trade(bot):
    """Book with zip+time but no service type → bot asks what kind of service."""
    first = bot.handle("I need an appointment at 94115 for 2026-04-15 14:00")
    assert "service" in first.lower() or "kind" in first.lower()

    second = bot.handle("plumbing")
    assert "Michael Page" in second
    assert "confirm" in second.lower() or "booked" in second.lower()


def test_multiturn_missing_time(bot):
    first = bot.handle("I need a plumber at 94115")
    assert "time" in first.lower() or "date" in first.lower()

    second = bot.handle("2026-04-15 14:00")
    assert "Michael Page" in second


def test_multiturn_missing_all_fields(bot):
    """Gather trade, zip, and time across three turns."""
    r1 = bot.handle("I need to book an appointment")
    r2 = bot.handle("plumbing")
    r3 = bot.handle("94115")
    r4 = bot.handle("2026-04-15 14:00")
    assert "Michael Page" in r4


def test_state_cleared_after_successful_booking(bot):
    """After a successful booking, the next message should start fresh."""
    bot.handle("book a plumber at 94115 for 2026-04-15 14:00")
    # Next message is a bare zip with no prior context - should NOT book anything
    response = bot.handle("94117")
    # Should ask for more info, not confirm a booking
    assert "confirm" not in response.lower() and "booked" not in response.lower()


# ---------- informal datetime handling ----------

def test_informal_datetime_day_of_week(bot):
    """User says 'wednesday' → bot asks for exact format."""
    response = bot.handle("book a plumber at 94115 on wednesday")
    assert "wednesday" in response.lower()
    assert "yyyy-mm-dd" in response.lower() or "format" in response.lower()
    # And an example should be shown
    assert "2026" in response or "14:00" in response or "HH:MM" in response


def test_informal_datetime_then_iso_completes_booking(bot):
    """After the informal-datetime prompt, typing a proper ISO should work."""
    bot.handle("book a plumber at 94115 on wednesday")
    response = bot.handle("2026-04-15 14:00")
    assert "Michael Page" in response
    assert "confirm" in response.lower() or "booked" in response.lower()


def test_informal_datetime_standalone_followup(bot):
    """User mid-booking responds to time prompt with 'tomorrow' → re-prompt for format."""
    bot.handle("I need a plumber at 94115")  # pending, missing time
    response = bot.handle("tomorrow")
    assert "tomorrow" in response.lower()
    assert "yyyy-mm-dd" in response.lower() or "format" in response.lower()


def test_informal_datetime_with_am_pm(bot):
    """'3pm' by itself isn't parseable — should trigger the format prompt."""
    response = bot.handle("book a plumber at 94115 at 3pm")
    assert "3pm" in response.lower() or "format" in response.lower()


# ---------- multi-turn: tech choice ----------

def test_multitech_choice_by_number(bot):
    """94115 electrical has 2 techs → bot offers choice → user picks by number."""
    first = bot.handle("book an electrician at 94115 for 2026-04-15 14:00")
    assert "Michael Page" in first
    assert "Christopher Johnson" in first
    assert "1" in first and "2" in first  # numbered list

    second = bot.handle("1")  # pick first option (Michael, sorted by id)
    assert "Michael Page" in second
    assert "confirm" in second.lower() or "booked" in second.lower()


def test_multitech_choice_by_full_name(bot):
    bot.handle("book an electrician at 94115 for 2026-04-15 14:00")
    response = bot.handle("Christopher Johnson")
    assert "Christopher Johnson" in response
    assert "confirm" in response.lower() or "booked" in response.lower()


def test_multitech_choice_by_first_name(bot):
    bot.handle("book an electrician at 94115 for 2026-04-15 14:00")
    response = bot.handle("Christopher")
    assert "Christopher Johnson" in response


def test_multitech_invalid_choice_reprompts(bot):
    """Invalid pick → bot re-shows the choices rather than giving up."""
    bot.handle("book an electrician at 94115 for 2026-04-15 14:00")
    response = bot.handle("banana")
    # Should still list the techs
    assert "Michael Page" in response
    assert "Christopher Johnson" in response


# ---------- failure messages ----------

def test_failure_no_zone_coverage(bot):
    """94109 has no technician coverage."""
    response = bot.handle("book a plumber at 94109 for 2026-04-15 14:00")
    assert "sorry" in response.lower() or "no" in response.lower()
    # Should mention the zip or "area" or "serve"
    assert "94109" in response or "area" in response.lower() or "serve" in response.lower()


def test_failure_unknown_trade(bot):
    response = bot.handle("book a carpenter at 94115 for 2026-04-15 14:00")
    assert "sorry" in response.lower() or "don't offer" in response.lower()


def test_failure_outside_business_hours(bot):
    response = bot.handle("book a plumber at 94115 for 2026-04-15 08:00")
    assert "9" in response and ("5" in response or "17" in response)


def test_failure_all_booked(bot):
    """Book the only plumber at 94115, then try again at the same slot."""
    bot.handle("book a plumber at 94115 for 2026-04-15 14:00")
    response = bot.handle("book a plumber at 94115 for 2026-04-15 14:00")
    assert "sorry" in response.lower() or "all" in response.lower() or "no" in response.lower()


def test_state_cleared_after_failure(bot):
    """Failed booking should clear state so next message starts fresh."""
    bot.handle("book a plumber at 94109 for 2026-04-15 14:00")  # no zone match
    # Next message should be treated as new, not as a follow-up
    response = bot.handle("what services do you offer?")
    assert "Plumbing" in response


# ---------- reset command ----------

def test_reset_clears_pending_state(bot):
    """User can explicitly reset the conversation."""
    bot.handle("I need a plumber")  # partial booking, pending state set
    response = bot.handle("reset")
    # Next message should not merge with the old state
    followup = bot.handle("94115")
    assert "confirm" not in followup.lower() and "booked" not in followup.lower()


# ---------- handle() always returns a non-empty string ----------

@pytest.mark.parametrize("message", [
    "",
    "   ",
    "hello",
    "help",
    "???",
])
def test_handle_always_returns_nonempty_string(bot, message):
    response = bot.handle(message)
    assert isinstance(response, str)
    assert len(response.strip()) > 0