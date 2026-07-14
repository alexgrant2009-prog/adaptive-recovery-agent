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

This is the core design + skeleton: the graph, prompts, and schemas are
complete; the Terra client, calendar clients, and approval-token service are
stubbed (`NotImplementedError`) as the next build step.

Requirements when wiring it up: `anthropic`, `langgraph`, `langgraph-checkpoint-sqlite`.
