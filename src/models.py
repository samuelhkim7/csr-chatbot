"""Domain models for the CSR chatbot.

All models are frozen dataclasses so they are hashable, immutable, and safe
to pass around without defensive copies. Collections on models use tuples
(not lists) for the same reason.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


_ZIP_RE = re.compile(r"\b(\d{5})\b")


@dataclass(frozen=True)
class Customer:
    id: int
    name: str
    contact: str


@dataclass(frozen=True)
class Location:
    id: int
    name: str
    address: str

    @property
    def zip_code(self) -> Optional[str]:
        """Extract the 5-digit ZIP from the address string.

        Addresses in the seed data end in a ZIP, e.g.
        '876 Paul Vista Apt. 335, San Francisco, CA, 94115'.
        We take the *last* 5-digit match because street numbers
        (e.g. '95281 Joshua Courts...') would otherwise win.
        """
        matches = _ZIP_RE.findall(self.address)
        return matches[-1] if matches else None


@dataclass(frozen=True)
class Technician:
    id: int
    name: str
    zones: tuple[str, ...]
    business_units: tuple[str, ...]


@dataclass(frozen=True)
class BookingRequest:
    """A (possibly partial) user request to book an appointment.

    Used as the parser's structured output before being handed to the
    booking engine. Fields may be None when the user hasn't supplied them yet.
    """
    trade: Optional[str] = None
    zip_code: Optional[str] = None
    appointment_time: Optional[datetime] = None
    customer_name: Optional[str] = None

    def missing_fields(self) -> tuple[str, ...]:
        """Return names of required fields still missing. `customer_name` is optional."""
        missing = []
        if self.trade is None:
            missing.append("trade")
        if self.zip_code is None:
            missing.append("zip_code")
        if self.appointment_time is None:
            missing.append("appointment_time")
        return tuple(missing)

    def is_complete(self) -> bool:
        return not self.missing_fields()


@dataclass(frozen=True)
class Booking:
    """A confirmed booking sitting in the in-memory ledger."""
    technician_id: int
    technician_name: str
    trade: str
    zip_code: str
    appointment_time: datetime
    customer_name: Optional[str] = None


@dataclass(frozen=True)
class SeedData:
    """Container for all loaded seed data with convenience lookups.

    Using tuples instead of lists keeps the whole object hashable and
    prevents accidental mutation by any layer above.
    """
    customers: tuple[Customer, ...] = field(default_factory=tuple)
    locations: tuple[Location, ...] = field(default_factory=tuple)
    technicians: tuple[Technician, ...] = field(default_factory=tuple)

    # ---------- lookups ----------

    def find_customer_by_name(self, name: str) -> Optional[Customer]:
        """Case-insensitive lookup. Tries exact match first, then substring.

        Substring match is deliberate so users can type just a first name
        ("Heather") and still resolve the customer.
        """
        if not name:
            return None
        needle = name.strip().lower()

        # Exact match wins
        for c in self.customers:
            if c.name.lower() == needle:
                return c

        # Fall back to substring match (first hit)
        for c in self.customers:
            if needle in c.name.lower():
                return c

        return None

    def find_location_by_id(self, location_id: int) -> Optional[Location]:
        for loc in self.locations:
            if loc.id == location_id:
                return loc
        return None

    def get_zip_for_customer(self, name: str) -> Optional[str]:
        """Resolve a customer name to their location's ZIP code.

        In the seed data, Customer.id matches the id of their Location,
        so we can use the shared id as the join key.
        """
        customer = self.find_customer_by_name(name)
        if customer is None:
            return None
        location = self.find_location_by_id(customer.id)
        return location.zip_code if location else None