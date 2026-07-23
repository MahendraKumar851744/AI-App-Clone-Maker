from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any


CSV_FIELDS = (
    "number",
    "depth",
    "interaction",
    "class",
    "text",
    "content_desc",
    "resource_id",
    "clickable",
    "long_clickable",
    "scrollable",
    "checkable",
    "checked",
    "enabled",
    "focusable",
    "focused",
    "selected",
    "bounds",
    "xpath",
    "package",
)


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def write_html(
    path: Path,
    metadata: dict[str, Any],
    summary: dict[str, Any],
    records: list[dict[str, Any]],
) -> None:
    def esc(value: Any) -> str:
        return html.escape(str(value if value is not None else ""))

    rows = "\n".join(
        "<tr>"
        f"<td>{record['number']}</td>"
        f"<td>{esc(record['interaction'])}</td>"
        f"<td>{esc(record['class'])}</td>"
        f"<td>{esc(record['text'])}</td>"
        f"<td>{esc(record['content_desc'])}</td>"
        f"<td>{esc(record['resource_id'])}</td>"
        f"<td>{esc(record['bounds'])}</td>"
        f"<td><code>{esc(record['xpath'])}</code></td>"
        "</tr>"
        for record in records
    )
    summary_cards = "\n".join(
        f"<div class='card'><strong>{esc(value)}</strong><span>{esc(key.replace('_', ' '))}</span></div>"
        for key, value in summary.items()
        if not isinstance(value, list)
    )
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Appium UI discovery report</title>
  <style>
    body {{ font: 14px system-ui, sans-serif; margin: 24px; color: #202124; }}
    h1 {{ margin-bottom: 4px; }}
    .muted {{ color: #5f6368; }}
    .grid {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 20px 0; }}
    .card {{ min-width: 130px; padding: 12px; border: 1px solid #dadce0; border-radius: 8px; }}
    .card strong, .card span {{ display: block; }}
    .card strong {{ font-size: 22px; }}
    img {{ max-width: 360px; max-height: 720px; border: 1px solid #dadce0; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
    th, td {{ border: 1px solid #dadce0; padding: 7px; text-align: left; vertical-align: top; }}
    th {{ position: sticky; top: 0; background: #f8f9fa; }}
    tr:nth-child(even) {{ background: #fafafa; }}
    code {{ font-size: 12px; word-break: break-all; }}
  </style>
</head>
<body>
  <h1>First-launch UI discovery</h1>
  <div class="muted">{esc(metadata.get('captured_at'))} ·
    {esc(metadata.get('current_package'))} · {esc(metadata.get('current_activity'))}</div>
  <div class="grid">{summary_cards}</div>
  <img src="screen.png" alt="Captured Android screen">
  <table>
    <thead><tr><th>#</th><th>Action</th><th>Class</th><th>Text</th>
      <th>Accessibility description</th><th>Resource ID</th><th>Bounds</th><th>XPath</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")
