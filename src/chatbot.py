"""Chatbot orchestrator.

The `Chatbot` class is the glue layer. It owns:
  * The parser (stateless)
  * The booking engine (owns the ledger)
  * Multi-turn conversation state (pending booking, pending tech choice)
  * User-facing message formatting

Both the CLI and the web UI call a single method: `handle(message) -> str`.
Keeping formatting here (not in the parser or engine) means the underlying
logic modules return structured data and this layer decides how to render.

State machine:
  idle → (partial booking) → awaiting field → (completed) → idle
  idle → (complete booking, multi-eligible) → awaiting tech choice → idle
  idle → (complete booking, single-eligible) → idle

FAQs are stateless and don't affect pending state.
"""
from __future__ import annotations

from typing import Optional

from src.booking_engine import BookingEngine, BookingResult, BookingStatus
from src.faq import (
    answer_locations_question,
    answer_services_question,
    answer_unknown_question,
)
from src.models import Booking, BookingRequest, SeedData, Technician
from src.parser import Intent, parse


_RESET_KEYWORDS = {"reset", "cancel", "start over", "nevermind", "never mind"}


class Chatbot:
    """Stateful conversation orchestrator.

    One instance holds the conversation state for one user. For the CLI
    that's the whole process; for a multi-user web service you'd want
    one instance per session.
    """

    def __init__(self, seed: SeedData) -> None:
        self.seed = seed
        self.engine = BookingEngine(seed=seed)
        self._pending_booking: Optional[BookingRequest] = None
        self._pending_choices: tuple[Technician, ...] = ()
        self._pending_choice_request: Optional[BookingRequest] = None

    # ---------- public entry point ----------

    def handle(self, message: str) -> str:
        """Main entry point. Never raises; always returns a non-empty string."""
        if message is None or not message.strip():
            return answer_unknown_question()

        # Explicit reset always wins, even mid-conversation.
        if message.strip().lower() in _RESET_KEYWORDS:
            self._clear_state()
            return "Okay, let's start over. What can I help you with?"

        parsed = parse(message, self.seed)

        # FAQs are stateless — they don't affect any pending booking.
        if parsed.intent is Intent.FAQ_LOCATIONS:
            return answer_locations_question(self.seed)
        if parsed.intent is Intent.FAQ_SERVICES:
            return answer_services_question(self.seed)

        # If we're waiting for a tech choice, interpret this message as a pick.
        if self._pending_choices:
            tech = self._resolve_tech_choice(message)
            if tech is None:
                # Not a valid pick — re-show the options.
                return self._format_tech_choice_prompt()
            return self._commit_with_preferred_tech(tech)

        # User mentioned a trade word we don't service (e.g. "carpenter").
        # Short-circuit before the booking flow to give a clear response.
        if parsed.unrecognized_trade is not None:
            self._clear_state()
            return (
                f"I'm sorry, we don't offer {parsed.unrecognized_trade} services. "
                f"We offer Plumbing, Electrical, and HVAC."
            )

        # Booking intent (possibly partial) or anything with extractable info.
        if parsed.intent is Intent.BOOKING and parsed.booking_request is not None:
            return self._handle_booking(parsed.booking_request)

        # Fall through: nothing we can do.
        return answer_unknown_question()

    # ---------- booking flow ----------

    def _handle_booking(self, request: BookingRequest) -> str:
        # Merge with pending booking (if any) — new values take priority.
        if self._pending_booking is not None:
            request = self._merge(self._pending_booking, request)

        # Incomplete? Save state and prompt for the next missing field.
        if not request.is_complete():
            self._pending_booking = request
            return self._prompt_for_next_missing_field(request.missing_fields())

        # Complete — attempt to book.
        result = self.engine.book(request)
        return self._handle_booking_result(result, request)

    def _handle_booking_result(
        self,
        result: BookingResult,
        request: BookingRequest,
    ) -> str:
        if result.status is BookingStatus.SUCCESS:
            self._clear_state()
            return self._format_confirmation(result.booking)

        if result.status is BookingStatus.MULTIPLE_CHOICES:
            # Stash the request so we can re-submit it with the user's pick.
            self._pending_booking = None
            self._pending_choices = result.choices
            self._pending_choice_request = request
            return self._format_tech_choice_prompt()

        # All other statuses are terminal failures — clear state.
        self._clear_state()
        return self._format_failure(result.status, request)

    def _commit_with_preferred_tech(self, tech: Technician) -> str:
        """Re-submit the stashed request with the user's chosen technician."""
        request = self._pending_choice_request
        assert request is not None  # guaranteed by caller
        result = self.engine.book(request, preferred_technician_id=tech.id)
        return self._handle_booking_result(result, request)

    # ---------- merging + choice resolution ----------

    @staticmethod
    def _merge(old: BookingRequest, new: BookingRequest) -> BookingRequest:
        """Merge two booking requests. New values win when present."""
        return BookingRequest(
            trade=new.trade if new.trade is not None else old.trade,
            zip_code=new.zip_code if new.zip_code is not None else old.zip_code,
            appointment_time=(
                new.appointment_time
                if new.appointment_time is not None
                else old.appointment_time
            ),
            customer_name=(
                new.customer_name
                if new.customer_name is not None
                else old.customer_name
            ),
        )

    def _resolve_tech_choice(self, message: str) -> Optional[Technician]:
        """Interpret a message as a selection from the pending choices.

        Accepts:
          * A 1-indexed number ("1", "2")
          * A full name ("Michael Page") or substring ("Michael")
        Returns None if no match.
        """
        msg = message.strip().lower()
        if not msg:
            return None

        # Numeric pick (1-indexed so users don't have to learn 0-indexing)
        if msg.isdigit():
            idx = int(msg) - 1
            if 0 <= idx < len(self._pending_choices):
                return self._pending_choices[idx]
            return None

        # Name pick — prefer longest matching substring
        best: Optional[Technician] = None
        best_len = 0
        for tech in self._pending_choices:
            name_lower = tech.name.lower()
            if name_lower in msg or msg in name_lower:
                if len(name_lower) > best_len:
                    best = tech
                    best_len = len(name_lower)
        if best is not None:
            return best

        # First-name pick
        for tech in self._pending_choices:
            first = tech.name.split()[0].lower()
            if first in msg and len(first) > best_len:
                best = tech
                best_len = len(first)
        return best

    # ---------- state management ----------

    def _clear_state(self) -> None:
        self._pending_booking = None
        self._pending_choices = ()
        self._pending_choice_request = None

    # ---------- formatting ----------

    def _prompt_for_next_missing_field(self, missing: tuple[str, ...]) -> str:
        first = missing[0]
        if first == "trade":
            return (
                "What kind of service do you need? "
                "We offer Plumbing, Electrical, and HVAC."
            )
        if first == "zip_code":
            return "What is your ZIP code or location?"
        if first == "appointment_time":
            return (
                "What date and time would you like? "
                "Please use the format YYYY-MM-DD HH:MM "
                "(for example, 2026-04-15 14:00)."
            )
        return "Could you tell me more about what you need?"

    def _format_tech_choice_prompt(self) -> str:
        lines = ["Multiple technicians are available. Please choose one:"]
        for i, tech in enumerate(self._pending_choices, start=1):
            lines.append(f"  {i}) {tech.name}")
        lines.append("(Reply with the number or the technician's name.)")
        return "\n".join(lines)

    def _format_confirmation(self, booking: Booking) -> str:
        trade_display = "HVAC" if booking.trade == "hvac" else booking.trade.title()
        time_display = booking.appointment_time.strftime("%A, %B %d, %Y at %I:%M %p")
        lines = [
            "Booking confirmed!",
            f"  Technician: {booking.technician_name}",
            f"  Service:    {trade_display}",
            f"  Location:   ZIP {booking.zip_code}",
            f"  Time:       {time_display}",
        ]
        if booking.customer_name:
            lines.insert(2, f"  Customer:   {booking.customer_name}")
        return "\n".join(lines)

    def _format_failure(
        self,
        status: BookingStatus,
        request: BookingRequest,
    ) -> str:
        if status is BookingStatus.UNKNOWN_TRADE:
            return (
                f"I'm sorry, we don't offer that service. "
                f"We offer Plumbing, Electrical, and HVAC."
            )
        if status is BookingStatus.NO_ZONE_MATCH:
            return (
                f"I'm sorry, we don't have any technicians serving "
                f"ZIP {request.zip_code} for that service right now."
            )
        if status is BookingStatus.ALL_BOOKED:
            time_str = (
                request.appointment_time.strftime("%B %d at %I:%M %p")
                if request.appointment_time
                else "that time"
            )
            return (
                f"I'm sorry, all matching technicians are already booked "
                f"for {time_str}. Please try a different time."
            )
        if status is BookingStatus.OUTSIDE_BUSINESS_HOURS:
            return (
                "I'm sorry, we can only book appointments between "
                "9:00 AM and 5:00 PM. Please choose a time in that range."
            )
        return "I'm sorry, I couldn't complete your booking. Please try again."