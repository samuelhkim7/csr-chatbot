"""FastAPI web UI for the CSR chatbot.

Thin HTTP wrapper around the same `Chatbot` class the CLI uses. All
business logic lives in `chatbot.py` — this file owns:
  * One POST endpoint (`/chat`) that takes a message and returns a reply
  * One GET endpoint (`/`) that serves a minimal single-page chat UI
    (vanilla JS, no framework, no build step)
  * A module-level `Chatbot` singleton + a dependency-injectable getter
    so tests can swap in a fresh bot per test

Run locally with:
    uvicorn src.web:app --reload

Then open http://localhost:8000 in a browser.

**Session-state note:** The module-level singleton means all browser
tabs share the same conversation state. That's fine for a single-user
demo but would need session cookies (or a per-connection `Chatbot` in
a dict keyed by session id) for a real multi-user deployment. This is
the most obvious productionization step and is called out in the
presentation notes.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from src.chatbot import Chatbot
from src.data_loader import load_seed


_SEED_PATH = Path(__file__).parent.parent / "data" / "seed.json"

app = FastAPI(title="CSR Chatbot")

# Module-level singleton. Tests override `get_chatbot` via
# `app.dependency_overrides` so they get a fresh bot per test.
_default_chatbot: Chatbot = Chatbot(load_seed(_SEED_PATH))


def get_chatbot() -> Chatbot:
    """Dependency that returns the active Chatbot.

    Tests override this via `app.dependency_overrides[get_chatbot]` to
    inject a fresh bot so conversation state doesn't leak between tests.
    """
    return _default_chatbot


# ---------- request / response models ----------

class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


# ---------- endpoints ----------

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, bot: Chatbot = Depends(get_chatbot)) -> ChatResponse:
    """Handle a single user message. Returns the chatbot's reply."""
    return ChatResponse(reply=bot.handle(req.message))


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Serve the single-page chat UI."""
    return _INDEX_HTML


# ---------- embedded HTML ----------

_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>CSR Chatbot</title>
  <style>
    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      max-width: 720px;
      margin: 2em auto;
      padding: 0 1em;
      color: #24292e;
      background: #fff;
    }
    h1 { font-size: 1.5em; border-bottom: 1px solid #eee; padding-bottom: 0.5em; }
    .subtitle { color: #586069; font-size: 0.9em; margin-top: -0.5em; }
    #log {
      border: 1px solid #d1d5da;
      border-radius: 6px;
      padding: 1em;
      height: 480px;
      overflow-y: auto;
      background: #f6f8fa;
      font-size: 0.95em;
    }
    .msg { margin: 0.6em 0; line-height: 1.4; }
    .msg .tag {
      font-weight: 600;
      display: inline-block;
      width: 3.5em;
    }
    .msg.you .tag { color: #0366d6; }
    .msg.bot .tag { color: #28a745; }
    .msg .body { white-space: pre-wrap; }
    #input-row {
      display: flex;
      margin-top: 1em;
      gap: 0.5em;
    }
    #msg-input {
      flex: 1;
      padding: 0.6em 0.8em;
      font-size: 1em;
      border: 1px solid #d1d5da;
      border-radius: 6px;
      font-family: inherit;
    }
    #msg-input:focus {
      outline: none;
      border-color: #0366d6;
    }
    button {
      padding: 0.6em 1.2em;
      font-size: 1em;
      background: #0366d6;
      color: white;
      border: none;
      border-radius: 6px;
      cursor: pointer;
      font-family: inherit;
    }
    button:hover { background: #0256b3; }
    button:disabled { background: #959da5; cursor: not-allowed; }
    .hint {
      font-size: 0.85em;
      color: #586069;
      margin-top: 0.5em;
    }
    .hint code {
      background: #f1f2f4;
      padding: 0.1em 0.3em;
      border-radius: 3px;
      font-size: 0.95em;
    }
  </style>
</head>
<body>
  <h1>CSR Chatbot</h1>
  <p class="subtitle">Book a technician or ask about our services.</p>

  <div id="log"></div>

  <div id="input-row">
    <input id="msg-input" type="text" placeholder="Type a message..." autofocus autocomplete="off">
    <button id="send-btn" onclick="send()">Send</button>
  </div>

  <p class="hint">
    Try: <code>book a plumber at 94115 for 2026-04-15 14:00</code>
    or <code>what services do you offer?</code>
  </p>

  <script>
    const log = document.getElementById('log');
    const input = document.getElementById('msg-input');
    const sendBtn = document.getElementById('send-btn');

    function append(who, text) {
      const div = document.createElement('div');
      div.className = 'msg ' + who;
      const tag = document.createElement('span');
      tag.className = 'tag';
      tag.textContent = who === 'you' ? 'you >' : 'bot >';
      const body = document.createElement('span');
      body.className = 'body';
      body.textContent = ' ' + text;
      div.appendChild(tag);
      div.appendChild(body);
      log.appendChild(div);
      log.scrollTop = log.scrollHeight;
    }

    async function send() {
      const msg = input.value.trim();
      if (!msg) return;
      append('you', msg);
      input.value = '';
      sendBtn.disabled = true;
      try {
        const res = await fetch('/chat', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({message: msg}),
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        append('bot', data.reply);
      } catch (e) {
        append('bot', 'Error contacting server: ' + e.message);
      } finally {
        sendBtn.disabled = false;
        input.focus();
      }
    }

    input.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') send();
    });

    // Welcome message on load
    append('bot', 'Hi! I can help you book a plumber, electrician, or HVAC technician, or answer questions about our services and coverage areas.');
  </script>
</body>
</html>
"""