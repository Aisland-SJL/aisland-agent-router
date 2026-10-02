#!/usr/bin/env python3
"""Read scoped Codex telemetry for explicitly recorded physical sessions."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import router


FIELDS = {"total": "total_tokens", "input": "input_tokens", "output": "output_tokens",
          "cached_input": "cached_input_tokens", "reasoning": "reasoning_output_tokens"}
SESSION_ID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")


def default_sessions_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"


def timestamp(value: Any) -> datetime | None:
    try:
        return router.parse_time(value, "telemetry timestamp")
    except router.RouterError:
        return None


def counters(value: Any) -> dict[str, int | None] | None:
    if not isinstance(value, dict):
        return None
    result = {key: value.get(raw) for key, raw in FIELDS.items()}
    if any(v is not None and (not router.is_int(v) or v < 0) for v in result.values()):
        return None
    if result["input"] is None or result["output"] is None:
        return None
    if result["total"] is None:
        result["total"] = result["input"] + result["output"]
    if result["total"] != result["input"] + result["output"]:
        return None
    if any(result[k] is not None and result[k] > result[parent]
           for k, parent in (("cached_input", "input"), ("reasoning", "output"))):
        return None
    return result


def add(target: dict[str, int | None], values: dict[str, int | None]) -> None:
    for key in FIELDS:
        target[key] = (target[key] + values[key]
                       if target[key] is not None and values[key] is not None else None)


def read_trace(path: Path, session_id: str, start: datetime, finish: datetime,
               turn_id: str | None = None) -> dict[str, Any]:
    previous = None
    latest = None
    context = (None, None, None)
    segments: dict[tuple, dict] = {}
    warnings: set[str] = set()
    observed = {key: 0 for key in FIELDS}
    accepted = duplicates = 0
    meta_time = None
    verified = False
    terminal = False
    last_count_at = None
    last_event_at = None
    current_turn = None
    turn_closed_at = None
    target_established_by_cutoff = False
    with path.open("r", encoding="utf-8-sig") as stream:
        # Verify the named trace before processing any telemetry or message items.
        first = stream.readline()
        try:
            meta = json.loads(first)
        except (ValueError, UnicodeError):
            raise router.RouterError("Trace has no valid first session_meta") from None
        payload = meta.get("payload") if isinstance(meta, dict) else None
        if not isinstance(payload, dict) or meta.get("type") != "session_meta" or payload.get("id") != session_id:
            raise router.RouterError("Trace session_meta does not match the recorded session ID")
        verified = True
        meta_time = timestamp(meta.get("timestamp") or payload.get("timestamp"))
        for line in stream:
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                warnings.add("malformed_trace_line")
                continue
            if not isinstance(event, dict):
                warnings.add("invalid_event_shape")
                continue
            kind = event.get("type")
            payload = event.get("payload")
            if not isinstance(payload, dict) or kind not in {"turn_context", "event_msg"}:
                continue
            at = timestamp(event.get("timestamp"))
            if at is None:
                warnings.add("missing_telemetry_timestamp")
                continue
            if kind == "turn_context" or (kind == "event_msg" and payload.get("type") == "task_started"):
                current_turn = payload.get("turn_id")
                if turn_id is not None and current_turn == turn_id and at <= finish:
                    target_established_by_cutoff = True
            belongs = turn_id is None or current_turn == turn_id
            if at > finish and not (target_established_by_cutoff and belongs and turn_closed_at is None):
                continue
            if last_event_at is not None and at < last_event_at:
                warnings.add("nonmonotonic_event_time")
            last_event_at = at
            if kind == "turn_context":
                context = tuple(payload.get(k) if isinstance(payload.get(k), str) else None
                                for k in ("model", "effort", "service_tier"))
                continue
            if payload.get("type") == "task_started" and at >= start and belongs:
                terminal = False
            elif payload.get("type") in {"task_complete", "turn_completed"} and at >= start and belongs:
                if turn_id is not None and payload.get("turn_id") not in {None, turn_id}:
                    continue
                terminal = True
                turn_closed_at = at.isoformat()
            if payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            total = counters(info.get("total_token_usage"))
            last = counters(info.get("last_token_usage"))
            delta = None
            attribution = context
            if total is not None:
                latest = total
                if previous is not None:
                    if any(total[k] < previous[k] for k in ("total", "input", "output")):
                        if at >= start:
                            warnings.add("cumulative_counter_reset")
                        delta = last
                    else:
                        delta = {k: total[k] - previous[k] if total[k] is not None and previous[k] is not None else None
                                 for k in FIELDS}
                        if any(v is not None and v < 0 for v in delta.values()):
                            warnings.add("subset_counter_reset")
                            delta = last
                        elif delta["total"] == 0:
                            if at >= start:
                                duplicates += 1
                            previous = total
                            continue
                        elif at >= start and last is not None and any(delta[k] != last[k] for k in ("total", "input", "output")):
                            warnings.add("missing_usage_events_or_boundary")
                            attribution = (None, None, None)
                elif meta_time is not None and meta_time >= start and last is not None and total == last:
                    delta = total
                else:
                    delta = last
                    if at >= start:
                        warnings.add("missing_start_baseline")
                previous = total
            else:
                previous = None
                delta = last
                if at >= start:
                    warnings.add("no_cumulative_evidence")
            if at < start or delta is None or not belongs:
                continue
            for key, parent in (("cached_input", "input"), ("reasoning", "output")):
                if delta[key] is not None and delta[parent] is not None and delta[key] > delta[parent]:
                    warnings.add("invalid_subset_delta")
                    delta[key] = None
            segment = segments.setdefault(attribution, {"model": attribution[0], "effort": attribution[1],
                                                       "service_tier": attribution[2],
                                                       "tokens": {k: 0 for k in FIELDS}, "events": 0})
            add(segment["tokens"], delta)
            add(observed, delta)
            segment["events"] += 1
            accepted += 1
            last_count_at = at.isoformat()
    if not accepted:
        observed = latest if latest is not None else {key: None for key in FIELDS}
        scope = "session_total" if latest is not None else "unknown"
        warnings.add("no_task_usage_observed")
    else:
        scope = "task_delta"
    if not terminal:
        warnings.add("snapshot_before_task_completion")
    return {"session_id": session_id, "trace": str(path), "verified_session": verified,
            "scope": scope, "tokens": observed, "complete": accepted > 0 and terminal and not warnings,
            "segments": list(segments.values()), "events": accepted, "duplicates_skipped": duplicates,
            "last_count_at": last_count_at, "turn_id": turn_id, "turn_closed_at": turn_closed_at,
            "warnings": sorted(warnings)}


def collect_usage_for_run(run: dict[str, Any], sessions_dir: Path) -> dict[str, Any]:
    router.validate_run(run)
    start = router.parse_time(run["started_at"], "started_at")
    sampled = datetime.now(timezone.utc)
    finish = router.parse_time(run.get("finished_at"), "finished_at", nullable=True) or sampled
    root = sessions_dir.resolve()
    rows = []
    seen_ids = set()
    for participant in run["participants"]:
        session_id = participant.get("agent_id") or participant["id"]
        missing = {"participant_id": participant["id"], "session_id": session_id,
                   "scope": "unknown", "tokens": {key: None for key in FIELDS},
                   "complete": False, "segments": [], "warnings": []}
        if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
            missing["warnings"] = ["recorded_session_id_is_not_a_uuid"]
            rows.append(missing)
            continue
        if session_id in seen_ids:
            raise router.RouterError("Run refers to the same physical trace more than once")
        seen_ids.add(session_id)
        candidates = [p for p in root.rglob(f"*{session_id}.jsonl")
                      if p.is_file() and p.resolve().is_relative_to(root)] if root.is_dir() else []
        if len(candidates) != 1:
            missing["warnings"] = ["missing_trace" if not candidates else "ambiguous_duplicate_traces"]
            rows.append(missing)
            continue
        try:
            target_turn = participant.get("usage_turn_id")
            if target_turn is not None and (not isinstance(target_turn, str) or not SESSION_ID.fullmatch(target_turn)):
                raise router.RouterError("Invalid recorded usage turn ID")
            row = read_trace(candidates[0], session_id, start, finish, target_turn)
        except (router.RouterError, OSError, UnicodeError):
            missing["warnings"] = ["trace_unreadable_or_identity_mismatch"]
            rows.append(missing)
            continue
        row["participant_id"] = participant["id"]
        if run["status"] == "running":
            row["complete"] = False
        rows.append(row)
    complete = bool(rows) and all(row["complete"] and row["scope"] == "task_delta" for row in rows)
    return {"schema_version": 1, "run_id": run["run_id"], "sampled_at": sampled.isoformat(),
            "window": {"started_at": start.isoformat(), "finished_at": finish.isoformat()},
            "sessions": rows, "complete": complete,
            "task_total": sum(row["tokens"]["total"] for row in rows) if complete else None,
            "model_source": "turn_context telemetry; not proof of the served backend model",
            "billing": "not estimated"}


def apply_usage_to_run(run: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    router.require(report.get("run_id") == run["run_id"], "Usage report belongs to another run")
    result = copy.deepcopy(run)
    rows = {row["participant_id"]: row for row in report["sessions"]}
    for participant in result["participants"]:
        row = rows.get(participant["id"])
        if row is None:
            continue
        participant["token_usage"] = {**row["tokens"], "scope": row["scope"],
                                      "source": "Codex token_count snapshot " + report["sampled_at"],
                                      "complete": row["complete"]}
        participant["usage_segments"] = row["segments"]
        participant["usage_warnings"] = row["warnings"]
        # Logged context is an observation, not a backend attestation.
        participant["observed_models"] = sorted({seg["model"] for seg in row["segments"] if seg["model"]})
    result["snapshot_at"] = report["sampled_at"]
    router.validate_run(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", required=True, type=Path)
    parser.add_argument("--sessions-dir", type=Path, default=default_sessions_dir())
    parser.add_argument("--update", action="store_true", help="Update only this named run record")
    args = parser.parse_args(argv)
    try:
        run = router.read_json(args.record)
        report = collect_usage_for_run(run, args.sessions_dir)
        if args.update:
            router.atomic_json_write(args.record, apply_usage_to_run(run, report))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except router.RouterError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
