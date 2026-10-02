from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import usage


SID = "11111111-2222-3333-4444-555555555555"
START = datetime(2026, 10, 2, 10, tzinfo=timezone.utc)
FINISH = datetime(2026, 10, 2, 10, 10, tzinfo=timezone.utc)


def tokens(total, output=10):
    return {"input_tokens": total-output, "output_tokens": output, "total_tokens": total,
            "cached_input_tokens": 0, "reasoning_output_tokens": 0}


def event(kind, minute, payload):
    return {"type": kind, "timestamp": f"2026-10-02T10:{minute}:00Z", "payload": payload}


def count(minute, total, last):
    return event("event_msg", minute, {"type": "token_count", "info": {
        "total_token_usage": total, "last_token_usage": last}})


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="usage-test-", dir=os.environ["ROUTER_TEST_TMP"]))

    def trace(self, events, meta_at="2026-10-02T09:00:00Z", sid=SID, name=None):
        path = self.root / (name or f"rollout-{sid}.jsonl")
        meta = {"type": "session_meta", "timestamp": meta_at, "payload": {"id": sid}}
        path.write_text("\n".join(json.dumps(e) for e in [meta, *events])+"\n", encoding="utf-8")
        return path

    def scan(self, events, **kwargs):
        return usage.read_trace(self.trace(events, **kwargs), SID, START, FINISH)

    def test_delta_excludes_history_and_deduplicates_across_model_switch(self):
        result = self.scan([
            {"type": "event_msg", "timestamp": "2026-10-02T09:59:00Z", "payload": {
                "type": "token_count", "info": {"total_token_usage": tokens(100,20)}}},
            event("turn_context", "00", {"model": "luna", "effort": "medium"}),
            count("01", tokens(130,25), tokens(30,5)),
            event("turn_context", "02", {"model": "sol", "effort": "high"}),
            count("02", tokens(130,25), tokens(30,5)),
            count("03", tokens(150,30), tokens(20,5)),
            event("event_msg", "04", {"type": "task_complete"}),
        ])
        self.assertEqual(result["tokens"]["total"], 50)
        self.assertEqual(result["tokens"]["output"], 10)
        self.assertEqual(result["duplicates_skipped"], 1)
        self.assertTrue(result["complete"])
        self.assertEqual([(x["model"], x["tokens"]["total"]) for x in result["segments"]], [("luna",30),("sol",20)])

    def test_cache_and_reasoning_are_subsets(self):
        t = {"input_tokens":80,"output_tokens":20,"total_tokens":100,
             "cached_input_tokens":60,"reasoning_output_tokens":15}
        result=self.scan([count("01",t,t),event("event_msg","02",{"type":"task_complete"})],meta_at="2026-10-02T10:00:00Z")
        self.assertEqual(result["tokens"],{"total":100,"input":80,"output":20,"cached_input":60,"reasoning":15})
        self.assertTrue(result["complete"])

    def test_inherited_first_counter_is_not_charged_to_task(self):
        result=self.scan([count("01",tokens(100010,15),tokens(10,5))],meta_at="2026-10-02T10:00:00Z")
        self.assertEqual(result["tokens"]["total"],10)
        self.assertFalse(result["complete"])
        self.assertIn("missing_start_baseline",result["warnings"])

    def test_reset_is_visible_and_never_counts_negative_delta(self):
        result=self.scan([count("01",tokens(100,20),tokens(100,20)),count("02",tokens(30,5),tokens(30,5)),
                          event("event_msg","03",{"type":"task_complete"})],meta_at="2026-10-02T10:00:00Z")
        self.assertEqual(result["tokens"]["total"],130)
        self.assertFalse(result["complete"])
        self.assertIn("cumulative_counter_reset",result["warnings"])

    def test_without_window_events_labels_session_cumulative(self):
        result=self.scan([{"type":"event_msg","timestamp":"2026-10-02T09:59:00Z","payload":{
            "type":"token_count","info":{"total_token_usage":tokens(500)}}}])
        self.assertEqual(result["scope"],"session_total")
        self.assertEqual(result["tokens"]["total"],500)
        self.assertFalse(result["complete"])

    def test_right_boundary_and_unfinished_snapshot(self):
        result=self.scan([count("01",tokens(100),tokens(100)),count("11",tokens(200,20),tokens(100)),
                          event("event_msg","12",{"type":"task_complete"})],meta_at="2026-10-02T10:00:00Z")
        self.assertEqual(result["tokens"]["total"],100)
        self.assertFalse(result["complete"])
        self.assertIn("snapshot_before_task_completion",result["warnings"])

    def test_unknown_optional_counts_stay_unknown(self):
        t=tokens(100);t.pop("cached_input_tokens");t.pop("reasoning_output_tokens")
        result=self.scan([count("01",t,t),event("event_msg","02",{"type":"task_complete"})],meta_at="2026-10-02T10:00:00Z")
        self.assertIsNone(result["tokens"]["cached_input"])
        self.assertIsNone(result["tokens"]["reasoning"])

    def test_identity_mismatch_and_malformed_tail_are_not_silent(self):
        p=self.trace([],sid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        with self.assertRaisesRegex(usage.router.RouterError,"session_meta"):
            usage.read_trace(p,SID,START,FINISH)
        p=self.trace([count("01",tokens(100),tokens(100)),event("event_msg","02",{"type":"task_complete"})],meta_at="2026-10-02T10:00:00Z")
        with p.open("a",encoding="utf-8") as f:f.write('{"partial":')
        result=usage.read_trace(p,SID,START,FINISH)
        self.assertFalse(result["complete"])
        self.assertIn("malformed_trace_line",result["warnings"])

    def test_known_sessions_only_and_no_message_body_export(self):
        from test_router import run_fixture
        run=run_fixture();run["started_at"]=START.isoformat();run["finished_at"]=FINISH.isoformat()
        run["review_decision"]={"required":False,"reason":"Offline telemetry fixture"}
        run["participants"][0]["agent_id"]=SID
        self.trace([event("response_item","01",{"content":"PRIVATE_BODY_SENTINEL"}),
                    count("01",tokens(100),tokens(100)),event("event_msg","02",{"type":"task_complete"})],
                   meta_at="2026-10-02T10:00:00Z")
        (self.root/"unrelated-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl").write_text('UNRELATED_INVALID',encoding="utf-8")
        report=usage.collect_usage_for_run(run,self.root)
        self.assertEqual(report["task_total"],100)
        self.assertNotIn("PRIVATE_BODY_SENTINEL",json.dumps(report))
        self.assertNotIn("unrelated",json.dumps(report))
        updated=usage.apply_usage_to_run(run,report)
        self.assertIsNone(updated["participants"][0]["actual_model"])
        self.assertEqual(updated["participants"][0]["token_usage"]["total"],100)
        self.assertIsNone(run["participants"][0]["token_usage"]["total"])

    def test_missing_and_duplicate_traces_do_not_inflate_total(self):
        from test_router import run_fixture
        run=run_fixture();run["participants"][0]["agent_id"]=SID
        run["review_decision"]={"required":False,"reason":"Offline missing-trace fixture"}
        report=usage.collect_usage_for_run(run,self.root)
        self.assertIsNone(report["task_total"])
        self.assertIn("missing_trace",report["sessions"][0]["warnings"])
        self.trace([]);self.trace([],name=f"duplicate-{SID}.jsonl")
        report=usage.collect_usage_for_run(run,self.root)
        self.assertIn("ambiguous_duplicate_traces",report["sessions"][0]["warnings"])

    def test_boolean_and_impossible_counts_are_invalid(self):
        self.assertIsNone(usage.counters({"input_tokens":True,"output_tokens":1}))
        self.assertIsNone(usage.counters({"input_tokens":5,"output_tokens":1,"total_tokens":50}))

    def test_derived_subsets_cannot_exceed_their_parent_delta(self):
        before={"input_tokens":80,"output_tokens":20,"total_tokens":100,
                "cached_input_tokens":0,"reasoning_output_tokens":0}
        after={"input_tokens":90,"output_tokens":20,"total_tokens":110,
               "cached_input_tokens":80,"reasoning_output_tokens":5}
        last={"input_tokens":10,"output_tokens":0,"total_tokens":10,
              "cached_input_tokens":5,"reasoning_output_tokens":0}
        result=self.scan([
            {"type":"event_msg","timestamp":"2026-10-02T09:59:00Z","payload":{
                "type":"token_count","info":{"total_token_usage":before}}},
            count("01",after,last),event("event_msg","02",{"type":"task_complete"})
        ])
        self.assertEqual(result["tokens"]["total"],10)
        self.assertIsNone(result["tokens"]["cached_input"])
        self.assertIsNone(result["tokens"]["reasoning"])
        self.assertFalse(result["complete"])
        self.assertIn("invalid_subset_delta",result["warnings"])

    def test_recorded_turn_can_close_after_delivery_snapshot_without_next_turn_leak(self):
        target="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        next_turn="99999999-8888-7777-6666-555555555555"
        path=self.trace([
            event("turn_context","00",{"turn_id":target,"model":"luna"}),
            count("01",tokens(100,20),tokens(100,20)),
            count("11",tokens(120,25),tokens(20,5)),
            event("event_msg","12",{"type":"task_complete","turn_id":target}),
            event("turn_context","13",{"turn_id":next_turn,"model":"sol"}),
            count("14",tokens(150,30),tokens(30,5))
        ],meta_at="2026-10-02T10:00:00Z")
        result=usage.read_trace(path,SID,START,FINISH,target)
        self.assertEqual(result["tokens"]["total"],120)
        self.assertTrue(result["complete"])
        self.assertEqual(result["turn_closed_at"],"2026-10-02T10:12:00+00:00")
        self.assertEqual(len(result["segments"]),1)

    def test_unclosed_recorded_turn_remains_incomplete(self):
        target="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        path=self.trace([event("turn_context","00",{"turn_id":target}),
                         count("01",tokens(100),tokens(100))],meta_at="2026-10-02T10:00:00Z")
        result=usage.read_trace(path,SID,START,FINISH,target)
        self.assertFalse(result["complete"])

    def test_recorded_turn_first_seen_after_cutoff_is_not_this_task(self):
        target="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        for kind, payload in (
            ("turn_context", {"turn_id":target,"model":"luna"}),
            ("event_msg", {"type":"task_started","turn_id":target}),
        ):
            with self.subTest(kind=kind):
                path=self.trace([
                    event(kind,"11",payload),
                    count("12",tokens(20,5),tokens(20,5)),
                    event("event_msg","13",{"type":"task_complete","turn_id":target}),
                ],meta_at="2026-10-02T10:00:00Z")
                result=usage.read_trace(path,SID,START,FINISH,target)
                self.assertEqual(result["events"],0)
                self.assertEqual(result["scope"],"unknown")
                self.assertIsNone(result["tokens"]["total"])
                self.assertIsNone(result["turn_closed_at"])
                self.assertFalse(result["complete"])


if __name__ == "__main__":
    unittest.main()
