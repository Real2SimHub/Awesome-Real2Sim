"""Build README.md and the figures from the files in data/.

    python scripts/build.py          # write everything
    python scripts/build.py --check  # exit 1 if any generated file is out of date

Inputs: data/taxonomy.json, data/works.json, data/institutions.json (with the logos in
assets/institutions/), data/milestones.json, data/resources.json and scripts/readme_template.md.
Outputs: README.md, assets/figures/<name>-light.svg and <name>-dark.svg, and site/index.html.
Other files in assets/figures/ (a figure drawn by hand, such as roadmap.svg) are not
generated: the build never writes or removes them, and the README may only point to files that exist.
Only the standard library is used.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import math
import re
import struct
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FIGURES = ROOT / "assets" / "figures"
ROADMAP = FIGURES / "roadmap.svg"  # drawn by hand; its cards are titled "name · institution · month · category"
TEMPLATE = ROOT / "scripts" / "readme_template.md"
README = ROOT / "README.md"
SITE = ROOT / "site" / "index.html"

FONT = "-apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif"
KINDS = ("method", "benchmark", "dataset", "survey")
LEVELS = ("yes", "partial", "no")
COMMON = ("id", "name", "title", "year", "venue", "kind", "category", "institution", "summary", "how")
DETAILED = ("input", "evaluation", "eval_types", "generalization")

# Tile gradients per group, after Real2SimLab's blue-to-violet.
GRADIENTS = {
    "method": ("#2563eb", "#7c3aed"),
    "benchmark": ("#f59e0b", "#ea580c"),
    "dataset": ("#06b6d4", "#2563eb"),
    "survey": ("#475569", "#1f2328"),
    "adjacent": ("#94a3b8", "#64748b"),
}


class DataError(Exception):
    """The data files are inconsistent; the message says where."""


@dataclass(frozen=True)
class Theme:
    name: str
    text: str
    muted: str
    surface: str
    row: str
    line: str
    accent: str
    soft: str


LIGHT = Theme("light", "#1f2328", "#59636e", "#ffffff", "#f6f8fa", "#d0d7de", "#6d28d9", "#eef2ff")
DARK = Theme("dark", "#e6edf3", "#9198a1", "#0d1117", "#161b22", "#3d444d", "#a78bfa", "#1e1b4b")


# --------------------------------------------------------------------------- data


def load() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Read and validate the taxonomy, the works and the institutions."""
    taxonomy = json.loads((DATA / "taxonomy.json").read_text(encoding="utf-8"))
    works = json.loads((DATA / "works.json").read_text(encoding="utf-8"))
    institutions = json.loads((DATA / "institutions.json").read_text(encoding="utf-8"))
    validate_institutions(institutions)
    validate(taxonomy, works, institutions)
    return taxonomy, works, institutions


def validate_institutions(institutions: dict[str, Any]) -> None:
    """Every institution needs a homepage and a square PNG logo under assets/institutions/."""
    for name, record in institutions.items():
        where = f"institutions.json {name!r}"
        for key in ("url", "logo", "logo_source"):
            if not record.get(key):
                raise DataError(f"{where}: missing {key}")
        path = ROOT / record["logo"]
        if not path.is_file() or path.parent != ROOT / "assets" / "institutions" or path.suffix != ".png":
            raise DataError(f"{where}: logo must be an existing PNG in assets/institutions/")
        with path.open("rb") as stream:
            header = stream.read(24)
        if len(header) != 24 or not header.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR"):
            raise DataError(f"{where}: invalid PNG header")
        width, height = struct.unpack(">II", header[16:24])
        if width != height or width < 96:
            raise DataError(f"{where}: logo must be square and at least 96 px")


def validate(taxonomy: dict[str, Any], works: list[dict[str, Any]], institutions: dict[str, Any]) -> None:
    """Raise DataError on the first problem in works.json."""
    categories = {c["key"]: c for c in taxonomy["categories"]}
    groups = {g["key"]: g for g in taxonomy["groups"]}
    eval_keys = {e["key"] for e in taxonomy["eval_types"]}
    builders = {b["key"] for b in taxonomy["builders"]}
    seen: set[str] = set()
    for index, work in enumerate(works):
        where = f"works.json entry {index} ({work.get('id', '?')})"
        required = COMMON + (() if work.get("kind") == "survey" else DETAILED)
        missing = [key for key in required if key not in work or work[key] in (None, "")]
        if missing:
            raise DataError(f"{where}: missing {', '.join(missing)}")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", work["id"]):
            raise DataError(f"{where}: id must be a lowercase slug")
        if work["id"] in seen:
            raise DataError(f"{where}: duplicate id")
        seen.add(work["id"])
        category = categories.get(work["category"])
        if category is None:
            raise DataError(f"{where}: unknown category {work['category']!r}")
        if work["kind"] not in groups[category["group"]]["kinds"]:
            raise DataError(f"{where}: kind {work['kind']!r} does not fit {work['category']!r}")
        subs = category.get("subcategories")
        if subs and work.get("subcategory") not in subs:
            raise DataError(f"{where}: subcategory must be one of {subs}")
        if work["kind"] == "method" and work.get("builder_tag") not in builders:
            raise DataError(f"{where}: builder_tag must be one of {sorted(builders)}")
        unknown = set(work.get("eval_types") or []) - eval_keys
        if unknown:
            raise DataError(f"{where}: unknown eval_types {sorted(unknown)}")
        if work["kind"] != "survey" and work["generalization"].get("level") not in LEVELS:
            raise DataError(f"{where}: generalization.level must be one of {LEVELS}")
        if not any(work.get(key) for key in ("paper", "code", "project")):
            raise DataError(f"{where}: needs at least one of paper, code, project")
        if work["institution"] not in institutions:
            raise DataError(f"{where}: institution {work['institution']!r} is not in institutions.json")


def by_category(taxonomy: dict[str, Any], works: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Works grouped by category key, in taxonomy order, newest first."""
    groups: dict[str, list[dict[str, Any]]] = {c["key"]: [] for c in taxonomy["categories"]}
    for work in works:
        groups[work["category"]].append(work)
    for items in groups.values():
        items.sort(key=lambda w: (-w["year"], w["name"].lower()))
    return groups


def group_of(taxonomy: dict[str, Any]) -> dict[str, str]:
    return {c["key"]: c["group"] for c in taxonomy["categories"]}


# --------------------------------------------------------------------------- drawing helpers


def esc(text: Any) -> str:
    return html.escape(str(text), quote=True)


def text(x: float, y: float, value: str, size: float, fill: str, weight: int = 400, anchor: str = "start",
         extra: str = "") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-family="{FONT}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}"{extra}>{esc(value)}</text>'
    )


def text_width(value: str, size: float, bold: bool = False) -> float:
    """Rough rendered width of a string in the system UI font."""
    units = 0.0
    for ch in value:
        if ch == " ":
            units += 0.28
        elif ch in "il.,:;'’|!·":
            units += 0.26
        elif ch.isupper() or ch in "mwMW@%&":
            units += 0.68
        elif ch.isdigit():
            units += 0.58
        else:
            units += 0.53
    return units * size * (1.16 if bold else 1.08)


def fit(value: str, width: float, size: float, bold: bool = False) -> str:
    """Cut text to fit a width, with an ellipsis."""
    if text_width(value, size, bold) <= width:
        return value
    while value and text_width(value + "…", size, bold) > width:
        value = value[:-1]
    return value.rstrip() + "…"


def wrap(value: str, width: float, size: float, bold: bool = False, lines: int = 2) -> list[str]:
    """Split text into at most `lines` lines that fit a width; the last one is cut with an ellipsis."""
    if lines == 1 or text_width(value, size, bold) <= width:
        return [fit(value, width, size, bold)]
    words = value.split(" ")
    for cut in range(len(words) - 1, 0, -1):
        first = " ".join(words[:cut])
        if text_width(first, size, bold) <= width:
            return [first] + wrap(" ".join(words[cut:]), width, size, bold, lines - 1)
    return [fit(value, width, size, bold)]


def gradient_defs(prefix: str) -> str:
    return "".join(
        f'<linearGradient id="{prefix}-{key}" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{a}"/><stop offset="1" stop-color="{b}"/></linearGradient>'
        for key, (a, b) in GRADIENTS.items()
    )


GLYPHS = {
    "target": '<circle cx="12" cy="12" r="7.5" {s}/><circle cx="12" cy="12" r="3" fill="#fff"/>'
              '<path d="M12 2.5V6M12 18v3.5M2.5 12H6M18 12h3.5" {s}/>',
    "sparkle": '<path d="M12 3l1.9 6.6L20.5 12l-6.6 1.9L12 20.5l-1.9-6.6L3.5 12l6.6-2.4z" fill="#fff"/>'
               '<circle cx="19" cy="5" r="1.6" fill="#fff"/>',
    "hinge": '<rect x="4.5" y="4" width="15" height="16" rx="2" {s}/><path d="M12 4v16" {s}/>'
             '<circle cx="12" cy="8.5" r="1.6" fill="#fff"/><circle cx="12" cy="15.5" r="1.6" fill="#fff"/>',
    "scene": '<rect x="3" y="4" width="18" height="16" rx="2.5" {s}/><rect x="6" y="11" width="5" height="6" rx="1" fill="#fff"/>'
             '<circle cx="16" cy="13.5" r="2.6" fill="#fff"/><path d="M3 17.5h18" {s}/>',
    "braces": '<path d="M9 4c-3 0-2 5-4.5 8C7 15 6 20 9 20M15 4c3 0 2 5 4.5 8-2.5 3-1.5 8-4.5 8" {s}/>'
              '<circle cx="12" cy="12" r="1.5" fill="#fff"/>',
    "network": '<path d="M5.5 7.5L12 12l-6.5 4.5M12 12l6.5-4.5M12 12l6.5 4.5" {s}/>'
               '<circle cx="5.5" cy="7.5" r="2.3" fill="#fff"/><circle cx="5.5" cy="16.5" r="2.3" fill="#fff"/>'
               '<circle cx="12" cy="12" r="2.6" fill="#fff"/><circle cx="18.5" cy="7.5" r="2.3" fill="#fff"/>'
               '<circle cx="18.5" cy="16.5" r="2.3" fill="#fff"/>',
    "terminal": '<rect x="3" y="4.5" width="18" height="15" rx="2.5" {s}/><path d="M7 9.5l3 2.5-3 2.5M12.5 15h4.5" {s}/>',
    "question": '<circle cx="12" cy="12" r="8.5" {s}/><path d="M9.6 9.6a2.5 2.5 0 1 1 3.4 2.3c-.7.3-1 .8-1 1.5v.6" {s}/>'
                '<circle cx="12" cy="16.8" r="1.2" fill="#fff"/>',
    "film": '<rect x="3" y="5" width="18" height="14" rx="2.5" {s}/><path d="M10 9.2l5 2.8-5 2.8z" fill="#fff"/>',
    "scale": '<path d="M12 4v15M6 7h12M8.5 20h7" {s}/><path d="M3.5 13.5L6 7.5l2.5 6zM15.5 13.5L18 7.5l2.5 6z" {s}/>'
             '<path d="M3.5 13.5a2.5 1.6 0 0 0 5 0M15.5 13.5a2.5 1.6 0 0 0 5 0" fill="#fff"/>',
    "database": '<ellipse cx="12" cy="6" rx="7" ry="2.6" {s}/><path d="M5 6v12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6V6" {s}/>'
                '<path d="M5 12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6" {s}/>',
    "book": '<path d="M12 6.5C9.6 5 6.6 4.8 4 5.6v13c2.6-.8 5.6-.6 8 .9 2.4-1.5 5.4-1.7 8-.9v-13c-2.6-.8-5.6-.6-8 .9z" {s}/>'
            '<path d="M12 6.5v13" {s}/>',
    "text": '<path d="M5 6.5h14M5 12h10M5 17.5h12" {s}/>',
}


def glyph(name: str, x: float, y: float, size: float) -> str:
    stroke = 'fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
    body = GLYPHS[name].replace("{s}", stroke)
    return f'<g transform="translate({x:.1f},{y:.1f}) scale({size / 24:.4f})">{body}</g>'


def tile(x: float, y: float, size: float, fill: str, glyph_name: str | None = None, label: str | None = None) -> str:
    out = f'<rect x="{x:.1f}" y="{y:.1f}" width="{size:.1f}" height="{size:.1f}" rx="{size * 0.24:.1f}" fill="{fill}"/>'
    if glyph_name:
        inner = size * 0.6
        out += glyph(glyph_name, x + (size - inner) / 2, y + (size - inner) / 2, inner)
    if label:
        font = size * (0.42 if len(label) <= 2 else 0.33)
        out += (
            f'<text x="{x + size / 2:.1f}" y="{y + size / 2:.1f}" dy="0.35em" text-anchor="middle" font-family="{FONT}" '
            f'font-size="{font:.1f}" font-weight="700" fill="#ffffff">{esc(label)}</text>'
        )
    return out


def svg(width: float, height: float, body: str, defs: str = "") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}"><defs>{defs}</defs>{body}</svg>\n'
    )


# --------------------------------------------------------------------------- work icons


def data_uri(path: Path) -> str:
    raw = path.read_bytes()
    mime = "image/jpeg" if raw[:3] == b"\xff\xd8\xff" else "image/png"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def logo_id(name: str) -> str:
    return "logo-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def logo_defs(names: set[str], institutions: dict[str, Any]) -> str:
    """Each institution logo once, as a symbol that icons reuse."""
    return "".join(
        f'<symbol id="{logo_id(name)}" viewBox="0 0 1 1"><image href="{data_uri(ROOT / institutions[name]["logo"])}" '
        f'width="1" height="1" preserveAspectRatio="xMidYMid meet"/></symbol>'
        for name in sorted(names)
    )


def work_icon(work: dict[str, Any], x: float, y: float, size: float, theme: Theme) -> str:
    """The logo of the work's first institution on a white rounded tile."""
    pad = size * 0.1
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{size}" height="{size}" rx="{size * 0.24:.1f}" fill="#ffffff" '
        f'stroke="{theme.line}" stroke-width="1"/>'
        f'<use href="#{logo_id(work["institution"])}" x="{x + pad:.1f}" y="{y + pad:.1f}" '
        f'width="{size - 2 * pad:.1f}" height="{size - 2 * pad:.1f}"/>'
    )


# --------------------------------------------------------------------------- header card

# Material Design Icons (Apache-2.0), 24 x 24, the icon set Real2SimLab's figures use.
MDI = {
    "cube-scan": "M17,22V20H20V17H22V20.5C22,20.89 21.84,21.24 21.54,21.54C21.24,21.84 20.89,22 20.5,22H17M7,22H3.5C3.11,22 2.76,21.84 2.46,21.54C2.16,21.24 2,20.89 2,20.5V17H4V20H7V22M17,2H20.5C20.89,2 21.24,2.16 21.54,2.46C21.84,2.76 22,3.11 22,3.5V7H20V4H17V2M7,2V4H4V7H2V3.5C2,3.11 2.16,2.76 2.46,2.46C2.76,2.16 3.11,2 3.5,2H7M13,17.25L17,14.95V10.36L13,12.66V17.25M12,10.92L16,8.63L12,6.28L8,8.63L12,10.92M7,14.95L11,17.25V12.66L7,10.36V14.95M18.23,7.59C18.73,7.91 19,8.34 19,8.91V15.23C19,15.8 18.73,16.23 18.23,16.55L12.75,19.73C12.25,20.05 11.75,20.05 11.25,19.73L5.77,16.55C5.27,16.23 5,15.8 5,15.23V8.91C5,8.34 5.27,7.91 5.77,7.59L11.25,4.41C11.5,4.28 11.75,4.22 12,4.22C12.25,4.22 12.5,4.28 12.75,4.41L18.23,7.59Z",
    "podium": "M12,7.09L14.45,8.58L13.8,5.77L16,3.89L13.11,3.64L12,1L10.87,3.64L8,3.89L10.18,5.77L9.5,8.58L12,7.09M4,13.09L6.45,14.58L5.8,11.77L8,9.89L5.11,9.64L4,7L2.87,9.64L0,9.89L2.18,11.77L1.5,14.58L4,13.09M20,10.09L22.45,11.58L21.8,8.77L24,6.89L21.11,6.64L20,4L18.87,6.64L16,6.89L18.18,8.77L17.5,11.58L20,10.09M15,23H9V10H15V23M7,23H1V17H7V23M23,23H17V13H23V23Z",
    "database-outline": "M12 3C7.58 3 4 4.79 4 7V17C4 19.21 7.59 21 12 21S20 19.21 20 17V7C20 4.79 16.42 3 12 3M18 17C18 17.5 15.87 19 12 19S6 17.5 6 17V14.77C7.61 15.55 9.72 16 12 16S16.39 15.55 18 14.77V17M18 12.45C16.7 13.4 14.42 14 12 14C9.58 14 7.3 13.4 6 12.45V9.64C7.47 10.47 9.61 11 12 11C14.39 11 16.53 10.47 18 9.64V12.45M12 9C8.13 9 6 7.5 6 7S8.13 5 12 5C15.87 5 18 6.5 18 7S15.87 9 12 9Z",
    "book-open-page-variant-outline": "M19 1L14 6V17L19 12.5V1M21 5V18.5C19.9 18.15 18.7 18 17.5 18C15.8 18 13.35 18.65 12 19.5V6C10.55 4.9 8.45 4.5 6.5 4.5C4.55 4.5 2.45 4.9 1 6V20.65C1 20.9 1.25 21.15 1.5 21.15C1.6 21.15 1.65 21.1 1.75 21.1C3.1 20.45 5.05 20 6.5 20C8.45 20 10.55 20.4 12 21.5C13.35 20.65 15.8 20 17.5 20C19.15 20 20.85 20.3 22.25 21.05C22.35 21.1 22.4 21.1 22.5 21.1C22.75 21.1 23 20.85 23 20.6V6C22.4 5.55 21.75 5.25 21 5M10 18.41C8.75 18.09 7.5 18 6.5 18C5.44 18 4.18 18.19 3 18.5V7.13C3.91 6.73 5.14 6.5 6.5 6.5C7.86 6.5 9.09 6.73 10 7.13V18.41Z",
    "map-outline": "M20.5,3L20.34,3.03L15,5.1L9,3L3.36,4.9C3.15,4.97 3,5.15 3,5.38V20.5A0.5,0.5 0 0,0 3.5,21L3.66,20.97L9,18.9L15,21L20.64,19.1C20.85,19.03 21,18.85 21,18.62V3.5A0.5,0.5 0 0,0 20.5,3M10,5.47L14,6.87V18.53L10,17.13V5.47M5,6.46L8,5.45V17.15L5,18.31V6.46M19,17.54L16,18.55V6.86L19,5.7V17.54Z",
}

CARD_THEMES = {
    "light": {"card": "#eef2ff", "edge": "#4f46e5", "text": "#1f2328", "muted": "#59636e", "accent": "#6d28d9"},
    "dark": {"card": "#1e2550", "edge": "#a5b4fc", "text": "#e6edf3", "muted": "#9198a1", "accent": "#a78bfa"},
}


def logo_svg(theme: Theme, cx: float, cy: float, width: float) -> str:
    """assets/logo-<theme>.svg nested at the given centre and width, without its <title>."""
    path = ROOT / "assets" / f"logo-{theme.name}.svg"
    if not path.is_file():
        raise SystemExit(f"error: {path.relative_to(ROOT)} is missing\n  fix: copy the logo from the Real2SimHub brand kit "
                         "(assets/brand/svg/awesome-real2sim-logo[-dark].svg in Real2SimHub.github.io)")
    src = path.read_text(encoding="utf-8").strip()
    box = re.search(r'viewBox="([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+)"', src)
    if not box or not src.endswith("</svg>"):
        raise SystemExit(f"error: {path.relative_to(ROOT)} is not a single <svg> with a viewBox\n  fix: copy it again from the brand kit")
    vw, vh = float(box[3]), float(box[4])
    inner = re.sub(r"<title>.*?</title>", "", src[src.index(">") + 1:-len("</svg>")], flags=re.S)
    height = width * vh / vw
    return (f'<svg x="{cx - width / 2:.1f}" y="{cy - height / 2:.1f}" width="{width:.1f}" height="{height:.1f}" '
            f'viewBox="{box[1]} {box[2]} {box[3]} {box[4]}">{inner}</svg>')


def card_body(taxonomy: dict[str, Any], works: list[dict[str, Any]], theme: Theme, x0: float, y0: float) -> str:
    """The centre card of the overview, on the grid of Real2SimLab's card: title, subtitle, five icon rows."""
    t = CARD_THEMES[theme.name]
    groups = group_of(taxonomy)
    count = Counter(groups[w["category"]] for w in works)
    kinds = {g: sum(1 for c in taxonomy["categories"] if c["group"] == g) for g in ("method", "benchmark")}
    rows = [
        ("cube-scan", "Methods", f"{kinds['method']} kinds, by what they deliver"),
        ("podium", "Benchmarks", f"{kinds['benchmark']} kinds, by what is handed in"),
        ("database-outline", "Datasets", "physics, assets, real–sim pairs"),
        ("book-open-page-variant-outline", "Surveys", "the field and its neighbours"),
        ("map-outline", "Map", "every work, its paper and code"),
    ]
    body = [f'<rect x="{x0}" y="{y0}" width="380" height="300" rx="14" fill="{t["card"]}" stroke="{t["edge"]}" stroke-width="1.6"/>']
    # The project's logo from the Real2SimHub family (assets/logo-<theme>.svg), as in the README header.
    body.append(logo_svg(theme, x0 + 190, y0 + 33, 290))
    body.append(text(x0 + 190, y0 + 70, f"{len(works)} works · one taxonomy · one map", 12.5, t["muted"], 400, "middle"))
    for i, (icon_name, label, note) in enumerate(rows):
        y = y0 + 89 + 40 * i
        body.append(
            f'<svg x="{x0 + 20}" y="{y}" width="30" height="30" viewBox="0 0 48 48"><rect width="48" height="48" rx="11" '
            f'fill="#4f46e5"/><g fill="#ffffff" transform="translate(10 10) scale(1.16667)"><path d="{MDI[icon_name]}"/></g></svg>'
        )
        body.append(text(x0 + 62, y + 20, label, 15, t["text"], 700))
        body.append(text(x0 + 166, y + 20, note, 12.5, t["muted"]))
    return "".join(body)


# --------------------------------------------------------------------------- overview figure


def render_overview(taxonomy: dict[str, Any], groups: dict[str, list[dict[str, Any]]], theme: Theme,
                    works: list[dict[str, Any]]) -> str:
    """The hero figure, on the layout of Real2SimLab's teaser: methods, the list, benchmarks and resources."""
    prefix = f"o{theme.name}"
    width, panel_w, row_h, pitch = 1280.0, 380.0, 44.0, 50.0
    left_x, card_x, right_x = 20.0, 450.0, 880.0
    line = "#a5b4fc" if theme is LIGHT else "#6366f1"
    cats = taxonomy["categories"]
    left = [c for c in cats if c["group"] == "method"]
    right = [c for c in cats if c["group"] in ("benchmark", "dataset", "survey")]
    top = 4.0
    panel_h = 50 + max(len(left), len(right)) * pitch
    mid = top + panel_h / 2
    defs = [gradient_defs(prefix),
            f'<marker id="arrow-{theme.name}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" '
            f'orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{line}"/></marker>']
    curves: list[str] = []
    body: list[str] = []

    def panel(x: float, label: str, items: list[dict[str, Any]], side: str) -> None:
        body.append(f'<rect x="{x}" y="{top}" width="{panel_w}" height="{panel_h}" rx="12" fill="{theme.surface}" '
                    f'stroke="{theme.line}"/>')
        body.append(text(x + 16, top + 28, label, 14, theme.muted, 700))
        for i, cat in enumerate(items):
            ry = top + 44 + i * pitch
            cy = ry + row_h / 2
            if side == "left":
                curves.append(f'<path d="M{x + panel_w},{cy} C{x + panel_w + 40},{cy} {card_x - 40},{mid} {card_x},{mid}" '
                              f'fill="none" stroke="{line}" stroke-width="1.4" stroke-opacity="0.8"/>')
            else:
                curves.append(f'<path d="M{card_x + 380},{mid} C{card_x + 420},{mid} {x - 42},{cy} {x - 2},{cy}" '
                              f'fill="none" stroke="{line}" stroke-width="1.4" stroke-opacity="0.8" '
                              f'marker-end="url(#arrow-{theme.name})"/>')
            body.append(f'<rect x="{x + 10}" y="{ry}" width="{panel_w - 20}" height="{row_h}" rx="9" fill="{theme.row}"/>')
            body.append(tile(x + 18, ry + 6, 32, f"url(#{prefix}-{cat['group']})", cat["glyph"]))
            body.append(text(x + 60, ry + 19, cat["short"], 14.5, theme.text, 650))
            body.append(text(x + 60, ry + 35, fit(cat["tagline"], panel_w - 60 - 90, 11.5), 11.5, theme.muted))
            body.append(text(x + panel_w - 22, ry + 27, f"{len(groups[cat['key']])} works", 12, theme.muted, 400, "end"))

    panel(left_x, "BUILD · METHODS", left, "left")
    panel(right_x, "MEASURE · BENCHMARKS & RESOURCES", right, "right")
    body.append(card_body(taxonomy, works, theme, card_x, mid - 150))
    return svg(width, top + panel_h + 16, "".join(curves) + "".join(body), "".join(defs))


# --------------------------------------------------------------------------- map figures


def render_map(taxonomy: dict[str, Any], groups: dict[str, list[dict[str, Any]]], institutions: dict[str, Any],
               theme: Theme, group_keys: tuple[str, ...]) -> str:
    """Every work of the chosen groups as a chip, one band per category."""
    prefix = f"m{theme.name}{group_keys[0][:2]}"
    width, label_w, cols, chip_h, gap, pad = 1120.0, 230.0, 4, 36.0, 8.0, 12.0
    panel_x = 20 + label_w + 24
    panel_w = width - panel_x - 20
    chip_w = (panel_w - 2 * pad - (cols - 1) * gap) / cols
    body: list[str] = []
    used: set[str] = set()
    y = 16.0
    for cat in taxonomy["categories"]:
        works = groups[cat["key"]]
        if cat["group"] not in group_keys or not works:
            continue
        subs = cat.get("subcategories") or [None]
        blocks = [(s, [w for w in works if s is None or w.get("subcategory") == s]) for s in subs]
        blocks = [(s, ws) for s, ws in blocks if ws]
        height = pad
        for sub, ws in blocks:
            height += (22 if sub else 0) + math.ceil(len(ws) / cols) * (chip_h + gap)
        height += pad - gap
        body.append(f'<rect x="{panel_x:.1f}" y="{y:.1f}" width="{panel_w:.1f}" height="{height:.1f}" rx="14" '
                    f'fill="{theme.surface}" stroke="{theme.line}" stroke-width="1.2"/>')
        center = y + min(height / 2, 48)
        name, _, qualifier = cat["short"].partition(" · ")
        body.append(f'<rect x="20" y="{center - 44:.1f}" width="{label_w:.1f}" height="88" rx="14" fill="{theme.row}"/>')
        body.append(tile(32, center - 22, 44, f"url(#{prefix}-{cat['group']})", cat["glyph"]))
        lines = wrap(name, label_w - 80, 15.5, True)
        shift = 9 if len(lines) == 2 else 0
        lift = 0 if qualifier else 8
        for k, line in enumerate(lines):
            body.append(text(88, center - 9 - shift + lift + k * 18, line, 15.5, theme.text, 700))
        center += shift
        years = f"{works[-1]['year']}–{works[0]['year']}" if works[-1]["year"] != works[0]["year"] else str(works[0]["year"])
        if qualifier:
            body.append(text(88, center + 10, qualifier, 12.5, theme.accent, 700))
        body.append(text(88, center + (27 if qualifier else 18), f"{len(works)} works · {years}", 12, theme.muted, 500))
        body.append(f'<path d="M{20 + label_w:.1f},{center:.1f} H{panel_x:.1f}" stroke="{theme.line}" stroke-width="1.5"/>')
        cy = y + pad
        for sub, ws in blocks:
            if sub:
                body.append(text(panel_x + pad + 2, cy + 14, sub.upper(), 11.5, theme.muted, 700, extra=' letter-spacing="0.8"'))
                cy += 22
            for i, work in enumerate(sorted(ws, key=lambda w: (w["year"], w["name"].lower()))):
                cx = panel_x + pad + (i % cols) * (chip_w + gap)
                ry = cy + (i // cols) * (chip_h + gap)
                body.append(f'<rect x="{cx:.1f}" y="{ry:.1f}" width="{chip_w:.1f}" height="{chip_h}" rx="9" fill="{theme.row}"/>')
                body.append(f'<g><title>{esc(work["name"])} · {esc(work["institution"])}</title>')
                body.append(work_icon(work, cx + 7, ry + 7, 22, theme))
                used.add(work["institution"])
                body.append(text(cx + 37, ry + 23, fit(work["name"], chip_w - 37 - 32, 13, True), 13, theme.text, 600))
                body.append(text(cx + chip_w - 9, ry + 23, f"’{str(work['year'])[2:]}", 11.5, theme.muted, 500, "end"))
                body.append("</g>")
            cy += math.ceil(len(ws) / cols) * (chip_h + gap)
        y += height + 16
    return svg(width, y + 4, "".join(body), gradient_defs(prefix) + logo_defs(used, institutions))


# --------------------------------------------------------------------------- taxonomy figure


def step_badge(x: float, y: float, number: int, theme: Theme) -> str:
    """A violet circle with a white step number."""
    return (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="11" fill="{theme.accent}"/>'
            f'<text x="{x:.1f}" y="{y:.1f}" dy="0.35em" text-anchor="middle" font-family="{FONT}" font-size="12" '
            f'font-weight="700" fill="#ffffff">{number}</text>')


def answer_pill(x: float, y: float, answer: str, theme: Theme) -> str:
    """A small 'yes' / 'no' label centred on (x, y)."""
    return (f'<rect x="{x - 17:.1f}" y="{y - 10:.1f}" width="34" height="20" rx="10" fill="{theme.surface}" '
            f'stroke="{theme.line}" stroke-width="1"/>'
            + text(x, y + 4.5, answer, 11.5, theme.muted, 700, "middle"))


def arrow_defs(prefix: str, theme: Theme) -> str:
    return (f'<marker id="{prefix}-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{theme.muted}"/></marker>')


def category_card(x: float, y: float, w: float, h: float, cat: dict[str, Any], count: int, prefix: str,
                  theme: Theme) -> str:
    """A category: icon tile, short name, what it delivers, and the number of works."""
    out = [f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="12" fill="{theme.row}" '
           f'stroke="{theme.line}" stroke-width="1"/>']
    out.append(tile(x + 10, y + (h - 34) / 2, 34, f"url(#{prefix}-{cat['group']})", cat["glyph"]))
    count_w = 46
    body_w = w - 54 - count_w - 8
    lines = wrap(cat["delivers"], body_w, 11.5, lines=3)
    top = y + h / 2 - 7.5 * len(lines)
    out.append(text(x + 54, top + 2, fit(cat["short"], body_w, 14, True), 14, theme.text, 700))
    for k, line in enumerate(lines):
        out.append(text(x + 54, top + 19 + k * 14.5, line, 11.5, theme.muted, 500))
    out.append(text(x + w - 12, y + h / 2 + 1, str(count), 17, theme.accent, 700, "end"))
    out.append(text(x + w - 12, y + h / 2 + 15, "works", 10.5, theme.muted, 500, "end"))
    return "".join(out)


def render_taxonomy(taxonomy: dict[str, Any], groups: dict[str, list[dict[str, Any]]], theme: Theme) -> str:
    """How a work finds its category: the method questions as a flowchart, then the benchmark categories."""
    prefix = f"t{theme.name}"
    cats = {c["key"]: c for c in taxonomy["categories"]}
    group_info = {g["key"]: g for g in taxonomy["groups"]}

    def target(key: str) -> tuple[dict[str, Any], int]:
        """A category, or a whole group (adjacent directions) drawn like one."""
        if key in cats:
            return cats[key], len(groups[key])
        members = [c for c in taxonomy["categories"] if c["group"] == key]
        if not members:
            raise DataError(f"taxonomy.json questions: unknown target {key!r}")
        card = {"group": key, "glyph": members[0]["glyph"], "short": group_info[key]["title"],
                "delivers": group_info[key]["subtitle"]}
        return card, sum(len(groups[c["key"]]) for c in members)

    width = 1120.0
    q_x, q_w, q_h, pitch = 64.0, 460.0, 46.0, 84.0
    card_x, card_h = q_x + q_w + 92, 72.0
    card_w = width - 20 - card_x
    body = [text(20, 28, "METHODS", 12, theme.accent, 700, extra=' letter-spacing="1"'),
            text(104, 28, "Follow the arrows to find the category for each method.", 12.5, theme.muted, 500)]
    start_y = 46.0
    body.append(f'<rect x="{q_x + q_w / 2 - 54:.1f}" y="{start_y:.1f}" width="108" height="28" rx="14" '
                f'fill="url(#{prefix}-method)"/>')
    body.append(text(q_x + q_w / 2, start_y + 18.5, "A work", 13, "#ffffff", 700, "middle"))
    y = start_y + 28 + 30
    previous_bottom = start_y + 28
    questions = taxonomy["questions"]
    for i, q in enumerate(questions):
        cy = y + q_h / 2
        stay = "no" if q["exit"] == "yes" else "yes"
        body.append(f'<path d="M{q_x + q_w / 2:.1f},{previous_bottom:.1f} V{y - 2:.1f}" stroke="{theme.muted}" '
                    f'stroke-width="1.5" marker-end="url(#{prefix}-arrow)"/>')
        if i > 0:
            body.append(answer_pill(q_x + q_w / 2, (previous_bottom + y) / 2, stay_prev, theme))
        body.append(f'<rect x="{q_x:.1f}" y="{y:.1f}" width="{q_w:.1f}" height="{q_h:.1f}" rx="12" '
                    f'fill="{theme.surface}" stroke="{theme.accent}" stroke-width="1.4"/>')
        body.append(step_badge(q_x + 24, cy, i + 1, theme))
        body.append(text(q_x + 46, cy + 5, q["ask"], 14, theme.text, 600))
        card, count = target(q["to"])
        body.append(f'<path d="M{q_x + q_w:.1f},{cy:.1f} H{card_x - 2:.1f}" stroke="{theme.muted}" stroke-width="1.5" '
                    f'marker-end="url(#{prefix}-arrow)"/>')
        body.append(answer_pill((q_x + q_w + card_x) / 2, cy, q["exit"], theme))
        body.append(category_card(card_x, cy - card_h / 2, card_w, card_h, card, count, prefix, theme))
        if q.get("otherwise"):
            other, other_count = target(q["otherwise"])
            oy = cy + pitch
            body.append(f'<path d="M{q_x + q_w / 2:.1f},{y + q_h:.1f} V{oy:.1f} H{card_x - 2:.1f}" fill="none" '
                        f'stroke="{theme.muted}" stroke-width="1.5" marker-end="url(#{prefix}-arrow)"/>')
            body.append(answer_pill((q_x + q_w + card_x) / 2, oy, stay, theme))
            body.append(category_card(card_x, oy - card_h / 2, card_w, card_h, other, other_count, prefix, theme))
            y = oy + card_h / 2
        else:
            previous_bottom = y + q_h
            y += pitch
        stay_prev = stay

    y += 40
    body.append(text(20, y, "BENCHMARKS", 12, theme.accent, 700, extra=' letter-spacing="1"'))
    body.append(text(135, y, "Sorted by what the system under test must hand in. Data without a protocol is a dataset.",
                     12.5, theme.muted, 500))
    y += 16
    bench = [c for c in taxonomy["categories"] if c["group"] in ("benchmark", "dataset")]
    gap = 10.0
    w = (width - 40 - gap * (len(bench) - 1)) / len(bench)
    h = 144.0
    for i, cat in enumerate(bench):
        x = 20 + i * (w + gap)
        body.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="12" fill="{theme.row}" '
                    f'stroke="{theme.line}" stroke-width="1"/>')
        body.append(tile(x + 12, y + 12, 32, f"url(#{prefix}-{cat['group']})", cat["glyph"]))
        body.append(text(x + w - 12, y + 34, str(len(groups[cat["key"]])), 17, theme.accent, 700, "end"))
        body.append(text(x + 12, y + 64, fit(cat["short"], w - 24, 13.5, True), 13.5, theme.text, 700))
        for k, line in enumerate(wrap(cat["delivers"], w - 24, 11.5, lines=4)):
            body.append(text(x + 12, y + 82 + k * 15, line, 11.5, theme.muted, 500))
    y += h
    return svg(width, y + 12, "".join(body), gradient_defs(prefix) + arrow_defs(prefix, theme))


# --------------------------------------------------------------------------- matrix and evaluation figures


def render_matrix(taxonomy: dict[str, Any], groups: dict[str, list[dict[str, Any]]], theme: Theme) -> str:
    """How each kind of deliverable is built: methods by category x builder tag."""
    prefix = f"x{theme.name}"
    cats = [c for c in taxonomy["categories"] if c["group"] == "method"]
    builders = taxonomy["builders"]
    left, top, col, row = 290.0, 96.0, 128.0, 56.0
    width = left + col * len(builders) + 20
    height = top + row * len(cats) + 40
    body: list[str] = []
    llm = next(i for i, b in enumerate(builders) if b["key"] == "LLM / agent")
    body.append(f'<rect x="{left + llm * col + 4:.1f}" y="8" width="{col - 8:.1f}" height="{top + row * len(cats) - 4:.1f}" '
                f'rx="12" fill="{theme.accent}" fill-opacity="0.08" stroke="{theme.accent}" stroke-opacity="0.5" stroke-width="1.2"/>')
    for i, b in enumerate(builders):
        x = left + i * col + col / 2
        words = b["label"].split(" ")
        lines = [" ".join(words[:-1]), words[-1]] if len(words) > 1 else ["", words[0]]
        for j, line in enumerate(lines):
            if line:
                body.append(text(x, top - 40 + j * 18, line, 13.5, theme.text, 700, "middle"))
    counts = Counter((w["category"], w["builder_tag"]) for c in cats for w in groups[c["key"]])
    biggest = max(counts.values()) if counts else 1
    for r, cat in enumerate(cats):
        y = top + r * row
        if r % 2 == 0:
            body.append(f'<rect x="12" y="{y:.1f}" width="{width - 24:.1f}" height="{row:.1f}" rx="10" fill="{theme.row}"/>')
        body.append(tile(22, y + 10, 36, f"url(#{prefix}-method)", cat["glyph"]))
        name, _, qualifier = cat["short"].partition(" · ")
        if qualifier:
            body.append(text(70, y + 25, qualifier, 12.5, theme.accent, 700))
        body.append(text(70, y + (43 if qualifier else 34), fit(name, left - 80, 15, True), 15, theme.text, 700))
        for i, b in enumerate(builders):
            n = counts.get((cat["key"], b["key"]), 0)
            cx, cy = left + i * col + col / 2, y + row / 2
            if n == 0:
                body.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="2.5" fill="{theme.muted}" fill-opacity="0.5"/>')
                continue
            radius = 9 + 13 * math.sqrt(n / biggest)
            body.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{radius:.1f}" fill="url(#{prefix}-method)"/>')
            body.append(text(cx, cy + 5, str(n), 14, "#ffffff", 700, "middle"))
    body.append(text(20, height - 14, "Number of works per kind of deliverable (rows) and way of building it (columns).",
                     12.5, theme.muted))
    return svg(width, height, "".join(body), gradient_defs(prefix))


def render_evaluation(taxonomy: dict[str, Any], groups: dict[str, list[dict[str, Any]]], theme: Theme) -> str:
    """Which kinds of evaluation each method and benchmark category reports."""
    prefix = f"e{theme.name}"
    evals = taxonomy["eval_types"]
    cats = [c for c in taxonomy["categories"] if c["group"] in ("method", "benchmark") and groups[c["key"]]]
    left, top, col, row = 290.0, 130.0, 80.0, 46.0
    width = left + col * len(evals) + 20
    height = top + row * len(cats) + 40
    body: list[str] = []
    for i, ev in enumerate(evals):
        x = left + i * col + col / 2 - 4
        body.append(
            f'<text transform="translate({x:.1f},{top - 14:.1f}) rotate(-38)" font-family="{FONT}" font-size="13.5" '
            f'font-weight="600" fill="{theme.text}">{esc(ev["label"])}</text>'
        )
    for r, cat in enumerate(cats):
        works = groups[cat["key"]]
        y = top + r * row
        if r % 2 == 0:
            body.append(f'<rect x="12" y="{y:.1f}" width="{width - 24:.1f}" height="{row:.1f}" rx="10" fill="{theme.row}"/>')
        body.append(tile(22, y + 8, 30, f"url(#{prefix}-{cat['group']})", cat["glyph"]))
        body.append(text(64, y + 28, fit(cat["short"], left - 110, 14, True), 14, theme.text, 700))
        body.append(text(left - 16, y + 28, str(len(works)), 12.5, theme.muted, 500, "end"))
        seen = Counter(t for w in works for t in w["eval_types"])
        for i, ev in enumerate(evals):
            cx, cy = left + i * col + col / 2, y + row / 2
            share = seen.get(ev["key"], 0) / len(works)
            if share == 0:
                body.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="2.2" fill="{theme.muted}" fill-opacity="0.5"/>')
            else:
                body.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{3 + 15 * math.sqrt(share):.1f}" '
                            f'fill="url(#{prefix}-{cat["group"]})" fill-opacity="0.9"/>')
    body.append(text(20, height - 14, "Circle area: share of the category's works that report this kind of evaluation.",
                     12.5, theme.muted))
    return svg(width, height, "".join(body), gradient_defs(prefix))


# --------------------------------------------------------------------------- milestones and resources


def load_extra(works: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The hand-curated milestones and resources, checked against works.json."""
    milestones = json.loads((DATA / "milestones.json").read_text(encoding="utf-8"))["milestones"]
    resources = json.loads((DATA / "resources.json").read_text(encoding="utf-8"))
    ids = {w["id"]: w for w in works}
    out = []
    for index, item in enumerate(milestones):
        work = ids.get(item.get("id"))
        if work is None:
            raise DataError(f"milestones.json entry {index}: unknown work id {item.get('id')!r}")
        if not item.get("keyword"):
            raise DataError(f"milestones.json entry {index} ({item['id']}): missing keyword")
        out.append({**work, "keyword": item["keyword"]})
    if not ROADMAP.is_file():
        raise DataError(f"no {ROADMAP.relative_to(ROOT)}, the roadmap figure")
    drawn = set(re.findall(r"<title>([^<]*?) · ", html.unescape(ROADMAP.read_text(encoding="utf-8"))))
    if differ := sorted(drawn ^ {w["name"] for w in out}):
        raise DataError(f"milestones.json and {ROADMAP.relative_to(ROOT)} name different works: {', '.join(differ)}")
    for index, sim in enumerate(resources.get("simulators", [])):
        if not all(sim.get(k) for k in ("name", "match", "url", "note")):
            raise DataError(f"resources.json simulators entry {index}: needs name, match, url, note")
    for index, item in enumerate(resources.get("news", [])):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item.get("date", "")) or not item.get("text"):
            raise DataError(f"resources.json news entry {index}: needs a YYYY-MM-DD date and text")
    return sorted(out, key=lambda w: (w["year"], w["name"].lower())), resources


def milestone_table(taxonomy: dict[str, Any], milestones: list[dict[str, Any]]) -> str:
    cats = {c["key"]: c for c in taxonomy["categories"]}
    lines = ["| Year | Work | Category | Why it matters | Links |", "|:-:|---|---|---|:-:|"]
    for w in milestones:
        target = w.get("project") or w.get("paper") or w.get("code")
        lines.append(f"| {w['year']} | [**{cell(w['name'])}**]({target}) <sub>{venue(w)}</sub> | "
                     f"{cell(cats[w['category']]['short'])} | {cell(w['keyword'])} | {links(w)} |")
    return "\n".join(lines)


def news_list(resources: dict[str, Any]) -> str:
    return "\n".join(f"- **{n['date']}** — {n['text']}" for n in resources.get("news", []))


def simulators_table(resources: dict[str, Any], works: list[dict[str, Any]]) -> str:
    lines = ["| Simulator | Used by | What it simulates |", "|---|:-:|---|"]
    for sim in resources.get("simulators", []):
        n = sum(1 for w in works if any(m.lower() in (w.get("simulator") or "").lower() for m in sim["match"]))
        lines.append(f"| [**{cell(sim['name'])}**]({sim['url']}) | {n} | {cell(sim['note'])} |")
    return "\n".join(lines)


SITE_FIELDS = ("id", "name", "title", "year", "venue", "kind", "category", "physics", "builder_tag",
               "institution", "paper", "code", "project", "summary")


def render_site(taxonomy: dict[str, Any], works: list[dict[str, Any]]) -> str:
    """A single-file page that filters and searches the list (for GitHub Pages or opening locally)."""
    data = {
        "categories": [{k: c[k] for k in ("key", "group", "title", "short")} for c in taxonomy["categories"]],
        "groups": [{k: g[k] for k in ("key", "title")} for g in taxonomy["groups"]],
        "works": [{k: w.get(k) for k in SITE_FIELDS} for w in works],
        "updated": taxonomy["updated"],
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return SITE_TEMPLATE.replace("__DATA__", payload)


SITE_TEMPLATE = r"""<!doctype html>
<!-- Generated by scripts/build.py from data/. Do not edit by hand. -->
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Awesome-Real2Sim · Browse</title>
<style>
:root{--ink:#1f2328;--muted:#59636e;--line:#d0d7de;--row:#f6f8fa;--bg:#ffffff;--acc:#6d28d9;--soft:#eef2ff;--m1:#2563eb;--m2:#7c3aed;--b1:#f59e0b;--b2:#ea580c}
@media (prefers-color-scheme:dark){:root{--ink:#e6edf3;--muted:#9198a1;--line:#3d444d;--row:#161b22;--bg:#0d1117;--acc:#a78bfa;--soft:#1e1b4b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
header{padding:28px 16px 8px;max-width:1180px;margin:0 auto}h1{margin:0;font-size:26px}h1 b{background:linear-gradient(90deg,var(--m1),var(--m2));-webkit-background-clip:text;background-clip:text;color:transparent}
.sub{color:var(--muted);margin:4px 0 0}
.bar{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line)}
.bar>div{max-width:1180px;margin:0 auto;padding:12px 16px;display:flex;flex-direction:column;gap:8px}
input[type=search]{width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:10px;background:var(--bg);color:var(--ink);font-size:15px}
.row{display:flex;flex-wrap:wrap;gap:6px;align-items:center}.lab{font-size:12px;font-weight:700;color:var(--muted);min-width:76px}
.chip{border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:999px;padding:3px 10px;font-size:13px;cursor:pointer}
.chip.on{background:var(--soft);border-color:var(--acc);color:var(--acc);font-weight:600}
main{max-width:1180px;margin:0 auto;padding:12px 16px 40px}.count{color:var(--muted);font-size:13px;margin:4px 0 12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.card{border:1px solid var(--line);border-radius:12px;padding:12px 14px;display:flex;flex-direction:column;gap:6px;background:var(--bg)}
.card .top{display:flex;justify-content:space-between;gap:8px;align-items:baseline}.card .n{font-weight:700;font-size:16px}
.card .v{font-size:12px;color:var(--muted);white-space:nowrap}.card .t{font-size:13px;color:var(--muted)}
.tag{display:inline-block;font-size:11.5px;border-radius:6px;padding:1px 7px;margin:0 4px 2px 0;background:var(--row);color:var(--muted)}
.tag.c{color:#fff;background:linear-gradient(90deg,var(--m1),var(--m2))}.tag.c.benchmark{background:linear-gradient(90deg,var(--b1),var(--b2))}
.tag.c.dataset{background:linear-gradient(90deg,#06b6d4,#2563eb)}.tag.c.survey,.tag.c.adjacent{background:#64748b}
.links a{font-size:12.5px;margin-right:10px;color:var(--acc);text-decoration:none}.links a:hover{text-decoration:underline}
input[type=range]{width:160px}
</style></head><body>
<header><h1>Awesome-<b>Real2Sim</b> · Browse</h1><p class="sub">Filter and search every work in the list. Updated <span id="upd"></span>.</p></header>
<div class="bar"><div>
<input id="q" type="search" placeholder="Search name, title, institution, summary…">
<div class="row"><span class="lab">Group</span><span id="groups" class="row"></span></div>
<div class="row"><span class="lab">Category</span><span id="cats" class="row"></span></div>
<div class="row"><span class="lab">Physics</span><span id="phys" class="row"></span></div>
<div class="row"><span class="lab">Built by</span><span id="builders" class="row"></span></div>
<div class="row"><span class="lab">Since</span><input id="year" type="range"><span id="yearv"></span>
<label style="margin-left:16px;font-size:13px"><input id="code" type="checkbox"> has code</label></div>
</div></div>
<main><div class="count" id="count"></div><div class="grid" id="grid"></div></main>
<script>
const D=__DATA__;
const cats=Object.fromEntries(D.categories.map(c=>[c.key,c]));
const st={q:"",groups:new Set(),cats:new Set(),phys:new Set(),builders:new Set(),year:0,code:false};
document.getElementById("upd").textContent=D.updated;
function chips(el,items,set){el.innerHTML="";for(const [k,label] of items){const b=document.createElement("button");b.className="chip";b.textContent=label;
b.onclick=()=>{set.has(k)?set.delete(k):set.add(k);b.classList.toggle("on");render()};el.appendChild(b)}}
chips(document.getElementById("groups"),D.groups.map(g=>[g.key,g.title]),st.groups);
chips(document.getElementById("cats"),D.categories.map(c=>[c.key,c.short]),st.cats);
const phys=[...new Set(D.works.flatMap(w=>w.physics||[]))].sort();chips(document.getElementById("phys"),phys.map(p=>[p,p]),st.phys);
const bl=[...new Set(D.works.map(w=>w.builder_tag).filter(Boolean))].sort();chips(document.getElementById("builders"),bl.map(b=>[b,b]),st.builders);
const ys=D.works.map(w=>w.year||0).filter(Boolean),yr=document.getElementById("year");yr.min=Math.min(...ys);yr.max=Math.max(...ys);yr.value=yr.min;st.year=+yr.min;
const yv=document.getElementById("yearv");yv.textContent=yr.value;yr.oninput=()=>{st.year=+yr.value;yv.textContent=yr.value;render()};
document.getElementById("code").onchange=e=>{st.code=e.target.checked;render()};
document.getElementById("q").oninput=e=>{st.q=e.target.value.toLowerCase();render()};
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function ok(w){const c=cats[w.category];
if(st.groups.size&&!st.groups.has(c.group))return false;if(st.cats.size&&!st.cats.has(w.category))return false;
if(st.phys.size&&!(w.physics||[]).some(p=>st.phys.has(p)))return false;if(st.builders.size&&!st.builders.has(w.builder_tag))return false;
if((w.year||0)<st.year)return false;if(st.code&&!w.code)return false;
if(st.q){const h=[w.name,w.title,w.institution,w.summary,w.venue].join(" ").toLowerCase();if(!h.includes(st.q))return false}return true}
function render(){const ws=D.works.filter(ok).sort((a,b)=>(b.year||0)-(a.year||0)||a.name.localeCompare(b.name));
document.getElementById("count").textContent=ws.length+" of "+D.works.length+" works";
document.getElementById("grid").innerHTML=ws.map(w=>{const c=cats[w.category];const l=[];
if(w.paper)l.push(`<a href="${esc(w.paper)}">paper</a>`);if(w.code)l.push(`<a href="${esc(w.code)}">code</a>`);if(w.project)l.push(`<a href="${esc(w.project)}">page</a>`);
return `<div class="card"><div class="top"><span class="n">${esc(w.name)}</span><span class="v">${esc(w.venue)} · ${esc(w.year)}</span></div>
<div><span class="tag c ${c.group}">${esc(c.short)}</span>${(w.physics||[]).map(p=>`<span class="tag">${esc(p)}</span>`).join("")}${w.builder_tag?`<span class="tag">${esc(w.builder_tag)}</span>`:""}</div>
<div class="t">${esc(w.summary)}</div><div class="t" style="font-size:12px">${esc(w.institution)}</div><div class="links">${l.join("")}</div></div>`}).join("")}
render();
</script></body></html>
"""


# --------------------------------------------------------------------------- README


def slug(heading: str) -> str:
    """GitHub's anchor for a heading (text only)."""
    value = heading.strip().lower()
    value = re.sub(r"[^\w\- ]", "", value)
    return value.replace(" ", "-")


def cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def nobreak(value: str) -> str:
    return cell(value).replace(" ", "&nbsp;")


def badge(label: str, color: str, url: str, logo: str, height: int, logo_color: str = "white") -> str:
    return (f'<a href="{esc(url)}"><img src="https://img.shields.io/badge/-{label}-{color}?logo={logo}'
            f'&logoColor={logo_color}" height="{height}" alt="{label}"></a>')


def links(work: dict[str, Any], *, height: int = 26) -> str:
    """Paper, code and project page as badges stacked in one narrow column, as in Real2SimLab's README."""
    out = []
    if work.get("paper"):
        logo = "arxiv" if "arxiv.org" in work["paper"] else "readthedocs"
        out.append(badge("paper", "b31b1b", work["paper"], logo, height))
    if work.get("code"):
        out.append(badge("code", "181717", work["code"], "github", height))
    project = work.get("project")
    if project and "huggingface.co" in project:
        out.append(badge("data", "ffcc4d", project, "huggingface", height, "black"))
    elif project and work["kind"] == "dataset":
        out.append(badge("data", "4285f4", project, "icloud", height))
    elif project:
        out.append(badge("page", "4f46e5", project, "googlechrome", height))
    return "<br>".join(out)


def venue_label(work: dict[str, Any]) -> str:
    value = work["venue"]
    if value.strip().lower() == "arxiv":
        value = f"arXiv {work['year']}"
    return value


def venue(work: dict[str, Any]) -> str:
    out = f"**{nobreak(venue_label(work))}**"
    if work.get("institution"):
        out += f" · {cell(work['institution'])}"
    return out


def io_line(work: dict[str, Any], key: str) -> str:
    source = ", ".join(work.get("input") or []) or "—"
    target = work.get(key) or work.get("output") or work.get("target_output") or "—"
    return f"{source} → {target}"


def details(works: list[dict[str, Any]]) -> str:
    items = [f"- **{w['name']}** — {w['how']}" for w in works]
    return "<details>\n<summary><b>How each work does it</b></summary>\n\n" + "\n".join(items) + "\n\n</details>"


def entry_description(work: dict[str, Any], group: str) -> str:
    if group == "dataset":
        return " · ".join(str(work[k]) for k in ("real_or_synthetic", "scale") if work.get(k)) or "—"
    if group == "survey":
        return work.get("scope") or "—"
    return io_line(work, "target_output" if group == "benchmark" else "output")


def entries_table(works: list[dict[str, Any]], group: str) -> str:
    title, description = {
        "method": ("Work", "Input → Output"),
        "benchmark": ("Benchmark", "Input → Output"),
        "dataset": ("Dataset", "Contents"),
        "survey": ("Survey", "Scope"),
        "adjacent": ("Work", "Input → Output"),
    }[group]
    lines = [
        "<table>",
        f'<thead><tr><th width="34%">{esc(title)}</th><th width="50%">{esc(description)}</th>'
        '<th width="16%">Links</th></tr></thead>',
        "<tbody>",
    ]
    for work in works:
        target = work.get("project") or work.get("paper") or work["code"]
        metadata = " · ".join(filter(None, (venue_label(work), work.get("institution"))))
        lines.append(
            f'<tr><td><a href="{esc(target)}"><b>{esc(work["name"])}</b></a>'
            f'<br><sub>{esc(metadata)}</sub></td>'
            f'<td>{esc(entry_description(work, group))}</td>'
            f'<td align="center">{links(work, height=30)}</td></tr>'
        )
    lines.extend(["</tbody>", "</table>"])
    return "\n".join(lines)


def heading(cat: dict[str, Any], works: list[dict[str, Any]]) -> str:
    return cat["title"]


def sections(taxonomy: dict[str, Any], groups: dict[str, list[dict[str, Any]]], group_key: str) -> str:
    blocks = []
    for cat in taxonomy["categories"]:
        works = groups[cat["key"]]
        if cat["group"] != group_key or not works:
            continue
        parts = [f"### {heading(cat, works)}", f"> **Delivers:** {cat['delivers']}.<br>\n> {cat['description']}"]
        subs = cat.get("subcategories") or [None]
        for sub in subs:
            ws = [w for w in works if sub is None or w.get("subcategory") == sub]
            if not ws:
                continue
            if sub:
                parts.append(f"#### {sub}")
            parts.append(entries_table(ws, group_key))
        if group_key in ("method", "benchmark"):
            parts.append(details(works))
        parts.append('<p align="right"><a href="#-contents">back to contents ↑</a></p>')
        blocks.append("\n\n".join(parts))
    return "\n\n".join(blocks)


def toc(taxonomy: dict[str, Any], groups: dict[str, list[dict[str, Any]]]) -> str:
    lines = []
    for group in taxonomy["groups"]:
        cats = [c for c in taxonomy["categories"] if c["group"] == group["key"] and groups[c["key"]]]
        title = f"{group['emoji']} {group['title']}"
        lines.append(f"- [{title}](#{slug(title)})")
        if len(cats) > 1:
            for c in cats:
                lines.append(f"  - [{c['title']}](#{slug(heading(c, groups[c['key']]))}) "
                             f"<sub>{len(groups[c['key']])}</sub>")
    return "\n".join(lines)


def taxonomy_table(taxonomy: dict[str, Any], groups: dict[str, list[dict[str, Any]]]) -> str:
    lines = ["| Category | Delivers | Works |", "|---|---|:-:|"]
    for cat in taxonomy["categories"]:
        if cat["group"] not in ("method", "benchmark", "dataset") or not groups[cat["key"]]:
            continue
        lines.append(f"| [{cat['title']}](#{slug(heading(cat, groups[cat['key']]))}) | "
                     f"{cell(cat['delivers'])} | {len(groups[cat['key']])} |")
    return "\n".join(lines)


def badges(taxonomy: dict[str, Any], works: list[dict[str, Any]]) -> str:
    kinds = Counter(w["kind"] for w in works)
    adjacent = {c["key"] for c in taxonomy["categories"] if c["group"] == "adjacent"}
    methods = sum(1 for w in works if w["kind"] == "method" and w["category"] not in adjacent)
    items = [
        ("methods", str(methods), "4f46e5", "bookstack"),
        ("benchmarks", str(kinds["benchmark"]), "ea580c", "speedtest"),
        ("datasets", str(kinds["dataset"]), "0891b2", "databricks"),
        ("surveys", str(kinds["survey"]), "475569", "readthedocs"),
        ("updated", taxonomy["updated"].replace("-", "--"), "1f2328", "github"),
    ]
    return "\n".join(
        f"![{k}](https://img.shields.io/badge/{k}-{v}-{c}?logo={logo}&logoColor=white)" for k, v, c, logo in items
    )


def render_readme(taxonomy: dict[str, Any], works: list[dict[str, Any]],
                  milestones: list[dict[str, Any]], resources: dict[str, Any]) -> str:
    groups = by_category(taxonomy, works)
    replacements = {
        "NEWS": news_list(resources),
        "MILESTONES": milestone_table(taxonomy, milestones),
        "SIMULATORS": simulators_table(resources, works),
        "BADGES": badges(taxonomy, works),
        "TOC": toc(taxonomy, groups),
        "TAXONOMY_TABLE": taxonomy_table(taxonomy, groups),
        "N_WORKS": str(len(works)),
        "METHODS": sections(taxonomy, groups, "method"),
        "BENCHMARKS": sections(taxonomy, groups, "benchmark"),
        "DATASETS": sections(taxonomy, groups, "dataset"),
        "SURVEYS": sections(taxonomy, groups, "survey"),
        "ADJACENT": sections(taxonomy, groups, "adjacent"),
        "BUILDERS": "\n".join(f"- **{b['label']}**: {b['description']}." for b in taxonomy["builders"]),
    }
    value = TEMPLATE.read_text(encoding="utf-8")
    for key, replacement in replacements.items():
        value = value.replace("{{" + key + "}}", replacement)
    leftover = re.findall(r"\{\{[A-Z_]+\}\}", value)
    if leftover:
        raise DataError(f"readme_template.md has unknown placeholders: {leftover}")
    return value


# --------------------------------------------------------------------------- main


def outputs() -> dict[Path, str]:
    taxonomy, works, institutions = load()
    milestones, resources = load_extra(works)
    groups = by_category(taxonomy, works)
    files: dict[Path, str] = {}
    for theme in (LIGHT, DARK):
        files[FIGURES / f"overview-{theme.name}.svg"] = render_overview(taxonomy, groups, theme, works)
        files[FIGURES / f"map-methods-{theme.name}.svg"] = render_map(taxonomy, groups, institutions, theme, ("method", "adjacent"))
        files[FIGURES / f"map-benchmarks-{theme.name}.svg"] = render_map(
            taxonomy, groups, institutions, theme, ("benchmark", "dataset", "survey"))
        files[FIGURES / f"taxonomy-{theme.name}.svg"] = render_taxonomy(taxonomy, groups, theme)
        files[FIGURES / f"builders-{theme.name}.svg"] = render_matrix(taxonomy, groups, theme)
        files[FIGURES / f"evaluation-{theme.name}.svg"] = render_evaluation(taxonomy, groups, theme)
    files[README] = render_readme(taxonomy, works, milestones, resources)
    files[SITE] = render_site(taxonomy, works)
    return files


def missing_assets(files: dict[Path, str], stale: list[Path]) -> list[str]:
    """The local files the README shows (images, figures) that neither exist nor are generated,
    or that the build would remove."""
    links = re.findall(r'(?:src|srcset)="([^"]+)"|!\[[^\]]*\]\(([^)\s]+)', files[README])
    missing = []
    for target in {a or b for a, b in links}:
        if re.match(r"^[a-z][a-z0-9+.-]*:|#", target):
            continue  # a URL or an anchor
        path = ROOT / target.split("#", 1)[0]
        if path not in files and (not path.is_file() or path in stale):
            missing.append(target)
    return sorted(missing)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only report out-of-date files")
    args = parser.parse_args()
    try:
        files = outputs()
    except DataError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    themed = {p for theme in (LIGHT, DARK) for p in FIGURES.glob(f"*-{theme.name}.svg")}
    stale = sorted(themed - set(files))  # generated figures no longer generated; hand-drawn ones stay
    missing = missing_assets(files, stale)
    if missing:
        print(f"error: README.md points to files that do not exist or that the build removes: {', '.join(missing)}",
              file=sys.stderr)
        print("fix: add the files, or change the paths in scripts/readme_template.md", file=sys.stderr)
        return 2
    changed = [p for p, content in files.items() if not p.exists() or p.read_text(encoding="utf-8") != content]
    if args.check:
        for path in changed + stale:
            print(f"out of date: {path.relative_to(ROOT)}")
        return 1 if changed or stale else 0
    for path in changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(files[path], encoding="utf-8")
    for path in stale:
        path.unlink()
    print(f"wrote {len(changed)} files, removed {len(stale)} stale files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
