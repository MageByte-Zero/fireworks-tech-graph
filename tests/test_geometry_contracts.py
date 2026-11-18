from __future__ import annotations

import importlib.util
import copy
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


generator = load_module("fireworks_generator", ROOT / "scripts" / "generate-from-template.py")
validator = load_module("fireworks_validator", ROOT / "scripts" / "validate_svg.py")
interactive_html = load_module("fireworks_interactive_html", ROOT / "scripts" / "interactive_html.py")


class ArrowLabelContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.diagram = {
            "mode": "flowchart",
            "style": 1,
            "width": 640,
            "height": 320,
            "nodes": [
                {"id": "check", "x": 20, "y": 90, "width": 100, "height": 60, "label": "Check"},
                {"id": "allow", "x": 450, "y": 90, "width": 120, "height": 60, "label": "Allow"},
            ],
            "arrows": [
                {"id": "allowed", "source": "check", "target": "allow", "label": "条件通过"},
            ],
        }
        self.arrow = self.diagram["arrows"][0]

    def render_label(self):
        svg = generator.build_svg("flowchart", self.diagram)
        root = ET.fromstring(svg)
        self.assertEqual(validator.geometry_check(root), [])
        group = next(element for element in root if element.get("data-owner") == "allowed" and element.get("data-graph-role") == "label")
        bounds = tuple(map(float, group.get("data-graph-bounds").split(",")))
        label_text = [element for element in group if element.tag.endswith("}text")]
        return group, bounds, label_text

    def test_arrow_font_size_uses_the_diagram_setting_and_edge_override(self) -> None:
        self.diagram["style_overrides"] = {"arrow_label_size": 24}
        _, _, (label,) = self.render_label()
        self.assertEqual(label.get("font-size"), "24")
        self.arrow["label_size"] = 19.5
        _, _, (label,) = self.render_label()
        self.assertEqual(label.get("font-size"), "19.5")

    def test_configured_edge_label_can_export_to_offline_html(self) -> None:
        self.arrow["label_size"] = 24
        svg = generator.build_svg("flowchart", self.diagram)
        offline_svg = ET.fromstring(interactive_html.sanitize_svg(svg))
        label = next(element for element in offline_svg.iter() if element.text == "条件通过")
        self.assertEqual(label.get("font-size"), "24")

    def test_larger_badge_avoids_nodes_and_declares_its_rendered_size(self) -> None:
        self.diagram["nodes"].append(
            {"id": "nearby", "x": 175, "y": 10, "width": 230, "height": 68, "label": "Nearby"}
        )
        _, default_bounds, _ = self.render_label()
        self.assertGreater(default_bounds[1], 78)
        self.assertLess(default_bounds[3], 120)

        self.arrow["label_size"] = 36
        group, bounds, _ = self.render_label()
        self.assertGreaterEqual(bounds[1], 82)
        self.assertGreater(bounds[1] + bounds[3], default_bounds[1] + default_bounds[3])
        badge = next(element for element in group if element.tag.endswith("}rect"))
        self.assertGreater(float(badge.get("width")), 144.0)
        self.assertEqual(float(badge.get("height")), bounds[3] - bounds[1])
        self.assertEqual(float(badge.get("y")), bounds[1])

    def test_offset_branch_label_follows_waypoints_at_its_effective_size(self) -> None:
        self.arrow.update(label_size=28, label_style="offset", route_points=[[150, 235], [420, 235]])
        group, bounds, (label,) = self.render_label()
        self.assertEqual(label.text, "条件通过")
        self.assertEqual(list(group), [label])
        self.assertGreater(bounds[1], 170)
        self.assertLess(bounds[3], 235)
        self.assertGreater(bounds[2] - bounds[0], 112)

    def test_sized_offset_label_stays_clear_of_a_short_connector(self) -> None:
        self.diagram["nodes"] = []
        self.diagram["height"] = 420
        self.arrow.clear()
        self.arrow.update(
            id="allowed", x1=260, y1=210, x2=290, y2=210,
            label="通过", label_size=24, label_style="offset",
        )
        _, bounds, (label,) = self.render_label()
        self.assertEqual(label.get("font-size"), "24")
        self.assertEqual(label.text, "通过")
        self.assertLess(bounds[3], 210)
        self.assertAlmostEqual((bounds[0] + bounds[2]) / 2, 275.0)

    def test_sized_monospace_label_reserves_room_for_narrow_glyphs(self) -> None:
        self.diagram.update(style=2, height=420)
        self.diagram["nodes"] = [
            {"id": "nearby", "x": 45, "y": 100, "width": 80, "height": 60, "label": "Nearby"}
        ]
        self.arrow.clear()
        self.arrow.update(
            id="allowed", x1=50, y1=170, x2=550, y2=170,
            label="initialization_failed", label_size=32, routing_padding=0,
        )
        _, bounds, (label,) = self.render_label()
        self.assertEqual(label.get("font-size"), "32")
        self.assertGreaterEqual(bounds[2] - bounds[0], 420)

    def test_c4_dual_badge_reserves_both_lines_at_the_configured_size(self) -> None:
        self.diagram.update(style=9, semantic_profile="generic")
        self.arrow.update(label_size=24, protocol="内部服务消息通道")
        group, bounds, (primary, secondary) = self.render_label()
        self.assertEqual(primary.get("font-size"), "24")
        self.assertEqual(secondary.text, "[内部服务消息通道]")
        self.assertEqual(float(secondary.get("font-size")), 17.0)
        self.assertGreater(bounds[2] - bounds[0], 8 * 17)
        badge = next(element for element in group if element.tag.endswith("}rect"))
        self.assertGreater(float(badge.get("height")), 32)
        self.assertEqual(float(badge.get("height")), bounds[3] - bounds[1])

    def test_arrow_size_rejects_non_numeric_or_non_positive_settings(self) -> None:
        for size in (0, -12, float("inf"), True, "large", None):
            with self.subTest(size=size, setting="style_overrides.arrow_label_size"):
                self.diagram["style_overrides"] = {"arrow_label_size": size}
                with self.assertRaisesRegex(ValueError, r"style_overrides\.arrow_label_size"):
                    generator.build_svg("flowchart", self.diagram)
                del self.diagram["style_overrides"]
            with self.subTest(size=size, setting="arrows[0].label_size"):
                self.arrow["label_size"] = size
                with self.assertRaisesRegex(ValueError, r"arrows\[0\]\.label_size"):
                    generator.build_svg("flowchart", self.diagram)
                del self.arrow["label_size"]


class RouteContractTest(unittest.TestCase):
    def test_collinear_port_clearance_hairpin_is_collapsed(self) -> None:
        self.assertEqual(
            generator.simplify_points([(0, 0), (20.4, 0), (19.6, 0), (40, 0)]),
            [(0, 0), (40, 0)],
        )

    def test_explicit_waypoints_are_preserved_without_diagonal_segments(self) -> None:
        route = generator.build_orthogonal_route(
            (0.0, 0.0),
            (100.0, 100.0),
            [],
            {"route_points": [[50, 50]]},
            canvas_bounds=(0.0, 0.0, 120.0, 120.0),
        )
        self.assertIn((50.0, 50.0), route)
        self.assertTrue(generator.route_is_orthogonal(route), route)

    def test_waypoint_inside_reserved_obstacle_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "waypoint.*obstacle"):
            generator.build_orthogonal_route(
                (0.0, 20.0),
                (100.0, 20.0),
                [(40.0, 0.0, 60.0, 40.0)],
                {"route_points": [[50, 20]]},
                canvas_bounds=(0.0, 0.0, 120.0, 80.0),
            )

    def test_waypoint_outside_canvas_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside the canvas"):
            generator.build_orthogonal_route(
                (10.0, 20.0),
                (100.0, 20.0),
                [],
                {"route_points": [[130, 20]]},
                canvas_bounds=(0.0, 0.0, 120.0, 80.0),
            )

    def test_existing_edge_is_avoided_when_a_clear_corridor_exists(self) -> None:
        existing = [[(50.0, 20.0), (50.0, 100.0)]]
        route = generator.build_orthogonal_route(
            (10.0, 60.0),
            (110.0, 60.0),
            [],
            {"corridor_y": [10]},
            canvas_bounds=(0.0, 0.0, 120.0, 120.0),
            existing_routes=existing,
        )
        self.assertEqual(generator.route_crossing_count(route, existing), 0, route)

    def test_layout_report_is_deterministic_and_identifies_edges(self) -> None:
        data = {
            "schema_version": 1,
            "mode": "architecture",
            "style": 1,
            "width": 420,
            "height": 240,
            "nodes": [
                {"id": "source", "x": 30, "y": 90, "width": 110, "height": 60, "label": "Source"},
                {"id": "target", "x": 280, "y": 90, "width": 110, "height": 60, "label": "Target"},
            ],
            "arrows": [
                {"id": "request", "source": "source", "target": "target", "source_port": "right", "target_port": "left"}
            ],
        }
        first_svg, first_report = generator.build_svg_with_report("architecture", data)
        second_svg, second_report = generator.build_svg_with_report("architecture", data)
        self.assertEqual(first_svg, second_svg)
        self.assertEqual(first_report, second_report)
        self.assertIn('data-graph-role="edge"', first_svg)
        self.assertEqual(first_report["schema_version"], 1)
        self.assertEqual(first_report["edges"][0]["id"], "request")
        self.assertEqual(first_report["issues"], [])

    def test_renderer_does_not_mutate_the_input(self) -> None:
        data = {
            "style": 1,
            "nodes": [
                {"id": "source", "x": 30, "auto_place": True, "width": 80, "height": 40},
                {"id": "target", "x": 220, "y": 120, "width": 80, "height": 40},
            ],
            "arrows": [{"source": "source", "target": "target"}],
        }
        before = copy.deepcopy(data)
        generator.build_svg_with_report("architecture", data)
        self.assertEqual(data, before)

    def test_sanitized_dom_ids_are_globally_unique_and_raw_ids_are_preserved(self) -> None:
        data = {
            "schema_version": 1,
            "mode": "architecture",
            "style": 1,
            "width": 900,
            "height": 420,
            "nodes": [
                {"id": "a b", "x": 40, "y": 100, "width": 120, "height": 60, "label": "A"},
                {"id": "a-b", "x": 260, "y": 100, "width": 120, "height": 60, "label": "B"},
                {"id": "中文一", "x": 480, "y": 240, "width": 120, "height": 60, "label": "C"},
                {"id": "中文二", "x": 700, "y": 240, "width": 120, "height": 60, "label": "D"},
            ],
            "arrows": [
                {"id": "edge one", "source": "a b", "target": "a-b", "source_port": "right", "target_port": "left"},
                {"id": "edge-one", "source": "中文一", "target": "中文二", "source_port": "right", "target_port": "left"},
            ],
        }
        svg, _ = generator.build_svg_with_report("architecture", data)
        root = ET.fromstring(svg)
        dom_ids = [element.get("id") for element in root.iter() if element.get("id")]
        self.assertEqual(len(dom_ids), len(set(dom_ids)))
        self.assertEqual(
            {element.get("data-node-id") for element in root if element.get("data-graph-role") == "node"},
            {"a b", "a-b", "中文一", "中文二"},
        )
        self.assertEqual(
            {element.get("data-edge-id") for element in root if element.get("data-graph-role") == "edge"},
            {"edge one", "edge-one"},
        )

    def test_all_generated_fixtures_pass_strict_geometry(self) -> None:
        for fixture in sorted((ROOT / "fixtures").glob("*.json")):
            with self.subTest(fixture=fixture.name):
                data = json.loads(fixture.read_text(encoding="utf-8"))
                template_type = data.get("template_type", "architecture")
                svg, report = generator.build_svg_with_report(template_type, data)
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "diagram.svg"
                    path.write_text(svg, encoding="utf-8")
                    ok, details = validator.run_check(path, "geometry")
                self.assertTrue(ok, details)
                self.assertTrue(report["ok"])

    def test_all_template_styles_share_the_showcase_composition_baseline(self) -> None:
        metrics = []
        for style in (1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12):
            data = json.loads(
                (ROOT / "fixtures" / "quality-baseline" / f"agent-runtime-style{style}.json").read_text(
                    encoding="utf-8"
                )
            )
            _, report = generator.build_svg_with_report("architecture", data)
            self.assertTrue(report["composition"]["ok"])
            self.assertEqual(report["composition"]["score"], 100)
            self.assertEqual(report["composition"]["metrics"]["bridged_crossings"], 0)
            self.assertFalse(report["placements"]["legend"]["moved"])
            metrics.append(report["composition"]["metrics"])
        self.assertTrue(all(item == metrics[0] for item in metrics[1:]), metrics)

    def test_showcase_profile_rejects_an_extra_bend(self) -> None:
        contract = generator.quality.resolve_contract("showcase")
        assessment = generator.quality.assess_composition(
            nodes=[],
            containers=[],
            edges=[
                {
                    "id": "zigzag",
                    "route": [(0, 0), (20, 0), (20, 20), (40, 20), (40, 40)],
                    "bends": 3,
                    "bridges": [],
                }
            ],
            contract=contract,
        )
        self.assertFalse(assessment["ok"])
        self.assertTrue(any(item["code"] == "EDGE_BEND_BUDGET" for item in assessment["violations"]))

    def test_long_node_title_is_fitted_inside_the_card(self) -> None:
        data = json.loads(
            (ROOT / "fixtures" / "quality-baseline" / "agent-runtime-style1.json").read_text(
                encoding="utf-8"
            )
        )
        svg, _ = generator.build_svg_with_report("architecture", data)
        root = ET.fromstring(svg)
        eval_group = next(element for element in root if element.get("id") == "node-eval")
        title = next(element for element in eval_group if "node-title" in element.get("class", ""))
        self.assertLess(float(title.get("font-size", "18")), 18.0)

    def test_decision_diamond_labels_fit_inside_the_sloping_outline(self) -> None:
        data = {
            "style": 1,
            "nodes": [
                {"id": "decision", "kind": "diamond", "x": 100, "y": 100, "width": 160, "height": 76,
                 "label": "是否需要人工复核当前请求", "sublabel": "审批规则和风险分类", "type_label": "REVIEW"},
            ],
        }
        svg, report = generator.build_svg_with_report("flowchart", data)
        root = ET.fromstring(svg)
        node = next(element for element in root if element.get("data-node-id") == "decision")
        polygon = next((element for element in node if element.tag.endswith("polygon")), None)
        self.assertIsNotNone(polygon, "the decision should be a polygon")
        corners = [tuple(map(float, pair.split(","))) for pair in polygon.get("points").split()]
        self.assertEqual(corners, [(180, 100), (260, 138), (180, 176), (100, 138)])
        labels = [element for element in node if element.tag.endswith("text")]
        self.assertEqual({element.get("data-text-role") for element in labels}, {"type", "title", "subtitle"})
        for label in labels:
            x, y, size = (float(label.get(key)) for key in ("x", "y", "font-size"))
            left, top, right, bottom = generator.geometry.estimate_text_bounds(
                x, y, label.text or "", font_size=size, anchor="middle",
            )
            for corner_x in (left, right):
                for corner_y in (top, bottom):
                    self.assertLess(abs(corner_x - 180) / 80 + abs(corner_y - 138) / 38, 1)
        self.assertFalse(report["typography"]["complete_text"])
        with self.assertRaisesRegex(ValueError, "TEXT_FIT"):
            generator.build_svg_with_report("flowchart", {**data, "text_policy": "strict"})

    def test_decision_diamond_routes_attach_to_vertices_and_fan_on_its_outline(self) -> None:
        data = {
            "style": 1,
            "width": 740,
            "height": 620,
            "nodes": [
                {"id": "start", "x": 300, "y": 90, "width": 100, "height": 50, "label": "Start"},
                {"id": "decision", "kind": "diamond", "x": 270, "y": 230, "width": 160, "height": 80,
                 "label": "Check?"},
                {"id": "left", "x": 50, "y": 245, "width": 100, "height": 50, "label": "Back"},
                {"id": "low", "x": 300, "y": 420, "width": 100, "height": 50, "label": "Later"},
                {"id": "upper-right", "x": 560, "y": 200, "width": 100, "height": 50, "label": "Accept"},
                {"id": "lower-right", "x": 560, "y": 310, "width": 100, "height": 50, "label": "Reject"},
            ],
            "arrows": [
                {"id": "enter", "source": "start", "target": "decision"},
                {"id": "back", "source": "decision", "target": "left", "source_port": "left"},
                {"id": "down", "source": "decision", "target": "low", "source_port": "bottom"},
                {"id": "a-yes", "source": "decision", "target": "upper-right", "source_port": "right"},
                {"id": "b-no", "source": "decision", "target": "lower-right", "source_port": "right"},
            ],
        }
        svg, report = generator.build_svg_with_report("flowchart", data)
        edges = {edge["id"]: edge for edge in report["edges"]}
        self.assertEqual(edges["enter"]["target_port"], [350, 230])
        self.assertEqual(edges["back"]["source_port"], [270, 270])
        self.assertEqual(edges["down"]["source_port"], [350, 310])
        for edge_id, expected in (("a-yes", [412, 261]), ("b-no", [412, 279])):
            route = edges[edge_id]["route"]
            self.assertEqual(route[0], expected)
            self.assertGreater(route[1][0], expected[0])
            self.assertEqual(route[1][1], expected[1])
        self.assertEqual(validator.geometry_check(ET.fromstring(svg)), [])

    def test_decision_diamond_geometry_rejects_a_connector_off_its_outline(self) -> None:
        data = {
            "style": 1,
            "nodes": [{"id": "decision", "kind": "diamond", "x": 100, "y": 100, "width": 160, "height": 80}],
            "arrows": [{"id": "exit", "source": "decision", "source_port": "right", "x2": 400, "y2": 140}],
        }
        svg, _ = generator.build_svg_with_report("flowchart", data)
        root = ET.fromstring(svg)
        edge = next(element for element in root if element.get("data-edge-id") == "exit")
        for path, code in (("M 260,120 L 400,120", "edge_anchor:"),
                           ("M 260,140 L 200,140 L 200,70 L 400,70", "edge_node:")):
            with self.subTest(path=path):
                edge.set("d", path)
                self.assertTrue(any(item.startswith(code) for item in validator.geometry_check(root)))

    def test_decision_diamond_rejects_an_unusable_title_font_size(self) -> None:
        data = {"style": 1, "nodes": [{"kind": "diamond", "x": 30, "y": 80, "label": "Check?", "title_size": -1}]}
        with self.assertRaisesRegex(ValueError, "font size"):
            generator.build_svg_with_report("flowchart", data)

    def test_decision_diamond_reflows_text_that_fits_at_smaller_sizes(self) -> None:
        for height, fields, labels in (
            (54, {"title_size": 48}, ["Check?"]),
            (62, {"type_label": "A", "sublabel": "B"}, ["A", "Check?", "B"]),
        ):
            with self.subTest(height=height):
                data = {
                    "style": 1,
                    "text_policy": "strict",
                    "nodes": [{"id": "decision", "kind": "diamond", "x": 30, "y": 80,
                               "width": 180, "height": height, "label": "Check?", **fields}],
                }
                svg, report = generator.build_svg_with_report("flowchart", data)
                root = ET.fromstring(svg)
                diamond = next(element for element in root if element.get("data-node-id") == "decision")
                text = [element for element in diamond if element.tag.endswith("text")]
                self.assertEqual([element.text for element in text], labels)
                self.assertTrue(report["typography"]["complete_text"])
                title = next(element for element in text if element.get("data-text-role") == "title")
                self.assertGreater(float(title.get("font-size")), 12)

    def test_horizontal_legend_uses_one_shared_baseline(self) -> None:
        data = json.loads(
            (ROOT / "fixtures" / "quality-baseline" / "agent-runtime-style1.json").read_text(
                encoding="utf-8"
            )
        )
        svg, _ = generator.build_svg_with_report("architecture", data)
        root = ET.fromstring(svg)
        legend = next(element for element in root if element.get("id") == "legend")
        lines = [element for element in legend if element.tag.endswith("line")]
        self.assertGreaterEqual(len(lines), 3)
        self.assertEqual({line.get("y1") for line in lines}, {"510.0"})

    def test_insufficient_port_capacity_fails_instead_of_stacking(self) -> None:
        nodes = [
            generator.normalize_node({"id": "hub", "x": 20, "y": 20, "width": 60, "height": 30}, "hub"),
            generator.normalize_node({"id": "a", "x": 180, "y": 10, "width": 40, "height": 30}, "a"),
            generator.normalize_node({"id": "b", "x": 180, "y": 60, "width": 40, "height": 30}, "b"),
            generator.normalize_node({"id": "c", "x": 180, "y": 110, "width": 40, "height": 30}, "c"),
        ]
        node_map = {node.node_id: node for node in nodes}
        arrows = [
            {"source": "hub", "target": target, "source_port": "right", "target_port": "left"}
            for target in ("a", "b", "c")
        ]
        with self.assertRaisesRegex(ValueError, "PORT_CAPACITY"):
            generator.prepare_arrows(arrows, node_map)

    def test_locked_legend_collision_fails(self) -> None:
        data = {
            "style": 1,
            "width": 420,
            "height": 240,
            "nodes": [{"id": "node", "x": 50, "y": 80, "width": 180, "height": 80}],
            "arrows": [],
            "legend": [{"flow": "control", "label": "Primary request path"}],
            "legend_x": 70,
            "legend_y": 100,
            "legend_locked": True,
        }
        with self.assertRaisesRegex(ValueError, "locked legend"):
            generator.build_svg_with_report("architecture", data)

    def test_bridge_mask_is_painted_above_crossed_edge_and_below_owner(self) -> None:
        data = {
            "schema_version": 1,
            "mode": "architecture",
            "style": 1,
            "width": 2000,
            "height": 800,
            "nodes": [
                {"id": "top", "x": 950, "y": 30, "width": 100, "height": 80},
                {"id": "bottom", "x": 950, "y": 690, "width": 100, "height": 80},
                {"id": "left", "x": 30, "y": 360, "width": 100, "height": 80},
                {"id": "right", "x": 1870, "y": 360, "width": 100, "height": 80},
            ],
            "arrows": [
                {
                    "id": "auto",
                    "source": "top",
                    "target": "bottom",
                    "source_port": "bottom",
                    "target_port": "top",
                },
                {
                    "id": "hard",
                    "source": "left",
                    "target": "right",
                    "source_port": "right",
                    "target_port": "left",
                    "route_points": [[1000, 400]],
                },
            ],
        }
        svg, report = generator.build_svg_with_report("architecture", data)
        root = ET.fromstring(svg)
        paint_order = {
            element.get("id"): index
            for index, element in enumerate(root)
            if element.get("id")
        }
        self.assertEqual(report["edges"][0]["bridges"], [[1000.0, 400.0]])
        self.assertLess(paint_order["hard"], paint_order["auto-bridge-mask"])
        self.assertLess(paint_order["auto-bridge-mask"], paint_order["auto"])
        self.assertEqual(validator.geometry_check(root), [])

    def test_blueprint_title_block_captions_fit_their_columns(self) -> None:
        data = {
            "style": 3,
            "width": 1120,
            "height": 760,
            "title": "Microservices Architecture",
            "blueprint_title_block": {
                "title": "AI MICROSERVICES",
                "subtitle": "SYSTEM ARCHITECTURE",
                "center_caption": "BLUEPRINT STYLE 3",
                "left_caption": "REV: 1.0",
                "right_caption": "DWG: ARCH-001",
                "width": 220,
                "height": 76,
                "x": 868,
                "y": 664,
            },
            "nodes": [],
            "arrows": [],
        }
        svg, _ = generator.build_svg_with_report("architecture", data)
        root = ET.fromstring(svg)
        group = next(element for element in root if element.get("id") == "blueprint-title-block")
        captions = [element for element in group if element.tag.endswith("text")][-3:]
        column_width = 220 / 3
        for index, caption in enumerate(captions):
            x = float(caption.get("x", "0"))
            y = float(caption.get("y", "0"))
            font_size = float(caption.get("font-size", "0"))
            bounds = generator.geometry.estimate_text_bounds(
                x,
                y,
                "".join(caption.itertext()),
                font_size=font_size,
                anchor="middle",
            )
            self.assertGreaterEqual(bounds[0], 868 + index * column_width + 5)
            self.assertLessEqual(bounds[2], 868 + (index + 1) * column_width - 5)
            self.assertLessEqual(bounds[3], 664 + 76)


class ArtifactGeometryTest(unittest.TestCase):
    def write_svg(self, body: str) -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        path = Path(tempdir.name) / "diagram.svg"
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 240" data-generator="fireworks-tech-graph">'
            '<defs><marker id="arrow-main"><path d="M0 0 L8 4 L0 8 Z"/></marker></defs>'
            f"{body}</svg>",
            encoding="utf-8",
        )
        return path

    def test_edge_edge_crossing_is_reported(self) -> None:
        path = self.write_svg(
            '<path id="horizontal" data-graph-role="edge" d="M 20 120 H 380" marker-end="url(#arrow-main)"/>'
            '<path id="vertical" data-graph-role="edge" d="M 200 20 V 220" marker-end="url(#arrow-main)"/>'
        )
        ok, details = validator.run_check(path, "geometry")
        self.assertFalse(ok)
        self.assertTrue(any("edge_crossing" in detail for detail in details), details)

    def test_declared_jump_over_allows_an_unavoidable_crossing(self) -> None:
        path = self.write_svg(
            '<path id="horizontal" data-graph-role="edge" d="M 20 120 H 380" marker-end="url(#arrow-main)"/>'
            '<path id="vertical-bridge-mask" data-graph-role="bridge-mask" data-owner="vertical" '
            'd="M 200 20 V 115 A 5 5 0 0 1 200 125 V 220" fill="none" stroke="#fff" stroke-width="7"/>'
            '<path id="vertical" data-graph-role="edge" data-bridges="200,120" d="M 200 20 V 115 A 5 5 0 0 1 200 125 V 220" marker-end="url(#arrow-main)"/>'
        )
        ok, details = validator.run_check(path, "geometry")
        self.assertTrue(ok, details)

    def test_declared_jump_requires_a_bridge_mask(self) -> None:
        path = self.write_svg(
            '<path id="horizontal" data-graph-role="edge" d="M 20 120 H 380" marker-end="url(#arrow-main)"/>'
            '<path id="vertical" data-graph-role="edge" data-bridges="200,120" '
            'd="M 200 20 V 115 A 5 5 0 0 1 200 125 V 220" marker-end="url(#arrow-main)"/>'
        )
        ok, details = validator.run_check(path, "geometry")
        self.assertFalse(ok)
        self.assertTrue(any("bridge_mask_missing" in detail for detail in details), details)

    def test_declared_jump_requires_effective_paint_order(self) -> None:
        path = self.write_svg(
            '<path id="vertical-bridge-mask" data-graph-role="bridge-mask" data-owner="vertical" '
            'd="M 200 20 V 115 A 5 5 0 0 1 200 125 V 220" fill="none" stroke="#fff" stroke-width="7"/>'
            '<path id="vertical" data-graph-role="edge" data-bridges="200,120" '
            'd="M 200 20 V 115 A 5 5 0 0 1 200 125 V 220" marker-end="url(#arrow-main)"/>'
            '<path id="horizontal" data-graph-role="edge" d="M 20 120 H 380" marker-end="url(#arrow-main)"/>'
        )
        ok, details = validator.run_check(path, "geometry")
        self.assertFalse(ok)
        self.assertTrue(any("bridge_paint_order" in detail for detail in details), details)

    def test_collinear_edge_overlap_is_reported(self) -> None:
        path = self.write_svg(
            '<path id="first" data-graph-role="edge" d="M 20 120 H 240" marker-end="url(#arrow-main)"/>'
            '<path id="second" data-graph-role="edge" d="M 120 120 H 380" marker-end="url(#arrow-main)"/>'
        )
        ok, details = validator.run_check(path, "geometry")
        self.assertFalse(ok)
        self.assertTrue(any("edge_overlap" in detail for detail in details), details)

    def test_route_through_reserved_legend_is_reported(self) -> None:
        path = self.write_svg(
            '<rect id="legend-zone" data-graph-role="reserved" x="250" y="150" width="130" height="70" fill="#fff"/>'
            '<path id="events" data-graph-role="edge" d="M 330 20 V 190 H 200" marker-end="url(#arrow-main)"/>'
        )
        ok, details = validator.run_check(path, "geometry")
        self.assertFalse(ok)
        self.assertTrue(any("reserved" in detail or "legend-zone" in detail for detail in details), details)

    def test_text_clipping_is_reported(self) -> None:
        path = self.write_svg('<text id="clipped" data-graph-role="label" x="392" y="40">control path</text>')
        ok, details = validator.run_check(path, "geometry")
        self.assertFalse(ok)
        self.assertTrue(any("canvas_clip" in detail for detail in details), details)


if __name__ == "__main__":
    unittest.main()
