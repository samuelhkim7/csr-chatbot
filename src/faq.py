"""FAQ handlers for the CSR chatbot.

Pure functions that take `SeedData` and return user-facing strings. The
content is derived entirely from the technician pool — there's no
separate FAQ database — so coverage and services automatically stay in
sync with whatever techs the company employs.

These return strings rather than structured data because every caller
today (CLI, web UI) just displays them verbatim. If a future consumer
needed richer data (e.g. JSON for a frontend), these would become the
formatters and a parallel pair of functions would return the raw sets.
"""
from __future__ import annotations

from src.models import SeedData


# Friendly display names for business_units values. Anything not in this
# map falls back to title case (fine for most trade names, but "hvac"
# needs an explicit entry to stay uppercase).
_SERVICE_DISPLAY_NAMES: dict[str, str] = {
    "plumbing": "Plumbing",
    "electrical": "Electrical",
    "hvac": "HVAC",
}


def answer_locations_question(seed: SeedData) -> str:
    """Return a formatted list of all ZIP codes served.

    Derived from the union of all `zones` across the technician pool.
    Sorted for deterministic output.
    """
    zones = sorted({zone for tech in seed.technicians for zone in tech.zones})
    if not zones:
        return "We don't currently have any service areas configured."
    return (
        f"We currently serve {len(zones)} ZIP codes in San Francisco: "
        f"{', '.join(zones)}."
    )


def answer_services_question(seed: SeedData) -> str:
    """Return a formatted list of services offered.

    Derived from the union of all `business_units` across the technician
    pool, then mapped through `_SERVICE_DISPLAY_NAMES` for presentation.
    """
    units = sorted({unit for tech in seed.technicians for unit in tech.business_units})
    if not units:
        return "We don't currently offer any services."
    display = [_SERVICE_DISPLAY_NAMES.get(u, u.title()) for u in units]
    return f"We offer {len(display)} services: {', '.join(display)}."


def answer_unknown_question() -> str:
    """Fallback when the user asks something we don't understand.

    Deliberately nudges toward the two things the bot can actually do,
    rather than apologizing generically.
    """
    return (
        "I'm not sure I understood that. I can help you book an appointment "
        "with one of our technicians, or answer questions about the services "
        "we offer and the areas we cover. What would you like to do?"
    )