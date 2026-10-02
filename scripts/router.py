#!/usr/bin/env python3
"""Deterministic local configuration and run-record helper for Aisland Agent Router."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


ROLES = ("brain", "explorer", "executor", "reviewer")
ROLE_LABELS = {"brain": "决策者", "explorer": "探索者", "executor": "执行者", "reviewer": "检查者"}
EFFORTS = {"low", "medium", "high", "xhigh", "max", "ultra"}
CONFIG_SERVICE_TIERS = {"default", "fast"}
PARTICIPANT_SERVICE_TIERS = {"default", "fast", "standard", "priority"}
DECISION_MODES = {"main", "dedicated"}
REVIEW_POLICIES = {"risk_based", "always"}
SENSITIVITIES = {"conservative", "standard", "active"}
RUN_STATUSES = {"running", "completed", "failed", "cancelled", "paused", "partial"}
PARTICIPANT_STATUSES = {"working", "awaiting_review", "accepted", "blocked", "cancelled", "failed", "paused"}
TRIGGER_MODES = {"explicit", "passive_execution", "passive_reasoning"}
VERIFY_STATUSES = {"not_run", "partial", "failed", "passed"}
TOKEN_FIELDS = ("total", "input", "output", "cached_input", "reasoning")


class RouterError(Exception):
    """A safe, user-facing validation or I/O error."""


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_config_path() -> Path:
    local = skill_root() / ".local" / "config.json"
    return local if local.exists() else skill_root() / "assets" / "default-config.json"


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            return json.load(stream)
    except FileNotFoundError:
        raise RouterError(f"File not found: {path}") from None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RouterError(f"Cannot read valid UTF-8 JSON from {path}: {exc}") from None


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RouterError(message)


def valid_model(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= 128 and not any(unicodedata.category(ch).startswith("C") for ch in value)


def valid_agent_type(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= 128 and not any(
        unicodedata.category(ch).startswith("C") for ch in value
    )


def validate_config(config: Any) -> None:
    require(isinstance(config, dict), "config must be an object")
    require(config.get("schema_version") == 1 and not isinstance(config.get("schema_version"), bool), "config schema_version must be 1")
    require(isinstance(config.get("preset"), str) and bool(config["preset"].strip()), "config preset must be nonempty")
    require(is_int(config.get("revision")) and config["revision"] >= 1, "config revision must be a positive integer")
    for key in ("enabled", "passive_execution", "passive_reasoning"):
        require(isinstance(config.get(key), bool), f"config {key} must be boolean")
    sensitivity = config.get("sensitivity")
    require(isinstance(sensitivity, str) and sensitivity in SENSITIVITIES, "config sensitivity is invalid")
    if "decision_mode" in config:
        require(isinstance(config["decision_mode"], str) and config["decision_mode"] in DECISION_MODES,
                "config decision_mode is invalid")
    if "review_policy" in config:
        require(isinstance(config["review_policy"], str) and config["review_policy"] in REVIEW_POLICIES,
                "config review_policy is invalid")
    require(is_int(config.get("max_concurrent")) and 1 <= config["max_concurrent"] <= 4, "config max_concurrent must be an integer from 1 to 4")
    require(is_int(config.get("max_attempts")) and 1 <= config["max_attempts"] <= 3, "config max_attempts must be an integer from 1 to 3")
    roles = config.get("roles")
    require(isinstance(roles, dict) and set(roles) == set(ROLES), "config roles must contain exactly brain, explorer, executor, reviewer")
    for role in ROLES:
        entry = roles[role]
        require(isinstance(entry, dict), f"config role {role} must be an object")
        require(isinstance(entry.get("label"), str) and bool(entry["label"].strip()), f"config role {role} label must be nonempty")
        require(valid_model(entry.get("model")), f"config role {role} model is invalid")
        effort = entry.get("effort")
        require(isinstance(effort, str) and effort in EFFORTS, f"config role {role} effort is invalid")
        if "service_tier" in entry:
            require(isinstance(entry["service_tier"], str) and entry["service_tier"] in CONFIG_SERVICE_TIERS,
                    f"config role {role} service_tier is invalid")
        if "agent_type" in entry:
            require(valid_agent_type(entry["agent_type"]), f"config role {role} agent_type is invalid")


def parse_time(value: Any, field: str, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    require(isinstance(value, str) and bool(value.strip()), f"{field} must be an ISO8601 timestamp" + (" or null" if nullable else ""))
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise RouterError(f"{field} must be an ISO8601 timestamp" + (" or null" if nullable else "")) from None
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None, f"{field} must include a timezone")
    return parsed


def validate_string_list(value: Any, field: str, allow_empty: bool = True) -> None:
    require(isinstance(value, list) and (allow_empty or bool(value)), f"{field} must be a {'' if allow_empty else 'nonempty '}list")
    require(all(isinstance(item, str) and bool(item.strip()) for item in value), f"{field} entries must be nonempty strings")


def validate_run(run: Any) -> None:
    require(isinstance(run, dict), "run must be an object")
    require(run.get("schema_version") == 1 and not isinstance(run.get("schema_version"), bool), "run schema_version must be 1")
    for key in ("run_id", "task"):
        require(isinstance(run.get(key), str) and bool(run[key].strip()), f"{key} must be nonempty")
    trigger = run.get("trigger")
    require(isinstance(trigger, dict) and isinstance(trigger.get("mode"), str) and trigger["mode"] in TRIGGER_MODES, "trigger mode is invalid")
    require(isinstance(trigger.get("reason"), str) and bool(trigger["reason"].strip()), "trigger reason must be nonempty")
    validate_config(run.get("config_snapshot"))
    require(isinstance(run.get("status"), str) and run["status"] in RUN_STATUSES, "run status is invalid")
    started = parse_time(run.get("started_at"), "started_at")
    finished = parse_time(run.get("finished_at"), "finished_at", nullable=True)
    if finished is not None:
        require(finished >= started, "finished_at must not precede started_at")
    participants = run.get("participants")
    require(isinstance(participants, list), "participants must be a list")
    ids: set[str] = set()
    agent_ids: set[str] = set()
    for index, participant in enumerate(participants):
        prefix = f"participants[{index}]"
        require(isinstance(participant, dict), f"{prefix} must be an object")
        pid = participant.get("id")
        require(isinstance(pid, str) and bool(pid.strip()), f"{prefix}.id must be nonempty")
        require(pid not in ids, "participant physical session ids must be unique")
        ids.add(pid)
        aid = participant.get("agent_id")
        require(aid is None or (isinstance(aid, str) and bool(aid.strip())), f"{prefix}.agent_id must be nonempty or null")
        if aid is not None:
            require(aid not in agent_ids, "non-null participant agent_id values must be unique")
            agent_ids.add(aid)
        roles = participant.get("roles")
        require(isinstance(roles, list) and bool(roles), f"{prefix}.roles must be a nonempty list")
        require(all(isinstance(role, str) and role in (*ROLES, "coordinator") for role in roles), f"{prefix}.roles contains an unknown role")
        require(len(set(roles)) == len(roles), f"{prefix}.roles must not contain duplicates")
        for key in ("requested_model", "actual_model"):
            value = participant.get(key)
            require(value is None or valid_model(value), f"{prefix}.{key} must be a bounded model identifier or null")
        for key in ("requested_effort", "actual_effort"):
            value = participant.get(key)
            require(value is None or (isinstance(value, str) and value in EFFORTS), f"{prefix}.{key} is invalid")
        for key in ("requested_service_tier", "actual_service_tier"):
            value = participant.get(key)
            require(value is None or (isinstance(value, str) and value in PARTICIPANT_SERVICE_TIERS),
                    f"{prefix}.{key} is invalid")
        status = participant.get("status")
        require(isinstance(status, str) and status in PARTICIPANT_STATUSES, f"{prefix}.status is invalid")
        require(isinstance(participant.get("work"), str), f"{prefix}.work must be a string")
        metrics = participant.get("metrics")
        require(isinstance(metrics, list), f"{prefix}.metrics must be a list")
        for mi, metric in enumerate(metrics):
            mp = f"{prefix}.metrics[{mi}]"
            require(isinstance(metric, dict), f"{mp} must be an object")
            require(isinstance(metric.get("name"), str) and bool(metric["name"].strip()), f"{mp}.name must be nonempty")
            require(is_number(metric.get("count")) and metric["count"] >= 0, f"{mp}.count must be a nonnegative number")
            require(isinstance(metric.get("unit"), str) and bool(metric["unit"].strip()), f"{mp}.unit must be nonempty")
            require(isinstance(metric.get("evidence"), str) and bool(metric["evidence"].strip()), f"{mp}.evidence must be nonempty")
        elapsed = participant.get("elapsed_seconds")
        require(elapsed is None or (is_number(elapsed) and elapsed >= 0), f"{prefix}.elapsed_seconds must be nonnegative or null")
        for key in ("attempts", "consultations"):
            value = participant.get(key)
            minimum = 1 if key == "attempts" else 0
            require(is_int(value) and value >= minimum, f"{prefix}.{key} must be an integer >= {minimum}")
        usage = participant.get("token_usage")
        require(isinstance(usage, dict), f"{prefix}.token_usage must be an object")
        for key in TOKEN_FIELDS:
            value = usage.get(key)
            require(value is None or (is_int(value) and value >= 0), f"{prefix}.token_usage.{key} must be a nonnegative integer or null")
        if usage.get("cached_input") is not None and usage.get("input") is not None:
            require(usage["cached_input"] <= usage["input"],
                    f"{prefix}.token_usage.cached_input cannot exceed input")
        if usage.get("reasoning") is not None and usage.get("output") is not None:
            require(usage["reasoning"] <= usage["output"],
                    f"{prefix}.token_usage.reasoning cannot exceed output")
        scope = usage.get("scope")
        require(isinstance(scope, str) and scope in {"unknown", "task_delta", "session_total"},
                f"{prefix}.token_usage.scope is invalid")
        source = usage.get("source")
        require(source is None or (isinstance(source, str) and bool(source.strip())), f"{prefix}.token_usage.source must be nonempty or null")
        require(isinstance(usage.get("complete"), bool), f"{prefix}.token_usage.complete must be boolean")
        if usage.get("total") is not None:
            require(source is not None, f"{prefix}.token_usage.total requires a source")
        validate_string_list(participant.get("evidence"), f"{prefix}.evidence")
        if "findings" in participant:
            validate_string_list(participant["findings"], f"{prefix}.findings")
        if "observed_models" in participant:
            validate_string_list(participant["observed_models"], f"{prefix}.observed_models")
            require(all(valid_model(model) for model in participant["observed_models"]),
                    f"{prefix}.observed_models entries must be model identifiers")
    coordinators = [p for p in participants if "coordinator" in p["roles"]]
    if participants:
        require(len(coordinators) == 1, "populated runs require exactly one coordinator physical session")
    else:
        require(run["status"] == "running", "empty participants are allowed only for an initializing running run")
    verification = run.get("verification")
    require(isinstance(verification, dict) and isinstance(verification.get("status"), str)
            and verification["status"] in VERIFY_STATUSES, "verification status is invalid")
    require(isinstance(verification.get("summary"), str), "verification summary must be a string")
    validate_string_list(verification.get("evidence"), "verification.evidence")
    if verification["status"] == "passed":
        require(bool(verification["summary"].strip()), "passed verification requires a nonempty summary")
        require(bool(verification["evidence"]), "passed verification requires evidence")
    require(is_int(run.get("retries")) and run["retries"] >= 0, "retries must be a nonnegative integer")
    validate_string_list(run.get("notes"), "notes")
    if run.get("snapshot_at") is not None:
        parse_time(run["snapshot_at"], "snapshot_at")
    review_policy = run["config_snapshot"].get("review_policy")
    if "review_decision" in run:
        decision = run["review_decision"]
        require(isinstance(decision, dict), "review_decision must be an object")
        require(isinstance(decision.get("required"), bool), "review_decision.required must be boolean")
        require(isinstance(decision.get("reason"), str) and bool(decision["reason"].strip()),
                "review_decision.reason must be nonempty")
    elif review_policy == "risk_based":
        raise RouterError("risk_based records require review_decision")
    if run["status"] == "completed":
        require(finished is not None, "completed run requires finished_at")
        require(verification.get("status") == "passed", "completed run requires verification status passed")
        coordinators = [p for p in participants if "coordinator" in p["roles"] and p["status"] == "accepted"]
        require(bool(coordinators), "completed run requires an accepted coordinator")
        require(all(p["work"].strip() and p["evidence"] for p in coordinators),
                "completed run requires coordinator work and evidence")
        reviewer_required = (
            review_policy in {None, "always"}
            or ("review_decision" in run and run["review_decision"]["required"])
        )
        if reviewer_required:
            reviewers = [p for p in participants if "reviewer" in p["roles"] and p["status"] == "accepted"]
            require(bool(reviewers), "completed run requires accepted Reviewer participation")
            require(all(p["work"].strip() and p["evidence"] for p in reviewers),
                    "completed run requires Reviewer work and evidence")
        active = {"working", "awaiting_review", "blocked", "paused"}
        require(not any(p["status"] in active for p in participants),
                "completed run cannot contain working, awaiting_review, blocked, or paused participants")


def display_config(config: dict[str, Any], as_json: bool = False) -> str:
    if as_json:
        return json.dumps(config, ensure_ascii=False, indent=2)
    lines = [f"Aisland Agent Router 配置 revision {config['revision']}（{safe_text(config['preset'])}）"]
    if config.get("decision_mode") == "main":
        lines.append("决策：主会话沿用当前对话的模型与推理强度；Astra 配置仅作按需专家支持。")
    elif config.get("decision_mode") == "dedicated":
        lines.append("决策：使用单独配置的决策角色。")
    if "review_policy" in config:
        policy = "按风险决定" if config["review_policy"] == "risk_based" else "始终复核"
        lines.append(f"复核策略：{policy}")
    for index, role in enumerate(ROLES, 1):
        entry = config["roles"][role]
        if role == "brain" and config.get("decision_mode") == "main":
            lines.append(f"{index}. 决策者（主会话）")
            lines.append("   模型与推理强度：继承当前对话。")
            lines.append(
                f"   按需专家（Astra）：{safe_text(entry['model'])}；推理强度：{entry['effort']}。"
            )
        else:
            label = "决策者" if role == "brain" else entry["label"]
            lines.append(f"{index}. {safe_text(label)}（{role}）")
            lines.append(f"   配置模型：{safe_text(entry['model'])}；推理强度：{entry['effort']}")
        if "service_tier" in entry:
            tier = "Fast" if entry["service_tier"] == "fast" else "默认"
            status = "；实际服务未验证" if entry["service_tier"] == "fast" else ""
            lines.append(f"   请求速度档：{tier}{status}")
        if "agent_type" in entry:
            lines.append(f"   具名角色：{safe_text(entry['agent_type'])}")
    lines.extend((
        f"触发与运行: enabled={on_off(config['enabled'])}, passive_execution={on_off(config['passive_execution'])}, passive_reasoning={on_off(config['passive_reasoning'])}",
        f"sensitivity={config['sensitivity']}, max_concurrent={config['max_concurrent']}, max_attempts={config['max_attempts']}",
        f"配置读取时间（UTC）: {datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')}",
        "模型、强度和触发设置都可以调整。需要修改哪个角色的模型、推理强度或设置吗？",
    ))
    return "\n".join(lines)


def on_off(value: bool) -> str:
    return "on" if value else "off"


def config_show(path: Path, as_json: bool) -> str:
    config = read_json(path)
    validate_config(config)
    return display_config(config, as_json)


def parse_setting(key: str, raw: str) -> Any:
    require(isinstance(key, str) and isinstance(raw, str), "setting key and value must be strings")
    if key in {"enabled", "passive_execution", "passive_reasoning"}:
        lowered = raw.strip().lower()
        require(lowered in {"true", "false"}, f"{key} value must be true or false")
        return lowered == "true"
    if key in {"max_concurrent", "max_attempts"}:
        require(re.fullmatch(r"[0-9]+", raw.strip()) is not None, f"{key} value must be an integer")
        return int(raw.strip())
    if key == "sensitivity":
        require(raw in SENSITIVITIES, "sensitivity value must be conservative, standard, or active")
        return raw
    if key == "decision_mode":
        require(raw in DECISION_MODES, "decision_mode value must be main or dedicated")
        return raw
    if key == "review_policy":
        require(raw in REVIEW_POLICIES, "review_policy value must be risk_based or always")
        return raw
    raise RouterError("setting key is invalid")


def atomic_json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temp_name = stream.name
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except OSError as exc:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
        raise RouterError(f"Cannot atomically save config to {path}: {exc}") from None


def config_set(path: Path, role: str | None, model: str | None, effort: str | None,
               setting: str | None, value: str | None, service_tier: str | None = None,
               agent_type: str | None = None) -> str:
    if path.exists():
        config = read_json(path)
    else:
        config = read_json(skill_root() / "assets" / "default-config.json")
    validate_config(config)
    if role is not None:
        require(isinstance(role, str) and role in ROLES, "role must be brain, explorer, executor, or reviewer")
        require(setting is None and value is None, "role changes cannot include --setting or --value")
        require(any(value is not None for value in (model, effort, service_tier, agent_type)),
                "role changes require a role setting")
        if model is not None:
            require(valid_model(model), "model must be a nonempty identifier of at most 128 characters")
            config["roles"][role]["model"] = model.strip()
        if effort is not None:
            require(isinstance(effort, str) and effort in EFFORTS, "effort must be low, medium, high, xhigh, max, or ultra")
            config["roles"][role]["effort"] = effort
        if service_tier is not None:
            require(isinstance(service_tier, str) and service_tier in CONFIG_SERVICE_TIERS,
                    "service_tier must be default or fast")
            config["roles"][role]["service_tier"] = service_tier
        if agent_type is not None:
            require(valid_agent_type(agent_type), "agent_type must be a nonempty identifier of at most 128 characters")
            config["roles"][role]["agent_type"] = agent_type.strip()
        changed = [key for key, value in (("model", model), ("effort", effort),
                                          ("service_tier", service_tier), ("agent_type", agent_type))
                   if value is not None]
        description = f"{role} " + "/".join(changed)
    else:
        require(model is None and effort is None and service_tier is None and agent_type is None,
                "setting changes cannot include role options")
        require(setting is not None and value is not None, "provide either --role with --model and --effort, or --setting with --value")
        config[setting] = parse_setting(setting, value)
        description = setting
    config["revision"] += 1
    validate_config(config)
    atomic_json_write(path, config)
    return f"已更新 {description}；revision {config['revision']}，保存至 {path}。"


def safe_text(value: Any) -> str:
    out = []
    for char in str(value):
        category = unicodedata.category(char)
        if char in "\n\r\t":
            out.append(" ")
        elif category.startswith("C") or category in {"Zl", "Zp"}:
            out.append("�")
        else:
            out.append(char)
    return "".join(out)


def show_value(value: Any) -> str:
    return "未知" if value is None else safe_text(value)


def evidence_preview(items: list[str], limit: int = 2, max_chars: int = 120) -> str:
    if not items:
        return "未记录"
    preview = []
    for item in items[:limit]:
        clean = safe_text(item)
        if re.match(r"^(?:[A-Za-z]:[\\/]|\\\\)", clean):
            clean = PureWindowsPath(clean).name + "（文件证据，明细见 JSON）"
        elif clean.startswith("/"):
            clean = PurePosixPath(clean).name + "（文件证据，明细见 JSON）"
        elif re.search(r"[A-Za-z]:[\\/]|\\\\|(?:^|[\s(（:\"'：])/(?!/|\s|$)", clean):
            clean = "文件证据（明细见 JSON）"
        clean = re.sub(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
                       "会话 ID（明细见 JSON）", clean)
        preview.append(clean if len(clean) <= max_chars else clean[:max_chars - 3] + "...")
    remaining = len(items) - len(preview)
    if remaining:
        preview.append(f"其余 {remaining} 项见 JSON")
    return "；".join(preview)


def participant_role(participant: dict[str, Any]) -> str:
    return "、".join(ROLE_LABELS.get(role, "协调者") for role in participant["roles"])


def token_completeness(participants: list[dict[str, Any]]) -> tuple[bool, int | None]:
    has_one_coordinator = sum("coordinator" in p.get("roles", []) for p in participants) == 1
    complete = bool(participants) and has_one_coordinator and all(
        isinstance(p.get("token_usage"), dict)
        and is_int(p["token_usage"].get("total"))
        and p["token_usage"].get("complete") is True
        and p["token_usage"].get("scope") == "task_delta"
        and isinstance(p["token_usage"].get("source"), str)
        and bool(p["token_usage"]["source"].strip()) for p in participants
    )
    return complete, sum(p["token_usage"]["total"] for p in participants) if complete else None


def elapsed_label(run: dict[str, Any]) -> str:
    start = parse_time(run["started_at"], "started_at")
    finish = parse_time(run.get("finished_at"), "finished_at", nullable=True)
    if finish is not None:
        return f"{(finish - start).total_seconds():g} 秒"
    return "未知（记录尚无 finished_at）"


def record_show(run: dict[str, Any], as_json: bool = False, source_path: Path | None = None) -> str:
    validate_run(run)
    if as_json:
        return json.dumps(run, ensure_ascii=False, indent=2)
    participants = run["participants"]
    token_complete, total_tokens = token_completeness(participants)
    output = [
        "Aisland Agent Router 本轮记录",
        f"任务：{safe_text(run['task'])}",
        f"触发：{safe_text(run['trigger']['mode'])}；原因：{safe_text(run['trigger']['reason'])}",
        f"状态：{safe_text(run['status'])}；预设：{safe_text(run['config_snapshot']['preset'])}；配置 revision：{run['config_snapshot']['revision']}",
        f"快照时间：{safe_text(run.get('snapshot_at') or '未知')}（记录文件快照，不是实时工具状态）",
    ]
    decision = run.get("review_decision")
    if decision is not None:
        policy = run["config_snapshot"].get("review_policy")
        reviewer_required = policy in {None, "always"} or decision["required"]
        choice = "需要 Reviewer" if reviewer_required else "无需 Reviewer"
        output.append(f"复核决定：{choice}；原因：{safe_text(decision['reason'])}")
    elif run["config_snapshot"].get("review_policy") != "risk_based":
        output.append("复核决定：沿用历史策略，完成时需要 Reviewer。")

    output.append("参与角色与结果")
    requested_fields = ("requested_model", "requested_effort", "requested_service_tier")
    actual_fields = ("actual_model", "actual_effort", "actual_service_tier")
    any_requested = any(any(p.get(key) is not None for key in requested_fields) for p in participants)
    any_actual = any(any(p.get(key) is not None for key in actual_fields) for p in participants)
    any_elapsed = any(p.get("elapsed_seconds") is not None for p in participants)
    any_tokens = any(any(p["token_usage"].get(key) is not None for key in TOKEN_FIELDS) for p in participants)

    if not participants:
        output.append("当前尚无参与会话。")
    for index, participant in enumerate(participants, 1):
        requested_model = participant.get("requested_model")
        requested_effort = participant.get("requested_effort")
        observed_models = list(dict.fromkeys(participant.get("observed_models", [])))
        if requested_model is not None or requested_effort is not None:
            model_heading = f"请求 {show_value(requested_model)} / {show_value(requested_effort)}"
        elif "coordinator" in participant["roles"]:
            model_heading = "当前会话"
        elif observed_models:
            model_heading = "日志模型 " + "、".join(safe_text(model) for model in observed_models)
        else:
            model_heading = "请求未知 / 未知"
        output.append(f"{index}. {safe_text(participant_role(participant))} · {model_heading}")
        output.append(f"   工作与结果：{safe_text(participant['work']) or '未记录'}")
        output.append(f"   状态：{participant['status']}")
        metrics = [
            f"{safe_text(metric['name'])}：{metric['count']} {safe_text(metric['unit'])}"
            for metric in participant["metrics"]
        ]
        if metrics:
            output.append(f"   可核对成果：{'；'.join(metrics)}")
        if participant["evidence"]:
            output.append(f"   证据：{evidence_preview(participant['evidence'])}")
        findings = participant.get("findings", [])
        if findings:
            output.append(f"   重要发现：{'；'.join(safe_text(item) for item in findings)}")
        requested_tier = participant.get("requested_service_tier")
        if requested_tier is not None:
            output.append(f"   请求速度档：{safe_text(requested_tier)}")
        actual = [participant.get(key) for key in actual_fields]
        if any(value is not None for value in actual):
            output.append(
                "   实际观测：模型 "
                + show_value(actual[0]) + "；推理强度 " + show_value(actual[1])
                + "；速度档 " + show_value(actual[2])
            )
        if observed_models:
            output.append(f"   日志模型：{'、'.join(safe_text(model) for model in observed_models)}")
        if participant.get("elapsed_seconds") is not None:
            output.append(f"   角色耗时：{participant['elapsed_seconds']:g} 秒")
        usage = participant["token_usage"]
        observed_tokens = [f"{key}={usage[key]}" for key in TOKEN_FIELDS if usage.get(key) is not None]
        if observed_tokens:
            scope = {
                "task_delta": "任务增量",
                "session_total": "会话累计",
                "unknown": "范围未知",
            }[usage["scope"]]
            output.append(f"   Token（{scope}）：{'；'.join(observed_tokens)}")
            if token_complete and total_tokens and usage["scope"] == "task_delta":
                output.append(f"   本轮占比：{usage['total'] / total_tokens * 100:.1f}%")
            elif token_complete and total_tokens == 0 and usage["scope"] == "task_delta":
                output.append("   本轮占比：总量为零，无法计算")
        output.append(f"   尝试：{participant['attempts']}；咨询：{participant['consultations']}")

    if participants and not any_requested:
        output.append("请求模型、推理强度与速度档：本轮未记录。")
    if participants and not any_actual:
        output.append("实际模型、推理强度与速度档：本轮未观测。")
    if participants and not any_elapsed:
        output.append("角色耗时：本轮未记录。")
    if not any_tokens:
        output.append("Token 用量：本轮未记录。")
    elif not token_complete:
        output.append("Token 任务总量：未知；占比未计算（数据不完整，或包含非任务增量范围）。")
    else:
        total_share = "未知（总量为零）" if total_tokens == 0 else "仅按完整任务增量计算"
        output.append(f"Token 任务总量：{total_tokens}；占比：{total_share}")

    output.extend((
        f"任务耗时：{elapsed_label(run)}",
        f"返工/重试：{run['retries']}",
        f"验收：{safe_text(run['verification']['status'])}；{safe_text(run['verification']['summary']) or '未记录'}",
        f"验收证据：{evidence_preview(run['verification']['evidence'])}",
    ))
    if run["notes"]:
        output.append(f"备注：{'；'.join(safe_text(note) for note in run['notes'])}")
    output.append("完整会话 ID 与数据来源保留在 JSON 记录中。")
    return "\n".join(output)


def record_validate(path: Path) -> str:
    validate_run(read_json(path))
    return f"有效记录：{path}"


def record_summary(paths: list[Path]) -> str:
    runs = []
    for path in paths:
        run = read_json(path)
        validate_run(run)
        runs.append((path, run))
    lines = [f"Aisland Agent Router 显式记录汇总（{len(runs)} 条）"]
    fully_measured = 0
    for index, (path, run) in enumerate(runs, 1):
        participants = run["participants"]
        complete, _ = token_completeness(participants)
        fully_measured += int(complete)
        usage_label = "完整任务增量" if complete else "不完整或未知"
        lines.append(
            f"{index}. {safe_text(path.name)}：{run['status']}；任务耗时 {elapsed_label(run)}；Token {usage_label}"
        )
    lines.append(f"总计：{len(runs)} 条；完整测量：{fully_measured} 条。仅对明确列出的文件汇总。")
    lines.append("不同工作单位不相加；部分 Token 不合并成总量，也不计算部分占比。")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aisland Agent Router local helper")
    commands = parser.add_subparsers(dest="command", required=True)
    config = commands.add_parser("config")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    show = config_commands.add_parser("show")
    show.add_argument("--config", type=Path)
    show.add_argument("--json", action="store_true")
    setting = config_commands.add_parser("set")
    setting.add_argument("--config", type=Path)
    group = setting.add_mutually_exclusive_group(required=True)
    group.add_argument("--role", choices=ROLES)
    group.add_argument("--setting")
    setting.add_argument("--model")
    setting.add_argument("--effort")
    setting.add_argument("--service-tier", choices=sorted(CONFIG_SERVICE_TIERS))
    setting.add_argument("--agent-type")
    setting.add_argument("--value")
    record = commands.add_parser("record")
    record_commands = record.add_subparsers(dest="record_command", required=True)
    validate = record_commands.add_parser("validate")
    validate.add_argument("--file", type=Path, required=True)
    show_record = record_commands.add_parser("show")
    show_record.add_argument("--file", type=Path, required=True)
    show_record.add_argument("--json", action="store_true")
    summary = record_commands.add_parser("summary")
    summary.add_argument("--files", type=Path, nargs="+", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "config":
            if args.config_command == "show":
                path = args.config or default_config_path()
                result = config_show(path, args.json)
            else:
                path = args.config or (skill_root() / ".local" / "config.json")
                result = config_set(path, args.role, args.model, args.effort, args.setting, args.value,
                                    args.service_tier, args.agent_type)
        elif args.record_command == "validate":
            result = record_validate(args.file)
        elif args.record_command == "show":
            result = record_show(read_json(args.file), args.json, args.file)
        else:
            result = record_summary(args.files)
        print(result)
        return 0
    except RouterError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
