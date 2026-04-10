# PRD: CSR Chatbot (UR Edition)

## Goal
Build a CSR chatbot that (1) books appointments by matching trade + zip + time against technicians, maintaining an in-memory booking ledger to prevent double-booking, and (2) answers basic FAQs about services and coverage areas. Ship a polished CLI and, time permitting, a minimal FastAPI web chat UI.

## Stack & Key Decisions
| Decision | Choice | Why |
|---|---|---|
| Language | Python 3.10+ | Fast to iterate, stdlib covers most needs, `dataclasses` for clean models |
| Testing | `pytest` | Clean syntax, fixtures, parametrize |
| Parsing | Regex + keyword matching | Deterministic, testable, no API key / latency / hallucination risk during live demo |
| Interface | CLI first, FastAPI web stretch | CLI guarantees a working demo; web is the "bonus points" the spec mentions |
| Web UI (if reached) | FastAPI + single static HTML page | No frontend framework overhead; one file, one endpoint |
| Persistence | In-memory only | Spec says no DB; bookings live in a `BookingLedger` object |
| Data source | `data/seed.json` | Not hardcoded in source; easy to swap for tests |

## Architecture
```
csr-chatbot/
├── PRD.md
├── README.md
├── requirements.txt
├── .gitignore
├── data/seed.json
├── src/
│   ├── __init__.py
│   ├── models.py             # dataclasses
│   ├── data_loader.py        # JSON → models
│   ├── booking_engine.py     # matching + ledger
│   ├── parser.py             # text → intent
│   ├── faq.py                # FAQ handlers
│   ├── chatbot.py            # orchestrator
│   ├── cli.py                # CLI entry point
│   └── web.py                # FastAPI (stretch)
└── tests/
    ├── test_data_loader.py
    ├── test_booking_engine.py
    ├── test_parser.py
    ├── test_faq.py
    └── test_chatbot.py
```

**Key design principle:** `chatbot.py` is the orchestrator so both `cli.py` and `web.py` reuse identical logic — no business logic leaks into the interface layer.

## Phases

### ☑ Phase 0 — Repo Setup & Scaffold ✅
- [x] `git init`, create directory structure
- [x] `.gitignore`, `requirements.txt`
- [x] Virtualenv + install deps
- [x] `data/seed.json`
- [x] README stub + PRD.md committed
- [x] Initial commit (`f8a7b6b chore: scaffold repo, seed data, and PRD`)

### ☑ Phase 1 — Domain Models & Data Loading ✅
- [x] Tests first: `test_data_loader.py` (19 tests, all passing)
- [x] `models.py`: frozen dataclasses for Customer, Location, Technician, Booking, BookingRequest, SeedData
- [x] `data_loader.py`: load + validate JSON with `SeedDataError`
- [x] Verification: all 10/10/5 records loaded, customer/location IDs match 1:1, zip extraction fixed to use last-match
- [x] Commits: `01f0ac7 test: add data loader and domain model tests`, `bba71ba feat: add domain models and JSON seed loader`

### ☑ Phase 2 — Booking Engine ✅
- [x] Tests first: `test_booking_engine.py` (37 tests: trade aliases, ledger, matching, double-booking, tiebreak, failure cascade)
- [x] `booking_engine.py`: `TRADE_ALIASES`, `normalize_trade`, `BookingLedger`, `BookingEngine`, `BookingStatus` enum, `BookingResult`
- [x] Returns structured `BookingResult` (not bool) so chatbot layer can format messages
- [x] Verification: end-to-end trace of 5 scenarios (happy, fallback, exhausted, no-coverage, unknown-trade)
- [x] Commits: `test: add booking engine and ledger tests`, `feat: add booking engine with in-memory ledger`

### ☑ Phase 3 — Intent Parser ✅
- [x] Tests first: `test_parser.py` (35 tests: FAQ classification, trade extraction, ISO datetime, zip, customer name resolution, partial parses, unknown intents)
- [x] `parser.py`: `Intent` enum, `ParsedIntent` frozen dataclass, `parse(message, seed)` with regex + keyword matching
- [x] Auto-resolves zip from customer name when zip is missing; explicit zip always wins
- [x] Verification: 10 realistic phrases parsed manually end-to-end, all correct
- [x] Fixed singular/plural FAQ marker collision (`"electrical service"` booking vs `"what services do you offer"` FAQ)
- [x] Commits: `test: add intent parser tests`, `feat: add regex + keyword intent parser`

### ☑ Phase 3.5 — Mid-Project Revision ✅
*Triggered by check-in feedback. Expanded core behavior based on clarifications.*
- [x] **Business hours 9:00–17:00 enforced.** New `BookingStatus.OUTSIDE_BUSINESS_HOURS`. Last valid start is 16:00 so the 1-hour slot ends exactly at close.
- [x] **1-hour slot overlap.** Ledger's `is_available` now compares `|when - existing| < 1h` rather than exact-match set membership. Bookings at 14:00 and 14:30 conflict; 14:00 and 15:00 don't.
- [x] **Multi-tech selection.** New `BookingStatus.MULTIPLE_CHOICES` carries a list of eligible technicians. `book()` has a new `preferred_technician_id` parameter for the user's pick. Auto-book still applies when exactly 1 tech is eligible.
- [x] **Expanded trade vocabulary.** Plural forms (`plumbers`, `electricians`), slash form (`a/c`), abbreviation (`air con`) all added to `TRADE_ALIASES`.
- [x] **ZIP+4 format.** `94115-1234` extracts `94115` (hyphen forms a regex word boundary — no code change needed, just a test).
- [x] **First-person + standalone follow-up responses.** Parser already handled `"for me"` phrasing and bare `"94115"`/`"plumbing"` responses — verified with new tests. No impl change needed; the original design absorbed it.
- [x] Tests: +16 new (10 engine, 6 parser). Total suite: 115 passing.
- [x] Commits:
  - `test: update booking engine tests for business hours and multi-tech selection`
  - `refactor: enforce business hours, 1hr slot overlap, and multi-tech selection`
  - `test: expand parser tests for plural trades, zip+4, first-person phrasings`

### ☑ Phase 4 — FAQ Handler ✅
- [x] Tests first: `test_faq.py` (12 tests: locations/services content, friendly name mapping, empty seed graceful handling, determinism)
- [x] `faq.py`: three pure functions — `answer_locations_question`, `answer_services_question`, `answer_unknown_question`
- [x] Services mapped through `_SERVICE_DISPLAY_NAMES` (HVAC stays uppercase, others title-cased)
- [x] Verification: actual responses printed and reviewed; 11 zones, 3 services, helpful unknown fallback
- [x] Commits: `test: add FAQ handler tests`, `feat: add FAQ handler with derived locations and services`

### ☑ Phase 5 — Chatbot Orchestrator + CLI ✅
- [x] Tests first: `test_chatbot.py` (25 tests: FAQ pass-through, single-turn + multi-turn booking, tech choice flows, failure messages, state management, reset command)
- [x] `chatbot.py`: `Chatbot` class with `handle(message) -> str` as the single entry point; multi-turn state for pending booking + pending tech choice
- [x] `cli.py`: REPL wrapper with welcome banner, help, reset, quit commands
- [x] Parser enhancement: `unrecognized_trade` field on `ParsedIntent` so words like "carpenter" get a proper "we don't offer that" response instead of looping on the trade prompt
- [x] Verification: 9 end-to-end scenarios manually validated (spec phrasing, tech choice, auto-book, name-based, FAQs, unsupported trade, no coverage, outside hours, double-booking)
- [x] Commits: `test: add chatbot orchestrator tests for multi-turn conversation state`, `feat: add chatbot orchestrator and CLI`

### ☑ Phase 5.5 — Follow-on Polish ✅
*Small improvements driven by check-in feedback and self-testing.*
- [x] **Informal datetime detection** (`"wednesday"`, `"3pm"`, `"tomorrow"`) — parser flags, chatbot prompts for ISO. Preserves partial state across the re-prompt.
- [x] **Next available slot suggestion** — `BookingEngine.find_next_available_slot` iterates hour-by-hour within business hours for up to 7 days; chatbot's `ALL_BOOKED` message now includes "The next available time is X" when a suggestion can be found.
- [x] Commits: `fix: detect informal datetimes`, `feat: suggest next available slot on double-booking`

### ☑ Phase 6 — FastAPI Web UI ✅
- [x] Tests first: `test_web.py` (8 tests: root HTML, POST /chat contract, validation errors, multi-turn state persistence, tech choice flow, state isolation between tests via dependency override)
- [x] `web.py`: FastAPI app with `POST /chat` and `GET /`; module-level `Chatbot` singleton + `get_chatbot` dependency for test injection
- [x] Single-page chat UI embedded as an HTML string constant (no separate static directory, no build step, vanilla JS)
- [x] Pydantic `ChatRequest` / `ChatResponse` models for validation
- [x] Verification: booted `uvicorn` on port 8765, confirmed GET / serves HTML and POST /chat returns correct replies for booking and FAQ
- [x] Commits: `test: add FastAPI web UI smoke tests`, `feat: add FastAPI web UI with single-page chat`

### ☐ Phase 7 — README & Polish (~10 min)
- [ ] Full README: quickstart, examples, architecture, design decisions, regex vs LLM tradeoffs, future work
- [ ] Docstrings
- [ ] Final commit

## Explicit Non-Goals
- No authentication
- No persistence across runs
- No real NL understanding (regex + keywords only)
- No real-time technician schedule data (availability = "not already booked")

## Definition of Done
- [ ] All tests pass (`pytest`)
- [ ] CLI runs: happy path, no-availability, double-booking, name-based booking, both FAQs
- [ ] Web UI runs (if Phase 6 reached)
- [ ] README has quickstart + 3+ example interactions
- [ ] PRD checklist fully ticked
- [ ] Clean git history (one commit per phase)