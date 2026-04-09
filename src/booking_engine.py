"""Booking engine for the CSR chatbot.

Owns the business logic for matching technicians to requests, enforcing
business-hours and 1-hour slot invariants, and supporting multi-tech
selection when several technicians could take a job.

Design notes:
  * `BookingLedger` is the seam for persistence. Today it's an in-memory
    dict; tomorrow it could be Postgres with an exclusion constraint over
    (technician_id, time-range). Overlap checking happens here, not in
    the caller.
  * `BookingEngine.book()` returns a `BookingResult` discriminated by a
    `BookingStatus` enum rather than raising. Failures aren't exceptional —
    they're expected branches the chatbot surfaces as user messages.
  * When exactly one technician is eligible the engine auto-books (nicer
    UX — no pointless confirmation). When two or more are eligible it
    returns `MULTIPLE_CHOICES` with the list, and the caller re-invokes
    `book()` with `preferred_technician_id` once the user picks.
  * Business hours are 9:00-17:00 inclusive. Appointments are 1 hour long,
    so the latest valid start is 16:00 (slot ends exactly at close).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from src.models import Booking, BookingRequest, SeedData, Technician


# ---------- business-hours + slot constants ----------

BUSINESS_HOUR_OPEN = 9    # 9:00 AM
BUSINESS_HOUR_CLOSE = 17  # 5:00 PM
SLOT_DURATION = timedelta(hours=1)


# ---------- trade vocabulary ----------

#: Maps user-facing trade words to the canonical `business_units` values
#: used in the seed data. Keys are lowercased; lookups normalize input.
TRADE_ALIASES: dict[str, str] = {
    # plumbing
    "plumber": "plumbing",
    "plumbers": "plumbing",
    "plumbing": "plumbing",
    # electrical
    "electrician": "electrical",
    "electricians": "electrical",
    "electrical": "electrical",
    "electric": "electrical",
    # hvac — several common phrasings
    "hvac": "hvac",
    "ac": "hvac",
    "a/c": "hvac",
    "air con": "hvac",
    "air conditioning": "hvac",
    "heating": "hvac",
    "heat": "hvac",
}


def normalize_trade(trade: str) -> Optional[str]:
    """Canonicalize a user-facing trade word. Returns None if unknown."""
    if not trade or not trade.strip():
        return None
    return TRADE_ALIASES.get(trade.strip().lower())


# ---------- results ----------

class BookingStatus(Enum):
    SUCCESS = "success"
    MULTIPLE_CHOICES = "multiple_choices"
    UNKNOWN_TRADE = "unknown_trade"
    NO_ZONE_MATCH = "no_zone_match"
    ALL_BOOKED = "all_booked"
    OUTSIDE_BUSINESS_HOURS = "outside_business_hours"


@dataclass(frozen=True)
class BookingResult:
    """Outcome of a booking attempt.

    `choices` is populated only when `status is MULTIPLE_CHOICES`; the
    caller presents these to the user and re-invokes `book()` with the
    chosen technician id. `other_available_count` is populated on success
    and tells the caller how many additional techs could also have taken
    the job.
    """
    status: BookingStatus
    booking: Optional[Booking] = None
    choices: tuple[Technician, ...] = ()
    other_available_count: int = 0

    @property
    def success(self) -> bool:
        return self.status is BookingStatus.SUCCESS


# ---------- business-hours helper ----------

def _is_within_business_hours(when: datetime) -> bool:
    """A 1-hour slot starting at `when` must fit entirely within [9:00, 17:00).

    This means:
      * start >= 09:00
      * start + 1hr <= 17:00 (i.e. start <= 16:00)
    """
    open_at = when.replace(hour=BUSINESS_HOUR_OPEN, minute=0, second=0, microsecond=0)
    close_at = when.replace(hour=BUSINESS_HOUR_CLOSE, minute=0, second=0, microsecond=0)
    if when < open_at:
        return False
    if when + SLOT_DURATION > close_at:
        return False
    return True


# ---------- ledger ----------

class BookingLedger:
    """In-memory store of confirmed bookings with 1-hour overlap checks.

    Internal storage: `dict[tech_id, set[datetime]]`. We iterate the set
    on `is_available` and compare against `SLOT_DURATION`. For the scale
    of a 2-hour project this is plenty; at real scale an interval tree
    or DB-backed range query would be appropriate.
    """

    def __init__(self) -> None:
        self._slots: dict[int, set[datetime]] = {}
        self._all: list[Booking] = []

    def is_available(self, tech_id: int, when: datetime) -> bool:
        """True if no existing booking for `tech_id` overlaps `[when, when+1hr)`.

        Two slots overlap if their start times are strictly less than
        `SLOT_DURATION` apart in either direction.
        """
        for existing in self._slots.get(tech_id, ()):
            if abs(when - existing) < SLOT_DURATION:
                return False
        return True

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
        """Technicians serving the trade + zone AND free at the requested time.

        Returns sorted by id for determinism. Returns `[]` for unknown
        trades or partial requests; `book()` distinguishes those cases via
        its own cascade.
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

    def book(
        self,
        request: BookingRequest,
        preferred_technician_id: Optional[int] = None,
    ) -> BookingResult:
        """Attempt to book, returning a structured result.

        Cascade:
          1. Incomplete request / unknown trade   → UNKNOWN_TRADE
          2. Outside business hours               → OUTSIDE_BUSINESS_HOURS
          3. No tech serves that trade+zone       → NO_ZONE_MATCH
          4. All matching techs are booked        → ALL_BOOKED
          5. Multiple eligible, no preferred      → MULTIPLE_CHOICES
          6. Single eligible, or preferred given  → SUCCESS (auto-book)
        """
        if not request.is_complete():
            return BookingResult(status=BookingStatus.UNKNOWN_TRADE)

        canonical = normalize_trade(request.trade)
        if canonical is None:
            return BookingResult(status=BookingStatus.UNKNOWN_TRADE)

        assert request.appointment_time is not None  # is_complete() guarantees this
        if not _is_within_business_hours(request.appointment_time):
            return BookingResult(status=BookingStatus.OUTSIDE_BUSINESS_HOURS)

        # Techs matching trade + zone, ignoring the ledger.
        trade_zone_matches = [
            t for t in self.seed.technicians
            if canonical in t.business_units and request.zip_code in t.zones
        ]
        if not trade_zone_matches:
            return BookingResult(status=BookingStatus.NO_ZONE_MATCH)

        # Now filter by ledger availability.
        available = sorted(
            (t for t in trade_zone_matches
             if self.ledger.is_available(t.id, request.appointment_time)),
            key=lambda t: t.id,
        )
        if not available:
            return BookingResult(status=BookingStatus.ALL_BOOKED)

        # Pick the technician.
        if preferred_technician_id is not None:
            chosen = next((t for t in available if t.id == preferred_technician_id), None)
            if chosen is None:
                # The preferred tech isn't eligible. Could be because they
                # don't cover the trade/zone, or they were booked in the
                # meantime. Either way: can't fulfill the specific request.
                return BookingResult(status=BookingStatus.ALL_BOOKED)
        elif len(available) == 1:
            chosen = available[0]
        else:
            return BookingResult(
                status=BookingStatus.MULTIPLE_CHOICES,
                choices=tuple(available),
            )

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