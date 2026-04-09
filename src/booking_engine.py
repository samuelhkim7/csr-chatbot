"""Booking engine for the CSR chatbot.

This module owns the business logic for matching technicians to requests
and enforcing the one-slot-per-tech-per-time invariant. It deliberately
knows nothing about CLI/HTTP/string formatting — those concerns live in
the chatbot orchestrator above it.

Design notes:
  * `BookingLedger` is the seam for persistence. Today it's an in-memory
    dict; tomorrow it could be Postgres with a unique constraint on
    (technician_id, appointment_time). Swapping internals doesn't touch
    the engine.
  * `BookingEngine.book()` returns a `BookingResult` discriminated by a
    `BookingStatus` enum rather than raising exceptions. Failures aren't
    exceptional here — they're expected branches of normal flow.
  * Ties between eligible technicians are broken by lowest id. Determinism
    matters for tests and for not playing favorites at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from src.models import Booking, BookingRequest, SeedData, Technician


# ---------- trade vocabulary ----------

#: Maps user-facing trade words to the canonical `business_units` values
#: used in the seed data. Keys are lowercased; lookups normalize input.
TRADE_ALIASES: dict[str, str] = {
    # plumbing
    "plumber": "plumbing",
    "plumbing": "plumbing",
    # electrical
    "electrician": "electrical",
    "electrical": "electrical",
    "electric": "electrical",
    # hvac
    "hvac": "hvac",
    "ac": "hvac",
    "air conditioning": "hvac",
    "heating": "hvac",
    "heat": "hvac",
}


def normalize_trade(trade: str) -> Optional[str]:
    """Canonicalize a user-facing trade word. Returns None if unknown.

    Case-insensitive, whitespace-tolerant. Unknown words return None so
    the caller can surface a helpful "we don't offer that" message.
    """
    if not trade or not trade.strip():
        return None
    return TRADE_ALIASES.get(trade.strip().lower())


# ---------- results ----------

class BookingStatus(Enum):
    SUCCESS = "success"
    UNKNOWN_TRADE = "unknown_trade"
    NO_ZONE_MATCH = "no_zone_match"
    ALL_BOOKED = "all_booked"


@dataclass(frozen=True)
class BookingResult:
    """Outcome of a booking attempt.

    `other_available_count` is populated on success and tells the
    chatbot layer how many additional techs could also have taken the job
    (useful for friendly messages like "2 other techs were also available").
    """
    status: BookingStatus
    booking: Optional[Booking] = None
    other_available_count: int = 0

    @property
    def success(self) -> bool:
        return self.status is BookingStatus.SUCCESS


# ---------- ledger ----------

class BookingLedger:
    """In-memory store of confirmed bookings.

    Internal representation is `dict[tech_id, set[datetime]]` for O(1)
    availability checks. A parallel list keeps insertion order for
    debugging and future "list my bookings" features.

    NOTE: A slot is currently an exact `datetime`. Two bookings at 14:00
    and 14:30 do not conflict, even though in real life a service call
    takes longer than 30 minutes. Adding an appointment duration is
    listed in the README as future work.
    """

    def __init__(self) -> None:
        self._slots: dict[int, set[datetime]] = {}
        self._all: list[Booking] = []

    def is_available(self, tech_id: int, when: datetime) -> bool:
        return when not in self._slots.get(tech_id, set())

    def add(self, booking: Booking) -> None:
        self._slots.setdefault(booking.technician_id, set()).add(booking.appointment_time)
        self._all.append(booking)

    def all_bookings(self) -> tuple[Booking, ...]:
        return tuple(self._all)


# ---------- engine ----------

@dataclass
class BookingEngine:
    """Matches booking requests against the technician pool + ledger."""
    seed: SeedData
    ledger: BookingLedger = field(default_factory=BookingLedger)

    def find_eligible_technicians(self, request: BookingRequest) -> list[Technician]:
        """Technicians who serve the trade + zone AND are not already booked.

        Returned sorted by technician id for determinism. Returns `[]` for
        unknown trades (the engine's `book()` distinguishes that case via
        its own cascade).
        """
        canonical = normalize_trade(request.trade) if request.trade else None
        if canonical is None or request.zip_code is None or request.appointment_time is None:
            return []

        return sorted(
            (
                tech
                for tech in self.seed.technicians
                if canonical in tech.business_units
                and request.zip_code in tech.zones
                and self.ledger.is_available(tech.id, request.appointment_time)
            ),
            key=lambda t: t.id,
        )

    def book(self, request: BookingRequest) -> BookingResult:
        """Attempt to book. Returns a result describing the outcome.

        Cascade:
          1. Trade word isn't recognized      → UNKNOWN_TRADE
          2. No tech serves that trade+zone   → NO_ZONE_MATCH
          3. All matching techs already booked → ALL_BOOKED
          4. Otherwise                         → SUCCESS
        """
        if not request.is_complete():
            # Defensive: engine shouldn't be called with partial requests,
            # but if it is, treat it as a no-op failure. The chatbot layer
            # is responsible for re-prompting.
            return BookingResult(status=BookingStatus.UNKNOWN_TRADE)

        canonical = normalize_trade(request.trade)
        if canonical is None:
            return BookingResult(status=BookingStatus.UNKNOWN_TRADE)

        # All techs matching trade + zone, ignoring the ledger.
        trade_zone_matches = [
            t for t in self.seed.technicians
            if canonical in t.business_units and request.zip_code in t.zones
        ]
        if not trade_zone_matches:
            return BookingResult(status=BookingStatus.NO_ZONE_MATCH)

        # Now filter by availability.
        available = sorted(
            (t for t in trade_zone_matches
             if self.ledger.is_available(t.id, request.appointment_time)),
            key=lambda t: t.id,
        )
        if not available:
            return BookingResult(status=BookingStatus.ALL_BOOKED)

        chosen = available[0]
        booking = Booking(
            technician_id=chosen.id,
            technician_name=chosen.name,
            trade=canonical,
            zip_code=request.zip_code,
            appointment_time=request.appointment_time,
            customer_name=request.customer_name,
        )
        self.ledger.add(booking)
        return BookingResult(
            status=BookingStatus.SUCCESS,
            booking=booking,
            other_available_count=len(available) - 1,
        )