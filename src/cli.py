"""Command-line interface for the CSR chatbot.

Thin REPL wrapper around the `Chatbot` class. All business logic lives
in `chatbot.py` — this file is just I/O glue. The same `Chatbot` instance
is used by the FastAPI web UI in `web.py`, so changing behavior here
means changing it in exactly one place.
"""
from __future__ import annotations

import sys
from pathlib import Path

from src.chatbot import Chatbot
from src.data_loader import load_seed


_DEFAULT_SEED_PATH = Path(__file__).parent.parent / "data" / "seed.json"

_WELCOME = """
==============================================================
  CSR Chatbot — Book an appointment or ask about our services
==============================================================
Type your request in plain English. Examples:
  - "Book a plumber at 94115 for 2026-04-15 14:00"
  - "I need an electrician for Heather Russell on 2026-04-15 10:00"
  - "What services do you offer?"
  - "What areas do you serve?"

Commands: 'help' to see this again, 'reset' to start over, 'quit' to exit.
""".strip()

_HELP = _WELCOME


def main(seed_path: Path = _DEFAULT_SEED_PATH) -> int:
    try:
        seed = load_seed(seed_path)
    except Exception as e:
        print(f"Failed to load seed data from {seed_path}: {e}", file=sys.stderr)
        return 1

    bot = Chatbot(seed)
    print(_WELCOME)
    print()

    while True:
        try:
            user_input = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            return 0

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            return 0

        if user_input.lower() == "help":
            print(_HELP)
            continue

        response = bot.handle(user_input)
        # Indent multi-line responses so they're visually grouped
        print("bot > " + response.replace("\n", "\n      "))
        print()


if __name__ == "__main__":
    sys.exit(main())