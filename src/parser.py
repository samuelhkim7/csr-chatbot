"""Intent parser for the CSR chatbot.

Takes a raw user message and returns a structured `ParsedIntent`. The
parser is deliberately deterministic (regex + keyword matching) rather
than LLM-based — see README for the tradeoffs.

Responsibilities:
  * Classify the message as BOOKING, FAQ_LOCATIONS, FAQ_SERVICES, or UNKNOWN
  * For booking intents, extract as many fields as possible (trade, zip,
    datetime, customer name)
  * Resolve a customer name → zip via SeedData when the zip is otherwise
    missing (so "book for Heather Russell" works without the user typing
    their zip)
  * Report missing required fields so the chatbot can re-prompt

The parser does NOT normalize the trade word (e.g. "plumber" stays as
"plumber", not "plumbing"). That normalization happens in the booking
engine, so the parser's job stays purely about extraction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from src.booking_engine import TRADE_ALIASES
from src.models import BookingRequest, Customer, SeedData


# ---------- public types ----------

class Intent(Enum):
    BOOKING = "booking"
    FAQ_LOCATIONS = "faq_locations"
    FAQ_SERVICES = "faq_services"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ParsedIntent:
    intent: Intent
    booking_request: Optional[BookingRequest] = None
    missing_fields: tuple[str, ...] = ()
    raw_message: str = ""
    #: Set when the user mentioned a trade word we recognize as a trade
    #: but don't offer (e.g. "carpenter"). Lets the chatbot respond with
    #: "we don't offer X" instead of getting stuck prompting for the trade.
    unrecognized_trade: Optional[str] = None
    #: Set when the user mentioned something date/time-ish we couldn't
    #: parse as a real datetime (e.g. "wednesday", "tomorrow", "3pm").
    #: Lets the chatbot prompt for the ISO format instead of silently
    #: dropping the input.
    unrecognized_datetime: Optional[str] = None


# ---------- keyword tables ----------

# Checked in order. Longer/more specific phrases come first so that e.g.
# "air conditioning" is matched before a looser "ac" could grab it.
_TRADE_KEYWORDS: tuple[str, ...] = tuple(
    sorted(TRADE_ALIASES.keys(), key=len, reverse=True)
)

# FAQ classification is pure substring-on-lowercased-message. Services is
# checked first because "offer" and "services" are more specific than the
# broader location cues.
_FAQ_SERVICE_MARKERS: tuple[str, ...] = (
    "services", "trades", "offer", "kind of work", "types of work", "what do you do",
)
_FAQ_LOCATION_MARKERS: tuple[str, ...] = (
    "locations", "areas", "zip codes", "neighborhoods", "coverage", "cities",
    "where do you", "where are you",
)

_BOOKING_VERBS: tuple[str, ...] = (
    "book", "schedule", "appointment", "reserve", "set up", "make an",
    "help me find", "i need", "need a", "need an",
)

# Known trades we don't offer. Tracked separately so the chatbot can
# distinguish "user typed an unknown word" from "user typed a trade we
# don't service" — the two cases deserve different responses.
_UNSUPPORTED_TRADE_WORDS: tuple[str, ...] = (
    "carpenter", "carpentry",
    "painter", "painting",
    "roofer", "roofing",
    "handyman",
    "landscaper", "landscaping",
    "cleaner", "cleaning",
    "mover", "moving",
    "locksmith",
)

# ---------- regex ----------

# Matches YYYY-MM-DD followed by either 'T' or space, then HH:MM, with
# optional :SS. Loose validation only — `fromisoformat` does the real check.
_DATETIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?)")
_ZIP_RE = re.compile(r"\b(\d{5})\b")

# Informal date/time hints we can't actually parse, but want to flag so
# the chatbot can prompt for the ISO format instead of silently ignoring
# them. Case-insensitive. Order matters: longer multi-word phrases come
# first so "next week" wins over "week".
_INFORMAL_DATETIME_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Multi-word relative ranges
    re.compile(r"\b(next|this|last)\s+(week|weekend|month)\b", re.IGNORECASE),
    # Days of the week (full and common short forms)
    re.compile(
        r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"mon|tues?|wed|thurs?|fri|sat|sun)\b",
        re.IGNORECASE,
    ),
    # Relative day terms
    re.compile(r"\b(today|tomorrow|tonight|yesterday)\b", re.IGNORECASE),
    # AM/PM times like "3pm", "3:30 pm", "11:00 AM"
    re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)\b", re.IGNORECASE),
    # Fuzzy times of day
    re.compile(r"\b(morning|afternoon|evening|noon|midnight)\b", re.IGNORECASE),
)


# ---------- main entry point ----------

def parse(message: str, seed: SeedData) -> ParsedIntent:
    """Parse a raw user message into a structured intent.

    Always returns a `ParsedIntent`; never raises. Unparseable messages
    surface as `Intent.UNKNOWN`; partial booking requests surface as
    `Intent.BOOKING` with entries in `missing_fields`.
    """
    raw = message
    msg = (message or "").strip()
    if not msg:
        return ParsedIntent(intent=Intent.UNKNOWN, raw_message=raw)

    msg_lower = msg.lower()

    # FAQ detection runs first so that "do you offer plumbing?" classifies
    # as a services question rather than a booking.
    faq_intent = _detect_faq(msg_lower)
    if faq_intent is not None:
        return ParsedIntent(intent=faq_intent, raw_message=raw)

    # Booking extraction
    trade = _extract_trade(msg_lower)
    unrecognized_trade = _extract_unsupported_trade(msg_lower) if trade is None else None
    appointment_time = _extract_datetime(msg)
    unrecognized_datetime = (
        _extract_unrecognized_datetime(msg) if appointment_time is None else None
    )
    zip_code = _extract_zip(msg)
    customer = _extract_customer(msg, seed)
    customer_name = customer.name if customer else None

    # If the user gave a customer name but no explicit zip, resolve via
    # the seed data. Explicit zip always wins — we respect what the user
    # actually typed.
    if zip_code is None and customer is not None:
        zip_code = seed.get_zip_for_customer(customer.name)

    anything_extracted = any((trade, appointment_time, zip_code, customer_name))
    has_booking_verb = any(v in msg_lower for v in _BOOKING_VERBS)
    has_unrecognized_hint = unrecognized_trade is not None or unrecognized_datetime is not None

    if not (anything_extracted or has_booking_verb or has_unrecognized_hint):
        return ParsedIntent(intent=Intent.UNKNOWN, raw_message=raw)

    booking_request = BookingRequest(
        trade=trade,
        zip_code=zip_code,
        appointment_time=appointment_time,
        customer_name=customer_name,
    )
    return ParsedIntent(
        intent=Intent.BOOKING,
        booking_request=booking_request,
        missing_fields=booking_request.missing_fields(),
        raw_message=raw,
        unrecognized_trade=unrecognized_trade,
        unrecognized_datetime=unrecognized_datetime,
    )


# ---------- helpers ----------

def _detect_faq(msg_lower: str) -> Optional[Intent]:
    if any(marker in msg_lower for marker in _FAQ_SERVICE_MARKERS):
        return Intent.FAQ_SERVICES
    if any(marker in msg_lower for marker in _FAQ_LOCATION_MARKERS):
        return Intent.FAQ_LOCATIONS
    return None


def _extract_trade(msg_lower: str) -> Optional[str]:
    """Return the longest matching trade alias, or None.

    Longer matches win so "air conditioning" beats a later "ac" match.
    Word boundaries prevent "heat" from matching "heater" and similar.
    """
    for alias in _TRADE_KEYWORDS:
        if re.search(rf"\b{re.escape(alias)}\b", msg_lower):
            return alias
    return None


def _extract_unsupported_trade(msg_lower: str) -> Optional[str]:
    """Return the first unsupported trade word found, or None.

    Called only when `_extract_trade` returned None. Lets the chatbot
    distinguish "user typed gibberish" from "user typed a real trade
    we don't happen to offer."
    """
    for word in _UNSUPPORTED_TRADE_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", msg_lower):
            return word
    return None


def _extract_datetime(msg: str) -> Optional[datetime]:
    """Extract the first ISO-ish datetime, validated via `fromisoformat`.

    Returns None for malformed input rather than raising — the caller
    surfaces it as a missing field for re-prompting.
    """
    match = _DATETIME_RE.search(msg)
    if not match:
        return None
    try:
        # `fromisoformat` (3.11+) accepts both 'T' and space separators.
        return datetime.fromisoformat(match.group(1))
    except ValueError:
        return None


def _extract_unrecognized_datetime(msg: str) -> Optional[str]:
    """Return the first informal date/time hint found, or None.

    Called only when `_extract_datetime` returned None. Lets the chatbot
    distinguish "user gave no date at all" from "user tried to give a
    date but used a format we can't parse."
    """
    for pattern in _INFORMAL_DATETIME_PATTERNS:
        match = pattern.search(msg)
        if match:
            return match.group(0)
    return None


def _extract_zip(msg: str) -> Optional[str]:
    match = _ZIP_RE.search(msg)
    return match.group(1) if match else None


def _extract_customer(msg: str, seed: SeedData) -> Optional[Customer]:
    """Find the best customer mention in the message.

    Strategy:
      1. Prefer a full-name substring match (longest match wins).
      2. Fall back to a first-name whole-word match.

    This lets users type "book for Heather Russell" or just "book for
    Heather" and hit the same customer.
    """
    msg_lower = msg.lower()
    best: Optional[Customer] = None
    best_len = 0

    # Pass 1: full-name substring
    for customer in seed.customers:
        name_lower = customer.name.lower()
        if name_lower in msg_lower and len(name_lower) > best_len:
            best = customer
            best_len = len(name_lower)
    if best is not None:
        return best

    # Pass 2: first-name whole-word
    for customer in seed.customers:
        first = customer.name.split()[0].lower()
        if re.search(rf"\b{re.escape(first)}\b", msg_lower) and len(first) > best_len:
            best = customer
            best_len = len(first)

    return best