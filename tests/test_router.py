from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "router.py"
SPEC = importlib.util.spec_from_file_location("router", SOURCE)
router = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(router)
DEFAULT_CONFIG = json.loads((SOURCE.parents[1] / "assets" / "default-config.json").read_text(encoding="utf-8-sig"))


def config_fixture():
    return copy.deepcopy(DEFAULT_CONFIG)


def run_fixture():
    return {
        "schema_version": 1, "run_id": "test-run", "task": "验证本地记录",
        "trigger": {"mode": "explicit", "reason": "用户要求测试"},
        "config_snapshot": config_fixture(), "status": "partial",
        "review_decision": {"required": False, "reason": "Fixture has no elevated review risk"},
        "started_at": "2026-10-01T10:00:00Z", "finished_at": "2026-10-01T10:00:10Z",
        "participants": [{
            "id": "session-1", "agent_id": "agent-1", "roles": ["coordinator", "executor"],
            "requested_model": "gpt-6-luna", "requested_effort": "high",
            "actual_model": None, "actual_effort": None, "status": "accepted", "work": "验证输出",
            "metrics": [{"name": "files_changed", "count": 0, "unit": "files", "evidence": "fixture"}],
            "elapsed_seconds": 10, "attempts": 1, "consultations": 0,
            "token_usage": {"total": None, "input": 12, "output": None, "cached_input": None,
                            "reasoning": None, "scope": "unknown", "source": None, "complete": False},
            "evidence": ["fixture"],
        }],
        "verification": {"status": "partial", "summary": "仅测试数据", "evidence": []},
        "retries": 0, "notes": [],
    }


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="router-test-", dir=os.environ["ROUTER_TEST_TMP"]))

    def write_json(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def test_config_persistence_revision_and_invalid_nonmutation(self):
        path = self.write_json(self.root / "config.json", config_fixture())
        message = router.config_set(path, None, None, None, "max_concurrent", "4")
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("revision 2", message)
        self.assertEqual(saved["max_concurrent"], 4)
        before = path.read_bytes()
        with self.assertRaises(router.RouterError):
            router.config_set(path, None, None, None, "max_concurrent", "0")
        self.assertEqual(path.read_bytes(), before)
        invalid_existing = config_fixture()
        invalid_existing["sensitivity"] = {"bad": "enum"}
        self.write_json(path, invalid_existing)
        before = path.read_bytes()
        with self.assertRaises(router.RouterError):
            router.config_set(path, None, None, None, "enabled", "false")
        self.assertEqual(path.read_bytes(), before)

    def test_role_model_and_effort_can_be_updated_independently(self):
        path = self.write_json(self.root / "config.json", config_fixture())
        original = json.loads(path.read_text(encoding="utf-8"))["roles"]["explorer"]
        with redirect_stdout(io.StringIO()):
            status = router.main(["config", "set", "--config", str(path), "--role", "explorer", "--model", "custom-model"])
        self.assertEqual(status, 0)
        after_model = json.loads(path.read_text(encoding="utf-8"))["roles"]["explorer"]
        self.assertEqual(after_model["model"], "custom-model")
        self.assertEqual(after_model["effort"], original["effort"])
        with redirect_stdout(io.StringIO()):
            status = router.main(["config", "set", "--config", str(path), "--role", "explorer", "--effort", "high"])
        self.assertEqual(status, 0)
        after_effort = json.loads(path.read_text(encoding="utf-8"))["roles"]["explorer"]
        self.assertEqual(after_effort["model"], "custom-model")
        self.assertEqual(after_effort["effort"], "high")
        with redirect_stdout(io.StringIO()):
            status = router.main(["config", "set", "--config", str(path), "--role", "explorer",
                                  "--service-tier", "fast", "--agent-type", "aisland_explorer"])
        self.assertEqual(status, 0)
        after_role = json.loads(path.read_text(encoding="utf-8"))["roles"]["explorer"]
        self.assertEqual(after_role["service_tier"], "fast")
        self.assertEqual(after_role["agent_type"], "aisland_explorer")
        before = path.read_bytes()
        with self.assertRaises(router.RouterError):
            router.config_set(path, "explorer", None, "not-an-effort", None, None)
        self.assertEqual(path.read_bytes(), before)

    def test_malformed_enum_types_raise_router_error(self):
        config = config_fixture()
        for target, key, malformed in (
            (config, "sensitivity", []),
            (config, "decision_mode", []),
            (config, "review_policy", {}),
            (config["roles"]["brain"], "effort", {}),
            (config["roles"]["explorer"], "service_tier", []),
        ):
            changed = copy.deepcopy(config)
            if target is config:
                changed[key] = malformed
            else:
                changed["roles"]["brain"][key] = malformed
            with self.subTest(key=key, malformed=malformed), self.assertRaises(router.RouterError):
                router.validate_config(changed)
        mutations = (
            lambda r: r["trigger"].update(mode=[]),
            lambda r: r.update(status={}),
            lambda r: r["participants"][0].update(status=[]),
            lambda r: r["participants"][0]["token_usage"].update(scope={}),
            lambda r: r["participants"][0].update(requested_service_tier=[]),
            lambda r: r.update(review_decision={"required": 0, "reason": "bad"}),
            lambda r: r["verification"].update(status=[]),
        )
        for mutate in mutations:
            run = run_fixture()
            mutate(run)
            with self.subTest(run=run), self.assertRaises(router.RouterError):
                router.validate_run(run)

    def test_cli_set_defaults_to_local_and_preserves_packaged_asset(self):
        original_root = router.skill_root
        root = self.root
        (root / "assets").mkdir()
        asset = self.write_json(root / "assets" / "default-config.json", config_fixture())
        original_asset = asset.read_bytes()
        router.skill_root = lambda: root
        try:
            output = io.StringIO()
            with redirect_stdout(output):
                status = router.main(["config", "set", "--setting", "sensitivity", "--value", "active"])
            self.assertEqual(status, 0)
        finally:
            router.skill_root = original_root
        local = json.loads((root / ".local" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(local["sensitivity"], "active")
        self.assertEqual(local["revision"], 2)
        self.assertEqual(asset.read_bytes(), original_asset)

    def test_legacy_local_preferences_are_not_normalized_or_overwritten(self):
        path = self.write_json(self.root / "legacy.json", config_fixture())
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved.pop("decision_mode")
        saved.pop("review_policy")
        for role in saved["roles"].values():
            role.pop("service_tier", None)
            role.pop("agent_type", None)
        self.write_json(path, saved)
        router.config_set(path, None, None, None, "sensitivity", "active")
        changed = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("decision_mode", changed)
        self.assertNotIn("review_policy", changed)
        self.assertNotIn("service_tier", changed["roles"]["explorer"])
        self.assertNotIn("agent_type", changed["roles"]["explorer"])
        self.assertEqual(changed["sensitivity"], "active")

    def test_new_config_settings_can_be_changed_without_role_api_breakage(self):
        path = self.write_json(self.root / "config.json", config_fixture())
        router.config_set(path, None, None, None, "decision_mode", "dedicated")
        router.config_set(path, None, None, None, "review_policy", "always")
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["decision_mode"], "dedicated")
        self.assertEqual(saved["review_policy"], "always")
        self.assertEqual(saved["revision"], 3)

    def test_show_does_not_create_local_state(self):
        local_config = SOURCE.parents[1] / ".local" / "config.json"
        existed_before = local_config.exists()
        result = router.config_show(SOURCE.parents[1] / "assets" / "default-config.json", False)
        self.assertIn("1. 决策者（主会话）", result)
        self.assertIn("继承当前对话", result)
        self.assertIn("按需专家（Astra）", result)
        self.assertIn("2. 探索者（explorer）", result)
        self.assertIn("请求速度档：Fast；实际服务未验证", result)
        self.assertIn("4. 检查者（reviewer）", result)
        self.assertNotIn("|", result)
        self.assertNotIn("+---", result)
        self.assertIn("配置读取时间（UTC）", result)
        self.assertIn("需要修改哪个角色", result)
        self.assertEqual(local_config.exists(), existed_before)

    def test_missing_and_partial_tokens_hide_every_share(self):
        run = run_fixture()
        run["participants"][0]["token_usage"].update(total=99, source="partial observation")
        shown = router.record_show(run)
        self.assertIn("本轮记录", shown)
        self.assertIn("预设：main-led-v2", shown)
        self.assertIn("1. 协调者、执行者 · 请求 gpt-6-luna / high", shown)
        self.assertIn("   工作与结果：验证输出", shown)
        self.assertIn("Token 任务总量：未知", shown)
        self.assertNotIn("100.0%", shown)
        self.assertIn("total=99", shown)
        self.assertIn("input=12", shown)
        self.assertNotIn("session-1", shown)
        self.assertNotIn("partial observation", shown)
        self.assertNotIn("table(", shown)
        self.assertFalse(any(line.startswith(("|", "+---")) for line in shown.splitlines()))

    def test_complete_totals_and_subset_fields_not_double_counted(self):
        run = run_fixture()
        second = copy.deepcopy(run["participants"][0])
        second["id"], second["agent_id"] = "session-2", "agent-2"
        second["roles"] = ["explorer"]
        run["participants"][0]["token_usage"].update(total=100, input=80, output=20, cached_input=30,
                                                      reasoning=5, scope="task_delta", source="fixture", complete=True)
        second["token_usage"].update(total=50, input=35, output=15, cached_input=10,
                                     reasoning=2, scope="task_delta", source="fixture", complete=True)
        run["participants"].append(second)
        shown = router.record_show(run)
        self.assertIn("Token 任务总量：150", shown)
        self.assertIn("66.7%", shown)
        self.assertIn("33.3%", shown)
        self.assertNotIn("385", shown)

    def test_zero_complete_totals_have_unknown_shares(self):
        run = run_fixture()
        run["participants"][0]["token_usage"].update(total=0, scope="task_delta", source="fixture", complete=True)
        shown = router.record_show(run)
        self.assertIn("Token 任务总量：0", shown)
        self.assertIn("未知（总量", shown)
        self.assertIn("为零）", shown)

    def test_session_total_is_cumulative_and_never_enters_task_total_or_share(self):
        run = run_fixture()
        run["participants"][0]["token_usage"].update(
            total=500, scope="session_total", source="session log", complete=True
        )
        self.assertEqual(router.token_completeness(run["participants"]), (False, None))
        shown = router.record_show(run)
        self.assertIn("Token（会话累计）：total=500", shown)
        self.assertIn("Token 任务总量：未知", shown)
        self.assertNotIn("Token 任务总量：500", shown)
        self.assertNotIn("本轮占比：", shown)

    def test_logged_models_and_findings_do_not_claim_runtime_attestation(self):
        run = run_fixture()
        participant = run["participants"][0]
        participant["observed_models"] = ["gpt-6-luna", "gpt-6-sol"]
        participant["findings"] = ["验证结果符合预期"]
        participant["requested_model"] = None
        participant["requested_effort"] = None
        participant["actual_model"] = None
        shown = router.record_show(run)
        self.assertIn("1. 协调者、执行者 · 当前会话", shown)
        self.assertIn("日志模型：gpt-6-luna、gpt-6-sol", shown)
        self.assertIn("重要发现：验证结果符合预期", shown)
        self.assertIn("实际模型、推理强度与速度档：本轮未观测。", shown)
        self.assertIsNone(participant["actual_model"])

    def test_coordinator_is_required_unique_and_gates_token_totals(self):
        run = run_fixture()
        run["participants"][0]["roles"] = ["executor"]
        with self.assertRaisesRegex(router.RouterError, "exactly one coordinator"):
            router.validate_run(run)
        with self.assertRaisesRegex(router.RouterError, "exactly one coordinator"):
            router.record_show(run)
        self.assertEqual(router.token_completeness(run["participants"]), (False, None))

        run = run_fixture()
        coordinator = run["participants"][0]
        coordinator["token_usage"].update(total=10, scope="task_delta", source="fixture", complete=True)
        other = copy.deepcopy(coordinator)
        other.update(id="session-2", agent_id="agent-2", roles=["explorer"])
        other["token_usage"].update(total=None, input=None, output=None, cached_input=None, reasoning=None,
                                    scope="unknown", source=None, complete=False)
        run["participants"].append(other)
        shown = router.record_show(run)
        self.assertIn("Token 任务总量：未知", shown)

        duplicated = run_fixture()
        extra = copy.deepcopy(duplicated["participants"][0])
        extra.update(id="session-2", agent_id="agent-2")
        duplicated["participants"].append(extra)
        with self.assertRaisesRegex(router.RouterError, "exactly one coordinator"):
            router.validate_run(duplicated)

    def test_empty_participants_only_valid_while_running(self):
        run = run_fixture()
        run["participants"] = []
        run["status"] = "running"
        run["finished_at"] = None
        router.validate_run(run)
        run["status"] = "partial"
        with self.assertRaisesRegex(router.RouterError, "empty participants"):
            router.validate_run(run)

    def test_duplicate_physical_and_agent_sessions_rejected(self):
        run = run_fixture()
        run["participants"].append(copy.deepcopy(run["participants"][0]))
        with self.assertRaisesRegex(router.RouterError, "physical session"):
            router.validate_run(run)
        run["participants"][1]["id"] = "session-2"
        with self.assertRaisesRegex(router.RouterError, "agent_id"):
            router.validate_run(run)

    def test_negative_and_boolean_metrics_rejected(self):
        for count in (-1, True):
            run = run_fixture()
            run["participants"][0]["metrics"][0]["count"] = count
            with self.subTest(count=count), self.assertRaises(router.RouterError):
                router.validate_run(run)

    def test_known_token_subsets_are_bounded_without_assuming_missing_values_are_zero(self):
        for field, parent, count in (
            ("cached_input", "input", 13),
            ("reasoning", "output", 21),
        ):
            run = run_fixture()
            usage = run["participants"][0]["token_usage"]
            usage[parent] = 12 if parent == "input" else 20
            usage[field] = count
            with self.subTest(field=field), self.assertRaises(router.RouterError):
                router.validate_run(run)

        run = run_fixture()
        usage = run["participants"][0]["token_usage"]
        usage.update(input=None, cached_input=5, output=None, reasoning=7)
        router.validate_run(run)

    def test_risk_based_completion_allows_explicit_waiver_but_requires_coordinator_evidence(self):
        run = run_fixture()
        run["status"] = "completed"
        run["participants"][0]["evidence"] = [
            "executor report", "test log", r"E:\private\session-trace.jsonl"
        ]
        run["verification"].update(
            status="passed", summary="checks passed",
            evidence=["check-log", "test report", "full trace source"]
        )
        router.validate_run(run)
        shown = router.record_show(run)
        self.assertIn("证据：executor report；test log；其余 1 项见 JSON", shown)
        self.assertIn("验收证据：check-log；test report；其余 1 项见 JSON", shown)
        self.assertNotIn(r"E:\private\session-trace.jsonl", shown)

        run["participants"][0]["evidence"] = []
        with self.assertRaisesRegex(router.RouterError, "coordinator work and evidence"):
            router.validate_run(run)

    def test_evidence_preview_keeps_results_but_hides_source_paths_and_full_ids(self):
        run = run_fixture()
        session = "11111111-2222-3333-4444-555555555555"
        run["participants"][0]["evidence"] = [
            r"E:\private\session-trace.jsonl", "checked session " + session
        ]
        run["verification"].update(
            status="passed", summary="checks passed",
            evidence=[r"E:\private\verification.log", "41 regression checks passed"]
        )
        shown = router.record_show(run)
        self.assertIn("session-trace.jsonl", shown)
        self.assertIn("verification.log", shown)
        self.assertIn("41 regression checks passed", shown)
        self.assertNotIn(r"E:\private", shown)
        self.assertNotIn(session, shown)
        self.assertNotIn("/home/private", router.evidence_preview(["/home/private/check.log"]))
        self.assertNotIn(r"E:\private", router.evidence_preview([r"source: E:\private\check.log"]))
        self.assertEqual(router.evidence_preview(["Ran 41 tests / OK"]), "Ran 41 tests / OK")
        self.assertEqual(run["participants"][0]["evidence"][1], "checked session " + session)

    def test_legacy_and_always_policies_still_require_accepted_reviewer(self):
        legacy = run_fixture()
        legacy["config_snapshot"].pop("decision_mode")
        legacy["config_snapshot"].pop("review_policy")
        legacy.pop("review_decision")
        legacy["status"] = "completed"
        legacy["verification"].update(status="passed", summary="checks passed", evidence=["check-log"])
        with self.assertRaisesRegex(router.RouterError, "accepted Reviewer"):
            router.validate_run(legacy)

        always = run_fixture()
        always["config_snapshot"]["review_policy"] = "always"
        always["status"] = "completed"
        always["verification"].update(status="passed", summary="checks passed", evidence=["check-log"])
        always["review_decision"] = {"required": False, "reason": "Explicit false cannot override always policy"}
        with self.assertRaisesRegex(router.RouterError, "accepted Reviewer"):
            router.validate_run(always)

    def test_passed_verification_and_completed_reviewer_require_evidence(self):
        run = run_fixture()
        run["verification"].update(status="passed", summary="", evidence=[])
        with self.assertRaisesRegex(router.RouterError, "passed verification requires"):
            router.validate_run(run)

        run = run_fixture()
        run["status"] = "completed"
        run["verification"].update(status="passed", summary="checks passed", evidence=["check-log"])
        run["review_decision"]["required"] = True
        reviewer = copy.deepcopy(run["participants"][0])
        reviewer.update(id="session-review", agent_id="agent-review", roles=["reviewer"], status="accepted",
                        work="", evidence=[])
        run["participants"].append(reviewer)
        with self.assertRaisesRegex(router.RouterError, "Reviewer work and evidence"):
            router.validate_run(run)
        reviewer.update(work="Reviewed delivered artifacts", evidence=["review-notes.md"])
        router.validate_run(run)

        active = copy.deepcopy(run["participants"][0])
        active.update(id="session-active", agent_id="agent-active", roles=["explorer"], status="working")
        run["participants"].append(active)
        with self.assertRaisesRegex(router.RouterError, "cannot contain"):
            router.validate_run(run)

    def test_risk_based_records_require_a_valid_decision_even_before_completion(self):
        run = run_fixture()
        run.pop("review_decision")
        with self.assertRaisesRegex(router.RouterError, "require review_decision"):
            router.validate_run(run)
        for reason in ("", "  ", None):
            malformed = run_fixture()
            malformed["review_decision"]["reason"] = reason
            with self.subTest(reason=reason), self.assertRaisesRegex(router.RouterError, "reason must be nonempty"):
                router.validate_run(malformed)

    def test_timestamps_accept_offsets_and_compare_instants(self):
        run = run_fixture()
        run["finished_at"] = "2026-10-01T18:00:10+08:00"
        router.validate_run(run)
        self.assertIn("任务耗时：10 秒", router.record_show(run))
        run["finished_at"] = "2026-10-01T09:59:59-00:00"
        with self.assertRaisesRegex(router.RouterError, "must not precede"):
            router.validate_run(run)

    def test_long_chinese_prose_sanitizes_controls_without_table_borders(self):
        run = run_fixture()
        run["participants"][0]["work"] = "中文任务" * 20 + "\x1b[31m隐藏控制\x1b[0m\t结束"
        run["config_snapshot"]["preset"] = "\x1b[31muntrusted-preset"
        shown = router.record_show(run)
        self.assertNotIn("\x1b", shown)
        self.assertIn("�[31muntrusted-preset", shown)
        self.assertIn("中文任务" * 20, shown)
        self.assertFalse(any(line.startswith(("|", "+---")) for line in shown.splitlines()))

    def test_summary_reads_only_explicit_files_and_warns_about_partial_use(self):
        named = self.write_json(self.root / "one.json", run_fixture())
        ignored = self.write_json(self.root / "ignored.json", {"not": "a record"})
        summary = router.record_summary([named])
        self.assertIn("（1 条）", summary)
        self.assertNotIn(ignored.name, summary)
        self.assertIn("部分 Token 不合并", summary)
        self.assertFalse(any(line.startswith(("|", "+---")) for line in summary.splitlines()))

    def test_documented_full_record_template_validates(self):
        reference = SOURCE.parents[1] / "references" / "records.md"
        text = reference.read_text(encoding="utf-8")
        block = text.split("## Complete Record Template", 1)[1].split("```json", 1)[1].split("```", 1)[0]
        router.validate_run(json.loads(block))


if __name__ == "__main__":
    unittest.main()
