from __future__ import annotations

import re
from typing import Any
from xml.etree import ElementTree


TRUE_VALUES = {"true", "1", "yes"}
BOUNDS_PATTERN = re.compile(
    r"^\[(?P<x1>-?\d+),(?P<y1>-?\d+)\]\[(?P<x2>-?\d+),(?P<y2>-?\d+)\]$"
)


def _as_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in TRUE_VALUES


def _bounds(value: str | None) -> dict[str, int] | None:
    match = BOUNDS_PATTERN.match(value or "")
    if not match:
        return None
    coordinates = {name: int(number) for name, number in match.groupdict().items()}
    return {
        **coordinates,
        "width": coordinates["x2"] - coordinates["x1"],
        "height": coordinates["y2"] - coordinates["y1"],
        "center_x": (coordinates["x1"] + coordinates["x2"]) // 2,
        "center_y": (coordinates["y1"] + coordinates["y2"]) // 2,
    }


def parse_hierarchy(xml_source: str) -> list[dict[str, Any]]:
    """Flatten an Appium/UiAutomator XML hierarchy into report-friendly records."""
    root = ElementTree.fromstring(xml_source)
    records: list[dict[str, Any]] = []

    def visit(node: ElementTree.Element, xpath: str, depth: int) -> None:
        attributes = dict(node.attrib)
        class_name = attributes.get("class", node.tag)
        record: dict[str, Any] = {
            "number": len(records) + 1,
            "depth": depth,
            "xpath": xpath,
            "class": class_name,
            "package": attributes.get("package", ""),
            "resource_id": attributes.get("resource-id", ""),
            "text": attributes.get("text", ""),
            "content_desc": attributes.get("content-desc", ""),
            "bounds": attributes.get("bounds", ""),
            "rect": _bounds(attributes.get("bounds")),
        }

        boolean_attributes = (
            "clickable",
            "long-clickable",
            "checkable",
            "checked",
            "enabled",
            "focusable",
            "focused",
            "scrollable",
            "selected",
            "password",
            "displayed",
        )
        for name in boolean_attributes:
            record[name.replace("-", "_")] = _as_bool(attributes.get(name))

        if record["clickable"]:
            record["interaction"] = "click"
        elif record["long_clickable"]:
            record["interaction"] = "long_click"
        elif record["scrollable"]:
            record["interaction"] = "scroll"
        elif record["checkable"]:
            record["interaction"] = "check"
        elif class_name.endswith(("EditText", "AutoCompleteTextView")):
            record["interaction"] = "type"
        else:
            record["interaction"] = ""

        known_keys = {
            "class",
            "package",
            "resource-id",
            "text",
            "content-desc",
            "bounds",
            *boolean_attributes,
        }
        record["extra_attributes"] = {
            key: value for key, value in attributes.items() if key not in known_keys
        }
        records.append(record)

        sibling_counts: dict[str, int] = {}
        for child in node:
            child_class = child.attrib.get("class", child.tag)
            sibling_counts[child_class] = sibling_counts.get(child_class, 0) + 1
            child_xpath = f"{xpath}/{child_class}[{sibling_counts[child_class]}]"
            visit(child, child_xpath, depth + 1)

    root_class = root.attrib.get("class", root.tag)
    visit(root, f"/{root_class}[1]", 0)
    return records


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    interactive = [record for record in records if record["interaction"]]
    return {
        "total_elements": len(records),
        "visible_elements": sum(record["displayed"] for record in records),
        "clickable_elements": sum(record["clickable"] for record in records),
        "long_clickable_elements": sum(record["long_clickable"] for record in records),
        "scrollable_elements": sum(record["scrollable"] for record in records),
        "checkable_elements": sum(record["checkable"] for record in records),
        "text_elements": sum(bool(record["text"]) for record in records),
        "content_description_elements": sum(
            bool(record["content_desc"]) for record in records
        ),
        "resource_id_elements": sum(bool(record["resource_id"]) for record in records),
        "interactive_element_numbers": [record["number"] for record in interactive],
    }
