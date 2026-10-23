"""The sample dialogs the gallery shows and the harness drives.

One entry per gallery dialog, in gallery order. Each spec is validated when
it is used, so a sample that no longer matches the spec reference fails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

APP = "Release bot"

CHART_CODE = """\
const width = 600;
const height = 260;
const margin = { top: 16, right: 16, bottom: 32, left: 40 };
const rows: { label: string; value: number }[] = data;
const x = d3
  .scaleBand()
  .domain(rows.map((row) => row.label))
  .range([margin.left, width - margin.right])
  .padding(0.2);
const y = d3
  .scaleLinear()
  .domain([0, d3.max(rows, (row) => row.value) ?? 0])
  .nice()
  .range([height - margin.bottom, margin.top]);
const svg = d3.select(root).append("svg").attr("viewBox", `0 0 ${width} ${height}`);
svg
  .append("g")
  .selectAll("rect")
  .data(rows)
  .join("rect")
  .attr("x", (row) => x(row.label) ?? 0)
  .attr("y", (row) => y(row.value))
  .attr("width", x.bandwidth())
  .attr("height", (row) => y(0) - y(row.value))
  .attr("fill", "steelblue")
  .style("cursor", "pointer")
  .on("click", function () {
    d3.select(this).transition().duration(200).attr("fill", "orange");
  });
svg.append("g").attr("transform", `translate(0,${height - margin.bottom})`).call(d3.axisBottom(x));
svg.append("g").attr("transform", `translate(${margin.left},0)`).call(d3.axisLeft(y));
"""

CHART_DATA = [
    {"label": "Mon", "value": 12},
    {"label": "Tue", "value": 19},
    {"label": "Wed", "value": 7},
    {"label": "Thu", "value": 15},
    {"label": "Fri", "value": 22},
]

SAMPLES: dict[str, dict[str, Any]] = {
    "html": {
        "verb": "html",
        "app": APP,
        "title": "Static report",
        "root": str(Path(__file__).with_name("html-sample")),
        "entry": "index.html",
        "files": [
            "index.html",
            "pages/detail.html",
            "style.css",
            "view.js",
            "data.json",
            "selected.png",
        ],
    },
    "notify": {
        "verb": "notify",
        "app": APP,
        "title": "Build finished",
        "message": "Version 2.4.0 was built and signed.",
        "detail": "The artifacts are in the release folder.",
    },
    "confirm": {
        "verb": "confirm",
        "app": APP,
        "message": "Deploy version 2.4.0 to production?",
        "detail": "This replaces the version running now.",
        "yes_label": "Deploy",
        "no_label": "Not now",
        "timeout": 30,
    },
    "pick": {
        "verb": "pick",
        "app": APP,
        "message": "Which environment?",
        "options": [
            {"label": "Staging", "id": "staging", "detail": "Shared with the QA team"},
            {"label": "Production", "id": "production"},
            {"label": "Local", "id": "local"},
        ],
    },
    "pick-multiple": {
        "verb": "pick",
        "app": APP,
        "message": "Which checks should run?",
        "multiple": True,
        "options": ["Unit tests", "Integration tests", "Lint", "Type check"],
    },
    "ask": {
        "verb": "ask",
        "app": APP,
        "title": "Release notes",
        "questions": [
            {"id": "summary", "prompt": "One line for the changelog", "multiline": True},
            {
                "id": "kind",
                "prompt": "What kind of release is this?",
                "options": ["Bug fix", "Feature", "Security"],
                "other": True,
            },
            {
                "id": "notify",
                "prompt": "Who should hear about it?",
                "detail": "Pick any that apply.",
                "kind": "multi",
                "options": ["Support", "Sales", "Customers"],
            },
        ],
    },
    "secret": {
        "verb": "secret",
        "app": APP,
        "message": "Credentials for the package registry.",
        "fields": [
            {"name": "REGISTRY_USER", "label": "User name"},
            {"name": "REGISTRY_TOKEN", "label": "Access token"},
        ],
        "store": "true",
        "store_label": "Package registry vault",
    },
    "form": {
        "verb": "form",
        "app": APP,
        "title": "New service",
        "components": [
            {"type": "heading", "text": "Basics", "level": 2},
            {"type": "text", "text": "Describe the service the bot will create."},
            {"type": "field", "id": "name", "label": "Name", "required": True},
            {
                "type": "select",
                "id": "region",
                "label": "Region",
                "options": ["us-east", "eu-west", "ap-south"],
                "required": True,
            },
            {
                "type": "card",
                "title": "Options",
                "children": [
                    {"type": "checkbox", "id": "public", "label": "Public endpoint"},
                    {
                        "type": "checkbox",
                        "id": "backups",
                        "label": "Nightly backups",
                        "checked": True,
                    },
                    {"type": "list", "items": ["Logs are kept 30 days", "Metrics are on"]},
                ],
            },
            {"type": "field", "id": "notes", "label": "Notes", "multiline": True},
        ],
    },
    "custom": {
        "verb": "custom",
        "app": APP,
        "title": "Release summary",
        "height": 220,
        "html": (
            "<h2>Version 2.4.0</h2><p>Three fixes and <b>one</b> feature.</p>"
            "<ul><li>Faster start</li><li>Smaller bundle</li></ul>"
            '<script>alert("removed")</script><a href="https://example.com">removed link</a>'
        ),
    },
    "chart": {
        "verb": "chart",
        "timeout": 30,
        "app": APP,
        "title": "Deploys this week",
        "height": 320,
        "code": CHART_CODE,
        "data": CHART_DATA,
    },
}
