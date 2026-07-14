# Adaptive Recovery Agent — Core Architecture

An autonomous agent for students with high-stress academic schedules. It
perceives recovery state (HRV, sleep, resting HR via the Terra API) and
academic load (Google/Outlook calendar), reasons with an LLM about whether the
planned training is appropriate, proposes adjustments, and — only after an
explicit user **Approve** — updates the calendar.

## Repository layout

| Path | What it is |
|---|---|
| `agent/prompts/system_prompt.md` | The core system prompt: priority order (health > academics > recovery-appropriate training > progression), reasoning rules, and hard behavioral boundaries. |
| `agent/tools/tool_schemas.py` | Anthropic tool definitions (strict JSON Schema) for `get_health_data`, `get_calendar_events`, `reschedule_workout`, with example results. |
| `agent/graph.py` | The LangGraph state machine: `perceive → reason → propose → [interrupt] → apply`, with a SQLite checkpointer so the plan survives session end. |
| `docs/SECURITY.md` | Threat model and mitigations for holding health data + calendar write access. |

## How the pieces fit

```
        Terra webhook / morning cron
                   │
                   ▼
             ┌──────────┐   deterministic fetches (no LLM)
             │ perceive │   get_health_data + get_calendar_events
             └────┬─────┘
                  ▼
             ┌──────────┐   Claude (claude-opus-4-8, adaptive thinking)
             │  reason  │   read-only tools + structured assessment output
             └────┬─────┘
        verdict=keep │ verdict=change
            END ◄────┴────► ┌─────────┐
                            │ propose │──── interrupt(): checkpoint & wait
                            └────┬────┘     for the user (hours or days)
                     rejected    │    approved (app mints approval_token)
              (one revision) ◄───┴───► ┌───────┐
                                       │ apply │── validated calendar write
                                       └───────┘
```

Three design decisions carry most of the weight:

1. **The plan is durable state, not conversation memory.** LangGraph's
   checkpointer persists the whole graph state per `thread_id` (= user id).
   `interrupt()` parks a cycle at the approval gate indefinitely — across
   process restarts — until the app resumes it with the user's decision.

2. **Human-in-the-loop is enforced in code, twice.** The write tool isn't even
   offered to the model during reasoning, and the executor demands a
   single-use `approval_token` bound to the approved proposal's hash. The
   "never change the calendar without approval" rule would hold even if the
   model ignored its prompt entirely.

3. **The LLM is a proposer, not a principal.** Credentials, ownership checks,
   rate limits, and audit logging all live in the deterministic executor
   layer. Calendar text is treated as untrusted input (prompt-injection
   surface). See `docs/SECURITY.md`.

## Implementation status

The graph, prompts, schemas, and all three integration layers are implemented:

| Path | What it is |
|---|---|
| `agent/integrations/terra_client.py` | Terra REST client for `get_health_data`: daily HRV / sleep / resting-HR with 7-day baselines, sanity-band flagging of suspect readings, staleness detection. Env: `TERRA_API_KEY`, `TERRA_DEV_ID`. |
| `agent/integrations/calendar_client.py` | Google Calendar client (`calendar.events` scope only): event fetch + workout/academic classification, and the validated write path — agent-managed events only, health-data screen on outgoing text. Env: `GOOGLE_TOKEN_DIR` (per-user authorized-user JSON files). |
| `agent/services/token_service.py` | Single-use approval tokens: HMAC-signed, bound to user + proposal content hash, 15-minute TTL, burned in SQLite on use. Env: `APPROVAL_TOKEN_SECRET`. |
| `tests/` | 31 tests: unit tests per module with mocked API responses, plus end-to-end graph flow tests (pause at approval gate, approve-and-apply, reject, forged-token block). |

```bash
pip install -r requirements.txt
python -m pytest tests/
```

Remaining before production: the OAuth consent flow that produces the per-user
Google token files, Terra user onboarding (widget session → user_id mapping),
and the app surface that renders proposals and mints tokens on Approve.
