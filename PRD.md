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

### ☐ Phase 3 — Intent Parser (~25 min)
- [ ] Tests first: `test_parser.py`
- [ ] `parser.py`: intent classification, regex extraction, name resolution
- [ ] Verification + commit

### ☐ Phase 4 — FAQ Handler (~10 min)
- [ ] Tests first: `test_faq.py`
- [ ] `faq.py`: pure functions
- [ ] Verification + commit

### ☐ Phase 5 — Chatbot Orchestrator + CLI (~20 min)
- [ ] Tests first: `test_chatbot.py`
- [ ] `chatbot.py` + `cli.py`
- [ ] Manual E2E verification + commit

### ☐ Phase 6 — FastAPI Web UI (~20 min) *[stretch]*
- [ ] `web.py`: POST /chat + GET / (single HTML page)
- [ ] TestClient smoke test
- [ ] Manual browser verification + commit

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