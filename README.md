# CSR Chatbot (UR Edition)

A simplified Customer Service Representative chatbot that books technician appointments and answers basic FAQs about services and coverage areas. Built as a take-home project exercising integration, testing, and clean code under a 2-hour time budget.

**What it does**
- Books 1-hour appointments by matching a user's trade + ZIP + time against a pool of technicians
- Enforces business hours (9 AM–5 PM) and prevents double-booking via an in-memory ledger
- Handles multi-turn conversations: follows up for missing fields, lets the user pick between multiple eligible technicians, and suggests the next available slot when a requested time is taken
- Answers FAQs about services offered and areas served, derived directly from the technician pool
- Exposes the same experience via a CLI or a FastAPI web UI — they share all business logic

**What it doesn't do** (deliberate non-goals — see [Future Work](#future-work))
- No authentication or user accounts
- No persistence across runs (in-memory only)
- No real natural language understanding (regex + keyword matching only)
- No real-time technician schedules beyond "not already booked"

## Quickstart

```bash
# 1. Clone and enter
git clone <repo-url> csr-chatbot && cd csr-chatbot

# 2. Virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the tests (181 of them)
python -m pytest

# 5a. Run the CLI
python -m src.cli

# 5b. Run the web UI
uvicorn src.web:app --reload
# then open http://localhost:8000
```

## Web UI Demo
   
https://github.com/user-attachments/assets/2bc365dc-8db4-4861-a24a-715df914579a

## Example Interactions

### Single-turn booking (complete info)
```
you > book a plumber at 94115 for 2026-04-15 14:00
bot > Booking confirmed!
        Technician: Michael Page
        Service:    Plumbing
        Location:   ZIP 94115
        Time:       Wednesday, April 15, 2026 at 02:00 PM
```

### Multi-turn booking (spec's example phrasing)
```
you > Book a plumbing appointment for me on 2026-04-15 14:00
bot > What is your ZIP code or location?
you > 94115
bot > Booking confirmed!
        Technician: Michael Page
        ...
```

### Informal date → format re-prompt
```
you > book a plumber at 94115 on wednesday
bot > I saw 'wednesday' but I need an exact date and time.
      Please use the format YYYY-MM-DD HH:MM (for example, 2026-04-15 14:00).
you > 2026-04-15 14:00
bot > Booking confirmed!
        ...
```
Note that trade and zip are preserved across the re-prompt — the user doesn't re-enter them.

### Multi-technician selection
```
you > book an electrician at 94115 for 2026-04-15 14:00
bot > Multiple technicians are available. Please choose one:
        1) Michael Page
        2) Christopher Johnson
      (Reply with the number or the technician's name.)
you > 2
bot > Booking confirmed!
        Technician: Christopher Johnson
        ...
```

### Booking by customer name (auto-resolves ZIP)
```
you > book a plumber for Heather Russell on 2026-04-15 15:00
bot > Booking confirmed!
        Technician: Michael Page
        Customer:   Heather Russell
        Service:    Plumbing
        Location:   ZIP 94111
        Time:       Wednesday, April 15, 2026 at 03:00 PM
```

### Friendly failure with next-slot suggestion
```
you > book a plumber at 94117 for 2026-04-15 14:00
bot > Booking confirmed! ...
you > book a plumber at 94117 for 2026-04-15 14:00
bot > I'm sorry, all matching technicians are already booked for April 15 at 02:00 PM.
      The next available time is Wednesday, April 15 at 03:00 PM.
```

### FAQs
```
you > what services do you offer?
bot > We offer 3 services: Electrical, HVAC, Plumbing.

you > what areas do you serve?
bot > We currently serve 11 ZIP codes in San Francisco: 94101, 94106, 94107,
      94111, 94113, 94115, 94117, 94118, 94119, 94120, 94133.
```

See [`DEMO.md`](DEMO.md) for a complete list of inputs that exercise every feature.

## Architecture

The codebase is organized in layers, with each layer knowing nothing about the layers above it:

```
┌─────────────────────────────────────────────┐
│       Interface layer                       │
│   cli.py              web.py                │
│   (REPL wrapper)      (FastAPI + HTML)      │
└──────────────────┬──────────────────────────┘
                   │ handle(message) -> str
┌──────────────────▼──────────────────────────┐
│       Orchestration layer                   │
│   chatbot.py                                │
│   - Multi-turn state                        │
│   - Message routing                         │
│   - User-facing formatting                  │
└──────┬────────────┬─────────────┬───────────┘
       │            │             │
┌──────▼────┐  ┌────▼───────┐  ┌──▼──────┐
│ parser.py │  │booking_    │  │ faq.py  │
│           │  │engine.py   │  │         │
│ text →    │  │            │  │ seed →  │
│ intent    │  │ matching + │  │ strings │
│           │  │ ledger     │  │         │
└──────┬────┘  └────┬───────┘  └──┬──────┘
       │            │             │
┌──────▼────────────▼─────────────▼──────────┐
│       Data layer                           │
│   models.py (dataclasses)                  │
│   data_loader.py (JSON → SeedData)         │
└────────────────────────────────────────────┘
```

**Why it matters:** the interface layer is purely I/O glue — no business logic. Swapping the CLI for the web UI in Phase 6 required zero changes to the layers below it. If a Slack bot or a REST API were needed next, the same pattern applies.

### Module responsibilities

| Module | Responsibility |
|---|---|
| `models.py` | Frozen dataclasses for `Customer`, `Location`, `Technician`, `Booking`, `BookingRequest`, `SeedData`. Derivations like `Location.zip_code` live as `@property`. |
| `data_loader.py` | Load + validate seed JSON. Fails fast on missing fields or malformed input via `SeedDataError`. |
| `parser.py` | Regex + keyword extraction. Produces a structured `ParsedIntent` with extracted fields, a list of missing fields for re-prompting, and flags for unrecognized trades and informal date mentions. |
| `booking_engine.py` | Trade alias normalization, technician matching, business hours enforcement, in-memory ledger with 1-hour slot overlap checks, multi-tech selection, next-available-slot search. |
| `faq.py` | Pure functions mapping `SeedData` to user-facing strings. |
| `chatbot.py` | Conversation state machine (pending booking, pending tech choice) and all user-facing message formatting. |
| `cli.py` | REPL loop. ~60 lines. No business logic. |
| `web.py` | FastAPI `POST /chat` + `GET /` (HTML page). Module-level `Chatbot` singleton with dependency-injectable getter for testing. |

## Key Design Decisions

### 1. Layered architecture with a thin orchestrator
All formatting lives in `chatbot.py`. The parser returns structured data, the engine returns `BookingStatus` enum values, and the orchestrator maps those to user-facing strings. This is the decision that made the web UI cost ~20 minutes instead of hours — swapping the I/O layer didn't require touching anything else.

### 2. Immutable domain models (frozen dataclasses, tuples not lists)
`SeedData`, `Technician`, `Customer`, etc. are frozen dataclasses with tuples for collections. The whole object is hashable and can't be mutated by accident. Simpler to reason about under time pressure.

### 3. `BookingLedger` as an explicit persistence seam
Today it's `dict[tech_id, set[datetime]]` in memory. Tomorrow it could be Postgres with an exclusion constraint on `(technician_id, time-range)`. The engine doesn't care about the internals — it calls `is_available(tech_id, when)` and `add(booking)`. This is the single most important extensibility point in the codebase.

### 4. Cascading failure statuses instead of exceptions
`BookingStatus` is an enum with `SUCCESS`, `MULTIPLE_CHOICES`, `UNKNOWN_TRADE`, `NO_ZONE_MATCH`, `ALL_BOOKED`, `OUTSIDE_BUSINESS_HOURS`. Failures aren't exceptional — they're expected branches of normal flow, and each one deserves a different user message. The chatbot pattern-matches on the status and formats a tailored response.

### 5. Auto-book when N=1, prompt when N≥2
When exactly one technician is eligible for a request, the engine books them silently. When two or more are eligible, it returns `MULTIPLE_CHOICES` with the list so the user can pick. This is a UX principle: never ask a user to confirm something that has no alternative.

### 6. Parser doesn't normalize trades — the engine does
`"plumber"` stays as `"plumber"` in the `BookingRequest`; `normalize_trade()` converts it to `"plumbing"` when the engine actually books. One source of truth for what each trade word means. This decision paid off when new trade aliases were added mid-project — the parser absorbed them with zero code changes because `TRADE_ALIASES` is a single dict.

### 7. Parser flags unrecognized input rather than dropping it
Two cases caused an early version of the bot to drop user input silently:
- `"book a carpenter"` → parser returned `trade=None`, chatbot asked "what service?", user typed "carpenter" again, infinite loop
- `"book on wednesday"` → parser returned `time=None`, chatbot asked for a time, user typed "wednesday" again, loop

The fix in both cases was the same: new fields `unrecognized_trade` and `unrecognized_datetime` on `ParsedIntent`, small lists of common unsupported words / informal patterns, short-circuit prompts in the chatbot layer. Two variants of the same bug, one pattern. The fact that the same fix pattern worked twice is a sign the architecture is coherent.

## Parser: Regex/Keyword vs LLM Tradeoffs

The project spec allowed either approach. I went with regex + keyword matching. The tradeoffs:

| Aspect | Regex + Keywords (chosen) | LLM |
|---|---|---|
| **Determinism** | ✅ Same input → same output, always | ❌ Nondeterministic; same prompt can yield different results |
| **Testability** | ✅ Every branch covered by a unit test | ⚠️ Hard to test; need mocks or fixtures, can't test every phrasing |
| **Latency** | ✅ Microseconds, local | ❌ Hundreds of ms, network round-trip |
| **Cost** | ✅ Free | ❌ Per-request API cost |
| **Availability** | ✅ Works offline | ❌ Depends on external API being up |
| **Hallucination risk** | ✅ None — can't invent a zip code | ❌ Could confidently produce wrong values |
| **Security** | ✅ No user input sent to third parties | ⚠️ Need data handling policy |
| **Natural language coverage** | ❌ Limited to patterns I wrote | ✅ Handles phrasings I never anticipated |
| **Maintenance on new phrasings** | ⚠️ Requires code changes | ✅ Usually handled automatically |
| **Language support** | ❌ English-only (currently) | ✅ Multilingual out of the box |

**For this project, determinism and testability won.** A live demo with the interviewer watching is the worst possible time for a network call to time out, an API key to be wrong, or a model to hallucinate a zip code. The regex approach is slightly more brittle to unexpected phrasings, but every phrasing it *does* handle is proven by a test.

**The architecture keeps the LLM option open.** The parser is a single function, `parse(message, seed) → ParsedIntent`. If an LLM-based parser were desired later, it would be a drop-in replacement — nothing downstream would change. A reasonable production setup would be: LLM primary, regex fallback on timeout or error, with regex tests still serving as regression coverage.

## Testing

181 tests across 6 test modules, organized by source module:

```bash
python -m pytest                                # run all tests
python -m pytest tests/test_booking_engine.py  # one module
python -m pytest -v                             # verbose
python -m pytest -k "multi_tech"                # by name substring
```

| Test module | Count | Focus |
|---|---|---|
| `test_data_loader.py` | 19 | JSON loading, validation, field extraction, error paths |
| `test_booking_engine.py` | 51 | Trade normalization, ledger overlap, matching, multi-tech, next-slot, business hours |
| `test_parser.py` | 55 | Intent classification, extraction, name resolution, informal dates, edge cases |
| `test_faq.py` | 12 | FAQ content, friendly name mapping, empty seed handling |
| `test_chatbot.py` | 36 | Multi-turn state, failure messages, reset command, tech choice flow |
| `test_web.py` | 8 | HTTP contract, multi-turn over HTTP, state isolation via dependency override |

### TDD discipline
Every phase was test-first. The git history shows `test: …` commits followed by `feat: …` or `fix: …` commits, so a reviewer can see the contract, then see the code written to satisfy it. Two bugs were caught this way before any manual testing:

- **Zip extraction regex matched the street number** (`95281 Joshua Courts...` returned `95281` instead of `94111`). Test for Heather Russell's zip failed, fix was one line.
- **FAQ singular/plural collision**: the marker `"service"` (singular) triggered FAQ_SERVICES on real booking requests containing `"electrical service"`. Changed to plural `"services"`. Caught by a parametrized test.

## Future Work

What I'd add with more time, roughly in priority order:

1. **Database-backed ledger** — swap `BookingLedger`'s internal dict for a Postgres table with a unique/exclusion constraint on `(technician_id, time-range)`. Handles multi-user concurrency and persistence at the same time. The `BookingLedger` class is already designed as the seam.
2. **Per-session conversation state** — the web UI currently uses a module-level `Chatbot` singleton. Replace with a dict keyed by session cookie, swapped in via the existing `get_chatbot` dependency. ~15 lines of change.
3. **Natural language date parsing** — accept `"tomorrow at 3pm"`, `"next Monday"`, etc. via a library like `dateutil` or `parsedatetime`. Deliberately deferred because it's a rathole of edge cases under time pressure; the current informal-date detection at least prompts users for the ISO format.
4. **Per-tech working hours and time off** — extend `Technician` with a schedule, extend `BookingLedger.is_available` to consult it. Today everyone works 9–5 every day.
5. **Variable slot durations per trade** — `SLOT_DURATION` is currently one constant. HVAC jobs might really need 2 hours, plumbing 30 minutes. A dict lookup in `is_available` would handle it in a few lines.
6. **Rescheduling and cancellation** — the ledger already tracks all bookings; add a `remove(booking_id)` method and parse "reschedule" / "cancel" intents.
7. **LLM fallback parser** — primary regex, LLM on unrecognized input, regex tests still running as regression coverage.
8. **More FAQs** — pricing, hours, appointment policies. The derived-from-data pattern in `faq.py` makes this easy.
9. **Structured logging** — today the code is clean of `print` statements, but no real logging. Production would need request IDs, booking events, etc.
10. **CI/CD + linting** — `pyproject.toml` with `ruff`, `mypy --strict`, pre-commit hooks, GitHub Actions running `pytest`.

## Project Structure

```
csr-chatbot/
├── README.md                 # this file
├── DEMO.md                   # copy-pasteable inputs that exercise every feature
├── PRD.md                    # build plan and phase checklist
├── PRESENTATION_NOTES.md     # running design decisions and demo beats
├── requirements.txt          # pytest, fastapi, uvicorn, httpx
├── conftest.py               # makes src/ importable from tests/
├── data/
│   └── seed.json             # customers, locations, technicians
├── src/
│   ├── __init__.py
│   ├── models.py             # domain dataclasses
│   ├── data_loader.py        # JSON → SeedData with validation
│   ├── parser.py             # text → ParsedIntent
│   ├── booking_engine.py     # matching + ledger + next-slot
│   ├── faq.py                # SeedData → formatted strings
│   ├── chatbot.py            # orchestrator + formatting
│   ├── cli.py                # REPL wrapper
│   └── web.py                # FastAPI app + embedded HTML
└── tests/
    ├── __init__.py
    ├── test_data_loader.py
    ├── test_booking_engine.py
    ├── test_parser.py
    ├── test_faq.py
    ├── test_chatbot.py
    └── test_web.py
```

## License

Built as an interview project. No license — please don't redistribute.