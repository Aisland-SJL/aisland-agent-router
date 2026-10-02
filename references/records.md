# Local Configuration and Run Records

`router.py` manages configuration, validates records and renders vertical text. `usage.py` optionally reads Codex telemetry for the exact physical sessions listed in one run. Both use the Python standard library. They do not launch agents or contact a network service.

## Configuration

Read `.local/config.json` at the installed entry when present, otherwise `assets/default-config.json`. `config show` is read-only and displays four responsibilities, main-led decision mode, the expert fallback, requested Explorer speed, switches, revision and UTC read time. The agent asks whether the user wants changes.

`config set` validates and atomically saves one new revision. Existing model choices and omitted fields remain unchanged. New optional settings are `decision_mode: main|dedicated` and `review_policy: risk_based|always`; legacy configurations without them retain dedicated/always behavior. Role settings may contain `service_tier: default|fast` and `agent_type`. A requested speed does not prove a served speed. Changes to Explorer model/effort must also reconcile its fixed role template and personal entry.

```text
python -B scripts/router.py config show --json
python -B scripts/router.py config set --setting decision_mode --value main
python -B scripts/router.py config set --setting review_policy --value risk_based
python -B scripts/router.py config set --role executor --effort xhigh
```

Local model authorization is separate from preference values. Copy updates preserve the installed `.local/` directory; packaged defaults never overwrite it.

## Run Record

Use one JSON per actual run, maintained by the main conversation. `config_snapshot` preserves the startup configuration; each participant is a unique physical session. Exactly one participant includes `coordinator`; combine roles when the main conversation also executes or reviews. IDs and non-null agent IDs are unique. Prefer the confirmed Codex thread UUID for the coordinator ID so telemetry can be located. Do not replace identity with a role nickname.

All schema-1 root and participant fields in the template remain required. Null is unknown, not zero. Metrics require a count, unit and evidence; different units cannot be added. Requested values, logged context and backend-confirmed actual values are distinct.

- Root status: `running`, `completed`, `failed`, `cancelled`, `paused`, `partial`.
- Participant status: `working`, `awaiting_review`, `accepted`, `blocked`, `cancelled`, `failed`, `paused`.
- Trigger: `explicit`, `passive_execution`, `passive_reasoning`, with a concrete reason.
- Verification: `not_run`, `partial`, `failed`, `passed`; passed requires a nonblank summary and evidence.
- Times: timezone-aware ISO8601; finish cannot precede start. Elapsed task time does not sum parallel sessions.
- Token fields: nullable nonnegative integers `total`, `input`, `output`, `cached_input`, `reasoning`; scope `unknown`, `task_delta` or `session_total`; source required when total is known; complete is boolean.
- Optional participant fields: `findings` (nonblank strings), requested/actual service tier, `usage_turn_id` (confirmed turn UUID for completion follow-up). Collector metadata `observed_models`, `usage_segments`, `usage_warnings` stays in detailed JSON.

Risk-based records include `review_decision: {required: bool, reason: nonblank string}`. Completed runs require a finish, passed verification, an accepted coordinator with actual work and evidence, and no active or blocked participants. Required or always-policy review also needs an accepted Reviewer with work and evidence. Legacy snapshots without review_policy retain the former mandatory Reviewer rule. A justified optional review does not remove main-conversation acceptance checks.

## Scoped Telemetry

```text
python -B scripts/usage.py --record path/to/run.json
python -B scripts/usage.py --record path/to/run.json --update
python -B scripts/usage.py --record path/to/run.json --sessions-dir path/to/synthetic-sessions
```

Default root is `$CODEX_HOME/sessions` or `~/.codex/sessions`. Only filenames matching recorded UUIDs are opened, and the first session_meta must match before telemetry is processed. Archives and unknown descendants are not automatically included. Duplicate candidate files are reported as ambiguous instead of counted twice. The caller may select a different sessions directory explicitly.

Window is run `started_at` through `finished_at`, or the sampling instant while running. Cumulative token_count differences exclude earlier history; unchanged counters are duplicate snapshots even across model-context changes. Reset counters, missing baselines, malformed lines, unexplained gaps and unclosed tasks downgrade completeness. When no window usage is observed, a retained cumulative counter is explicitly `session_total` and excluded from task totals/shares. Missing counts remain null.

For a confirmed `usage_turn_id` established at or before the delivery cutoff, later collection can include that exact turn's final usage until its matching task_complete. A turn first seen after the cutoff and next-turn contexts are excluded. Without that recorded identity, retain the conservative time boundary and report incomplete data; do not guess which later events belong to the task.

Segments group by logged model, effort and speed. turn_context reports configuration observations, not an attestation of the served backend; collector never fills actual_model from it. Message content, credentials and rate-limit payloads are not exported. Output is a statistical snapshot, not a bill. This release deliberately has no price estimate or savings claim.

`--update` replaces only the named run with validated statistical fields; it does not copy source logs. A live main conversation commonly has not written its final token event yet, so report observed values and incomplete task accounting. Full task totals and token shares require every physical session to have sourced, complete task_delta values; cache and reasoning are subsets. A zero total has no computable shares.

## Recap

`record show` produces numbered vertical text, with role, model/effort, contribution, status, available time/token fields and optional review findings. Common missing fields are stated once; full IDs, trace paths and configuration details stay in JSON. The agent presents this as one writing block or one plain code frame, without table alignment or separate dividers.

```text
python -B scripts/router.py record validate --file path/to/run.json
python -B scripts/router.py record show --file path/to/run.json
python -B scripts/router.py record show --file path/to/run.json --json
python -B scripts/router.py record summary --files path/to/run-a.json path/to/run-b.json
```

Summary reads only named records. It does not combine partial observations into a task total, unlike work units into a workload total, or token proportions into cost proportions.

## Complete Record Template

This is an initializing example, not observed execution. Replace identity, task, timestamps and review reason; snapshot the actual local config. A completed record needs real participant work and acceptance evidence.

```json
{
  "schema_version": 1,
  "run_id": "replace-with-unique-run-id",
  "task": "Replace with the actual task",
  "trigger": {
    "mode": "explicit",
    "reason": "Replace with the observed trigger reason"
  },
  "config_snapshot": {
    "schema_version": 1,
    "preset": "main-led-v2",
    "revision": 1,
    "enabled": true,
    "passive_execution": true,
    "passive_reasoning": true,
    "sensitivity": "standard",
    "decision_mode": "main",
    "review_policy": "risk_based",
    "max_concurrent": 2,
    "max_attempts": 3,
    "roles": {
      "brain": {
        "label": "决策者",
        "model": "gpt-6-astra",
        "effort": "high"
      },
      "explorer": {
        "label": "探索者",
        "model": "gpt-6-luna",
        "effort": "medium",
        "service_tier": "fast",
        "agent_type": "aisland_explorer"
      },
      "executor": {
        "label": "执行者",
        "model": "gpt-6-luna",
        "effort": "xhigh"
      },
      "reviewer": {
        "label": "检查者",
        "model": "gpt-6-sol",
        "effort": "high"
      }
    }
  },
  "status": "running",
  "started_at": "2026-10-02T10:00:00+00:00",
  "finished_at": null,
  "review_decision": {
    "required": true,
    "reason": "Replace with the actual review requirement"
  },
  "participants": [
    {
      "id": "replace-with-physical-session-uuid",
      "agent_id": null,
      "roles": [
        "coordinator"
      ],
      "requested_model": null,
      "requested_effort": null,
      "actual_model": null,
      "actual_effort": null,
      "status": "working",
      "work": "",
      "metrics": [],
      "elapsed_seconds": null,
      "attempts": 1,
      "consultations": 0,
      "token_usage": {
        "total": null,
        "input": null,
        "output": null,
        "cached_input": null,
        "reasoning": null,
        "scope": "unknown",
        "source": null,
        "complete": false
      },
      "findings": [],
      "evidence": []
    }
  ],
  "verification": {
    "status": "not_run",
    "summary": "",
    "evidence": []
  },
  "retries": 0,
  "notes": []
}
```
