from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "fireworks.py"


def example() -> dict:
    return {
        "schema_version": 1,
        "mode": "sequence",
        "title": "两轮模型调用",
        "participants": [
            {"id": "app", "label": "应用"},
            {"id": "runner", "label": "Runner"},
            {"id": "model", "label": "模型"},
        ],
        "messages": [
            {"id": "start", "source": "app", "target": "runner", "kind": "call", "label": "运行规划"},
            {"id": "round-1", "source": "runner", "target": "model", "kind": "call", "label": "第一轮输入"},
            {"id": "read", "source": "model", "target": "runner", "kind": "return", "label": "返回工具请求"},
            {"id": "round-2", "source": "runner", "target": "model", "kind": "call", "label": "带上 obs-1 和 obs-2"},
            {"id": "finish", "source": "runner", "target": "app", "kind": "return", "label": "返回运行结果"},
        ],
    }


class StructuredSequenceTest(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        path = Path(scratch.name)
        self.source = path / "sequence.json"
        self.output = path / "sequence.svg"
        self.report = path / "sequence.report.json"

    def cli(self, *args):
        return subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True)

    def render(self, diagram):
        self.source.write_text(json.dumps(diagram, ensure_ascii=False), encoding="utf-8")
        completed = self.cli("render", "sequence", self.source, self.output, "--report", self.report)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        check = self.cli("check", self.output)
        self.assertEqual(check.returncode, 0, check.stdout)
        return ET.parse(self.output).getroot(), json.loads(self.report.read_text(encoding="utf-8"))

    def test_cli_renders_ordered_lifelines_calls_and_returns(self) -> None:
        source = example()
        root, report = self.render(source)
        checked = self.cli("validate", "sequence", self.source)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        details = json.loads(checked.stdout)
        for output in (details, report):
            self.assertEqual(output["semantics"]["sequence"], {
                "participants": source["participants"],
                "messages": source["messages"],
            })
        self.assertEqual((details["nodes"], details["edges"]), (3, 5))
        lifelines = root.findall(".//*[@data-graph-role='lifeline']")
        self.assertEqual([item.get("data-participant-id") for item in lifelines], ["app", "runner", "model"])
        centers = {item.get("data-participant-id"): float(item.get("x1")) for item in lifelines}
        self.assertEqual(list(centers.values()), sorted(centers.values()))
        edges = root.findall(".//*[@data-graph-role='edge']")
        self.assertEqual([edge.get("data-edge-id") for edge in edges], [item["id"] for item in source["messages"]])
        last_y = 0
        for edge, message, route in zip(edges, source["messages"], report["edges"]):
            self.assertEqual(route["id"], message["id"])
            self.assertEqual(route["message_kind"], message["kind"])
            self.assertEqual(edge.get("data-source"), message["source"])
            self.assertEqual(edge.get("data-target"), message["target"])
            self.assertEqual(edge.get("data-message-kind"), message["kind"])
            self.assertIsNotNone(edge.get("marker-end"))
            self.assertEqual(edge.get("stroke-dasharray") is not None, message["kind"] == "return")
            start_x, start_y = route["source_port"]
            end_x, end_y = route["target_port"]
            self.assertEqual(start_x, centers[message["source"]])
            self.assertEqual(end_x, centers[message["target"]])
            self.assertEqual(start_y, end_y)
            self.assertGreater(start_y, last_y)
            self.assertEqual(route["bends"], 0)
            last_y = start_y
        labels = [group.find("{*}text").text for group in root.findall(".//*[@data-graph-role='label']")]
        self.assertEqual(labels, [message["label"] for message in source["messages"]])
        original_bytes = self.output.read_bytes()
        self.render(source)
        self.assertEqual(self.output.read_bytes(), original_bytes)

    def test_autosizes_canvas_for_participant_text_and_configured_message_size(self) -> None:
        source = example()
        _, original = self.render(source)
        source["participants"][0]["label"] = "A participant with a long display label"
        source["style_overrides"] = {"arrow_label_size": 30}
        source["messages"][0]["label"] = "这个调用的内容比较长，自动安排宽度"
        source["text_policy"] = "strict"
        root, bigger = self.render(source)
        self.assertGreater(bigger["canvas"]["width"], original["canvas"]["width"])
        self.assertGreater(bigger["canvas"]["height"], original["canvas"]["height"])
        label = root.find(".//*[@data-owner='start'][@data-graph-role='label']/{*}text")
        self.assertEqual(label.get("font-size"), "30")
        self.assertEqual(label.text, source["messages"][0]["label"])

    def test_self_call_has_loop_and_next_message_stays_below_it(self) -> None:
        source = example()
        source["messages"].insert(0, {
            "id": "prepare", "source": "app", "target": "app", "kind": "call", "label": "校验输入",
        })
        _, report = self.render(source)
        loop, next_message = report["edges"][:2]
        self.assertEqual(loop["source_port"][0], loop["target_port"][0])
        self.assertGreater(loop["target_port"][1], loop["source_port"][1])
        self.assertEqual(loop["bends"], 2)
        self.assertLess(loop["target_port"][1], next_message["source_port"][1])

    def test_rejects_invalid_structured_data_in_validate(self) -> None:
        base = example()
        bad_scenarios = (
            ("flowchart", {**base, "mode": "flowchart"}, "mode.*sequence"),
            ("sequence", {**base, "nodes": []}, "cannot.*nodes"),
            ("sequence", {**base, "arrows": []}, "cannot.*arrows"),
            ("sequence", {**base, "edges": []}, "cannot.*edges"),
            ("sequence", {**base, "containers": []}, "cannot.*containers"),
            ("sequence", {**base, "messages": []}, r"messages.*non-empty array"),
            ("sequence", {**base, "messages": [{**base["messages"][0], "target": "missing"}]}, r"messages\[0\]\.target.*unknown participant"),
            ("sequence", {**base, "messages": [{**base["messages"][0], "kind": "reply"}]}, r"messages\[0\]\.kind.*call or return"),
            ("sequence", {**base, "messages": [{**base["messages"][0], "label": " "}]}, r"messages\[0\]\.label.*non-empty string"),
            ("sequence", {**base, "participants": [{**base["participants"][0], "id": 12}]}, r"participants\[0\]\.id.*non-empty string"),
            ("sequence", {**base, "participants": base["participants"] * 2}, "duplicate participant id"),
            ("sequence", {**base, "messages": base["messages"] * 2}, "duplicate message id"),
        )
        for mode, diagram, reason in bad_scenarios:
            with self.subTest(reason=reason):
                self.source.write_text(json.dumps(diagram), encoding="utf-8")
                result = self.cli("validate", mode, self.source)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertRegex(result.stderr, reason)


if __name__ == "__main__":
    unittest.main()
