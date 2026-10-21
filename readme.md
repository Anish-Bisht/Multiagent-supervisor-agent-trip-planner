# ✈️ TripMate AI — Multi-Agent Travel Planner

> A production-grade agentic application built with **LangGraph**, **FastAPI**, and the **Model Context Protocol (MCP)** that turns a natural-language trip request into a fully structured travel plan — with a human-in-the-loop approval gate before finalization.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Agent Descriptions](#agent-descriptions)
4. [Human-in-the-Loop (HITL)](#human-in-the-loop-hitl)
5. [MCP Tool Integrations](#mcp-tool-integrations)
6. [State Management](#state-management)
7. [API Reference](#api-reference)
8. [Project Structure](#project-structure)
9. [Tech Stack](#tech-stack)
10. [Prerequisites](#prerequisites)
11. [Environment Variables](#environment-variables)
12. [Installation & Running](#installation--running)
13. [Known Issues & Limitations](#known-issues--limitations)

---

## Overview

TripMate AI is a **multi-agent system** where a **Supervisor Agent** dynamically orchestrates five specialist agents to handle different aspects of travel planning:

- ✈️ **Flights** — AviationStack airport & airline data
- 🏨 **Hotels** — Tavily web search
- 🌤️ **Weather** — Custom OpenWeatherMap MCP server
- 💰 **Budget** — LLM-powered cost feasibility analysis
- 🗺️ **Itinerary** — Integrated day-by-day plan

The system pauses for **human review** of the draft itinerary before the final polished response is generated, using LangGraph's `interrupt()` mechanism.

---

## Architecture

```
User Request
     │
     ▼
┌─────────────────────────────────────┐
│          Supervisor Agent           │
│  • Input Guardrail (travel check)   │
│  • Selects relevant specialist      │
│    agents for this query            │
│  • Extracts trip constraints        │
└──────────────┬──────────────────────┘
               │
    ┌──────────▼──────────┐
    │  Guardrail Blocked? ├──YES──► Return rejection message ──► END
    └──────────┬──────────┘
               │ NO
    ┌──────────▼──────────────────────────────────────────────┐
    │              Specialist Agents (dynamic selection)      │
    │                                                         │
    │  flight_agent ──► hotel_agent ──► weather_agent         │
    │                         └──► budget_agent               │
    └──────────────────────────────┬──────────────────────────┘
                                   │
                          ┌────────▼────────┐
                          │ itinerary_agent │
                          │ (always runs)   │
                          └────────┬────────┘
                                   │
                          ┌────────▼────────┐
                          │  human_approval │  ◄── LangGraph interrupt()
                          │     (HITL)      │      pauses here
                          └────────┬────────┘
                        Approve / Revise
                                   │
                          ┌────────▼────────┐
                          │   final_agent   │
                          │ (polished plan) │
                          └────────┬────────┘
                                   │
                                  END
```

The graph is compiled with a **PostgreSQL checkpointer**, so every run is persisted to a unique `thread_id`. The HITL pause survives server restarts — the user can approve hours later and execution resumes from the exact checkpoint.

---

## Agent Descriptions

### 1. Supervisor Agent (`supervisor_agent`)

**File:** [`backend.py`](backend.py) — `supervisor_agent()`

The entry point for every request. It does two things in sequence:

**Step 1 — Input Guardrail**

Calls the LLM to classify whether the request is travel-related. Valid topics include destinations, flights, hotels, weather, budgets, visas, transportation, sightseeing, food, packing, and itineraries.

- If blocked → sets `guardrail_allowed = False` and routes to `guardrail_blocked_agent`.
- The guardrail fails open (allows the request) if the LLM returns malformed JSON, to avoid blocking valid requests on transient errors.

**Step 2 — Agent Selection & Constraint Extraction**

Calls the LLM to:
- Select only the relevant subset of specialist agents for this particular query.
- Extract structured trip constraints: `destination`, `origin`, `duration`, `budget`, `travel_style`, `special_preferences`.

The `itinerary_agent` is always appended to `selected_agents` regardless of what the supervisor picks.

---

### 2. Flight Agent (`flight_agent`)

**File:** [`backend.py`](backend.py) — `flight_agent()`

Calls two AviationStack MCP tools via `aviation_mcp_call()`:
- `list_airports` — retrieves airport data
- `list_airlines` — retrieves airline data

Passes up to 3,000 characters of each result to the LLM with a structured prompt requesting:
1. Departure & arrival airports
2. Airlines serving the route
3. Typical flight duration
4. Estimated airfare range
5. Peak season pricing warnings
6. Booking advice

On MCP failure, stores a graceful fallback message in `flight_results`.

---

### 3. Hotel Agent (`hotel_agent`)

**File:** [`backend.py`](backend.py) — `hotel_agent()`

Calls `tavily_mcp_search()` with the query `"Best hotels for {user_query}"`.

Uses the Tavily remote MCP endpoint (`https://mcp.tavily.com/mcp/`) for live web search results.

On failure, returns a clearly labeled non-live advice message instead of crashing.

---

### 4. Weather Agent (`weather_agent`)

**File:** [`backend.py`](backend.py) — `weather_agent()`

1. Calls `extract_destination()` to parse the city name from the user's free-text query (LLM-powered).
2. Calls `weather_mcp_search(city)` → **current conditions** (temperature, humidity, wind, description).
3. Calls `forecast_mcp_search(city)` → **5-entry 3-hour forecast**.

Both calls use the custom local MCP server (`custom_weather_mcp_server.py`) launched as a stdio subprocess.

---

### 5. Budget Agent (`budget_agent`)

**File:** [`backend.py`](backend.py) — `budget_agent()`

A synthesis agent — it receives the outputs from `flight_agent`, `hotel_agent`, and `weather_agent` and produces:
1. Estimated cost categories
2. Budget risk areas
3. Money-saving suggestions
4. Overall feasibility judgment

Clearly labels all estimates as approximate when live pricing is not available.

---

### 6. Itinerary Agent (`itinerary_agent`)

**File:** [`backend.py`](backend.py) — `itinerary_agent()`

Combines all specialist outputs into a **draft day-by-day travel plan**. The draft is intentionally labeled as pending human review. On completion it sets `approval_request` to prompt the frontend to display the review UI.

---

### 7. Human Approval Agent (`human_approval_agent`)

**File:** [`backend.py`](backend.py) — `human_approval_agent()`

Calls `interrupt()` — LangGraph's built-in mechanism to **pause graph execution** and wait for external input. The interrupt payload sent to the frontend includes:

```json
{
  "question": "Do you approve this itinerary?",
  "draft_itinerary": "...",
  "approval_request": "...",
  "selected_agents": [...],
  "supervisor_reasoning": "..."
}
```

Execution resumes when `resume_travel_agent()` is called with the user's decision.

---

### 8. Final Agent (`final_agent`)

**File:** [`backend.py`](backend.py) — `final_agent()`

Generates the final polished travel response. Its behavior changes based on the human decision:

- **Approved** → preserves the draft's decisions and polishes the formatting.
- **Rejected with feedback** → applies the specific revision feedback.

Output is structured into 7 sections:
1. Trip Summary
2. Flight Information
3. Hotel Suggestions
4. Weather Information
5. Day-by-Day Itinerary
6. Estimated Budget
7. Final Recommendations

---

## Human-in-the-Loop (HITL)

### How it works

TripMate AI uses **LangGraph's `interrupt()` + PostgreSQL checkpointer** to implement a durable HITL gate.

```
POST /api/travel          ──► Graph runs until interrupt()
                               Thread saved to PostgreSQL
                               Returns { requires_approval: true, ... }

(User reviews draft itinerary)

POST /api/travel/approve  ──► Graph resumes from checkpoint
                               Final agent runs
                               Returns { final_response: "..." }
```

### Thread persistence

Each session uses a unique `thread_id` (auto-generated UUID prefix or user-supplied). The PostgreSQL `PostgresSaver` checkpointer saves the full graph state, so:
- The server can restart between the initial request and the approval.
- Multiple users can have concurrent independent sessions.

### Approval request payload

```json
{
  "thread_id": "user_abc123...",
  "approved": true,
  "feedback": ""
}
```

If `approved` is `false`, `feedback` **must** be non-empty (enforced by `app.py`).

---

## MCP Tool Integrations

**File:** [`mcp_client.py`](mcp_client.py)

The app uses **three MCP servers** managed through `langchain-mcp-adapters`:

| Server | Transport | Source | Tools |
|---|---|---|---|
| `tavily` | `streamable_http` | Remote (Tavily cloud) | `tavily_search` |
| `aviationstack` | `stdio` | `uvx aviationstack-mcp` | `list_airports`, `list_airlines` |
| `weather` | `stdio` | Local Python script | `get_current_weather`, `get_forecast` |

### Custom Weather MCP Server

**File:** [`custom_weather_mcp_server.py`](custom_weather_mcp_server.py)

A locally-hosted MCP server built with `FastMCP`. It exposes two tools:

**`get_current_weather(city: str)`**

Returns:
```json
{
  "city": "Tokyo",
  "temperature_c": 22.1,
  "feels_like_c": 21.5,
  "humidity": 60,
  "condition": "clear sky",
  "wind_speed": 3.2
}
```

**`get_forecast(city: str)`**

Returns the first 5 three-hour forecast entries:
```json
{
  "city": "Tokyo",
  "forecast": [
    { "datetime": "2025-10-22 09:00:00", "temperature_c": 21.0, "condition": "few clouds" },
    ...
  ]
}
```

The server runs as a `stdio` subprocess — launched automatically by `mcp_client.py` when needed and does **not** need to be started manually.

### Destination Extractor

`extract_destination(query)` in `mcp_client.py` uses an LLM call to parse the destination city/country from a free-text query before passing it to the weather MCP tools.

---

## State Management

**File:** [`backend.py`](backend.py) — `TravelState`

The full graph state is a `TypedDict` with these fields:

| Field | Type | Description |
|---|---|---|
| `messages` | `list[AnyMessage]` | Accumulated message history (append-only) |
| `user_query` | `str` | Original user request |
| `guardrail_allowed` | `bool` | Whether the input passed the guardrail |
| `guardrail_reason` | `str` | Reason if blocked |
| `selected_agents` | `list[str]` | Agents chosen by the supervisor |
| `trip_constraints` | `dict` | Parsed trip details (destination, origin, duration, budget, etc.) |
| `supervisor_reasoning` | `str` | Supervisor's routing rationale |
| `flight_results` | `str` | Output from flight agent |
| `hotel_results` | `str` | Output from hotel agent |
| `weather_results` | `str` | Output from weather agent |
| `budget_results` | `str` | Output from budget agent |
| `itinerary` | `str` | Draft itinerary from itinerary agent |
| `approval_request` | `str` | Message shown to human reviewer |
| `approved` | `bool` | Human approval decision |
| `human_feedback` | `str` | Revision notes from human |
| `final_response` | `str` | The final polished travel plan |
| `llm_calls` | `int` | Running count of LLM invocations |

### Dynamic Agent Routing

The supervisor's `selected_agents` list controls which specialist agents run. The routing logic in `route_after_agent()` skips any agents not selected and routes directly to the next selected one, then always ends with `itinerary_agent`.

---

## API Reference

**Base URL:** `http://127.0.0.1:8000`

### `GET /`
Serves the web frontend (`templates/index.html`).

---

### `POST /api/travel`

Start a new travel planning session.

**Request body:**
```json
{
  "message": "Plan a 5-day trip to Paris with a $2000 budget",
  "thread_id": null
}
```

**Response (mid-workflow — requires approval):**
```json
{
  "success": true,
  "thread_id": "user_abc123...",
  "requires_approval": true,
  "answer": "...(draft itinerary)...",
  "approval_request": "Please review the generated draft itinerary...",
  "flight_results": "...",
  "hotel_results": "...",
  "weather_results": "...",
  "budget_results": "...",
  "itinerary": "...",
  "selected_agents": ["flight_agent", "hotel_agent", "weather_agent", "budget_agent", "itinerary_agent"],
  "trip_constraints": { "destination": "Paris", "budget": "$2000", ... },
  "supervisor_reasoning": "...",
  "guardrail_allowed": true,
  "guardrail_reason": "",
  "llm_calls": 5
}
```

**Response (guardrail blocked):**
```json
{
  "success": true,
  "requires_approval": false,
  "guardrail_allowed": false,
  "guardrail_reason": "TripMate AI can only help with travel-planning requests...",
  "answer": "TripMate AI can only help with travel-planning requests..."
}
```

---

### `POST /api/travel/approve`

Resume a paused session after human review.

**Request body:**
```json
{
  "thread_id": "user_abc123...",
  "approved": true,
  "feedback": ""
}
```

> If `approved` is `false`, `feedback` must be non-empty.

**Response:**
```json
{
  "success": true,
  "thread_id": "user_abc123...",
  "requires_approval": false,
  "answer": "...(final polished travel plan)...",
  "final_response": "...",
  "approved": true,
  "human_feedback": ""
}
```

---

### `GET /health`

```json
{
  "status": "ok",
  "message": "TripMate AI API is running",
  "features": ["supervisor_agent", "input_guardrail", "human_in_the_loop"]
}
```

---

### cURL Examples

```bash
# Start a travel request
curl -X POST http://127.0.0.1:8000/api/travel \
  -H "Content-Type: application/json" \
  -d '{"message": "Plan a 3-day trip to Tokyo with a budget of $1200"}'

# Approve the draft itinerary
curl -X POST http://127.0.0.1:8000/api/travel/approve \
  -H "Content-Type: application/json" \
  -d '{"thread_id": "user_abc123...", "approved": true, "feedback": ""}'

# Request a revision
curl -X POST http://127.0.0.1:8000/api/travel/approve \
  -H "Content-Type: application/json" \
  -d '{"thread_id": "user_abc123...", "approved": false, "feedback": "Add more vegetarian restaurant options."}'
```

---

## Project Structure

```
multiagent_system__superivosr_agent/
├── app.py                       # FastAPI entry point — routes & request models
├── backend.py                   # LangGraph graph, all agents, state, routing
├── mcp_client.py                # MCP client — connects to 3 MCP servers
├── custom_weather_mcp_server.py # Local FastMCP weather server (stdio)
├── main.py                      # Minimal test/dev runner
├── mcp_client_test.py           # MCP connectivity smoke test
├── test.py                      # Ad-hoc test script
├── requirements.txt             # Pinned Python dependencies
├── pyproject.toml               # Project metadata
├── Dockerfile                   # Container build definition
├── .env                         # API keys (not committed to VCS)
├── static/
│   ├── script.js                # Frontend logic (approval flow, chat UI)
│   └── style.css                # Frontend styles
├── templates/
│   └── index.html               # Jinja2 HTML template
└── tools/
    ├── __init__.py
    ├── flight_tool.py           # Standalone flight tool (reference)
    └── tavily_tool.py           # Standalone Tavily tool (reference)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| API framework | FastAPI 0.136 + Uvicorn |
| Agent orchestration | LangGraph 1.2 |
| LLM integration | LangChain 1.3 + `langchain-groq` |
| LLM provider | Groq (`openai/gpt-oss-20b`, `openai/gpt-oss-120b`) |
| Tool protocol | Model Context Protocol (MCP) via `langchain-mcp-adapters` |
| State persistence | PostgreSQL via `langgraph-checkpoint-postgres` + `psycopg` v3 |
| Hotel search | Tavily API (remote MCP) |
| Flight data | AviationStack API (stdio MCP via `uvx`) |
| Weather data | OpenWeatherMap API (custom local MCP server) |
| Frontend | Jinja2 + Vanilla HTML/CSS/JS |
| SSL certificates | `certifi` |

---

## Prerequisites

- **Python 3.10+** (`.python-version` pins 3.12)
- **PostgreSQL** — running and accessible (local or cloud e.g. Render)
- **`uvx`** — required to launch `aviationstack-mcp` (`pip install uv` or `brew install uv`)
- **API keys** for: Groq, Tavily, AviationStack, OpenWeatherMap

---

## Environment Variables

Create a `.env` file in the project root:

```env
# PostgreSQL — include sslmode=require for Render/cloud instances
DATABASE_URL=postgresql://user:password@host:5432/travel_db

# LLM
GROQ_API_KEY=your_groq_api_key

# MCP tool APIs
TAVILY_API_KEY=your_tavily_api_key
AVIATIONSTACK_API_KEY=your_aviationstack_api_key   # also accepted as AVIATION_STACK_API_KEY
OPENWEATHER_API_KEY=your_openweathermap_api_key
```

---

## Installation & Running

```bash
# 1. Clone the repo
git clone <repo-url>
cd multiagent_system__superivosr_agent

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your .env file (see above)

# 5. Verify MCP connectivity (optional)
python mcp_client_test.py

# 6. Start the server
python app.py
```

Then open **http://127.0.0.1:8000** in your browser.

### Docker

```bash
docker build -t tripmate-ai .
docker run -p 8000:8000 --env-file .env tripmate-ai
```

---

## Known Issues & Limitations

### Token Limit (413 errors)

The `final_agent` prompt includes all specialist outputs concatenated together, which can exceed the Groq free-tier limit of **8,000 TPM** for `openai/gpt-oss-20b`.

**Workarounds:**
- Upgrade to a paid Groq tier with higher TPM limits.
- Truncate specialist results before passing them to `final_agent` (e.g., limit `flight_results`, `hotel_results` to ~500 chars each).
- Switch to a model with a higher context window.

### Async in Sync Context

LangGraph nodes are synchronous. The MCP client is async. `asyncio.run()` and `nest_asyncio` are used to bridge this gap. This works correctly but may behave unexpectedly if the event loop is already running in a non-standard deployment.

### AviationStack Live Prices

The AviationStack free tier does not return live ticket prices. The flight agent will note this clearly in its output and advise the user to verify pricing on a booking platform.

### Weather City Extraction

`extract_destination()` uses an LLM call to parse the city from free text. For ambiguous queries (e.g., "a beach holiday"), it may extract an incorrect or empty city name and fall back to a graceful error message.