"""Shared look for the website, the HTML reports and the showcase scorecard.

Navy + signal orange: navy for structure, orange for attention, green/red only
for pass/fail meaning. The AROL logo (assets/arol_logo_white.png, white on
transparent, from arol.com) is always shown with DISCLAIMER, because this is a
student project for AROL, not an AROL product.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGO = ROOT / "assets" / "arol_logo_white.png"
PRODUCT = "Telemetry Analysis Console"
DISCLAIMER = "Student project for AROL - not an official AROL product"

NAVY = "#0C2340"
NAVY_2 = "#16365C"
ORANGE = "#F28C28"
GREEN = "#2E8B57"
RED = "#C0392B"
AMBER = "#D68910"
INK = "#1B2430"
MUTED = "#5F6B7A"
RULE = "#DDE3EA"
SURFACE = "#F4F6F9"
TONES = {"ok": GREEN, "warn": AMBER, "bad": RED, "neutral": NAVY_2}


@lru_cache(maxsize=1)
def logo_data_uri() -> str | None:
    """The logo as a data: URI so a single HTML file carries it."""
    if not LOGO.is_file():
        return None
    return "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode("ascii")


def header_html(title: str = PRODUCT, subtitle: str = "") -> str:
    """Navy header band with the logo, a title and the disclaimer."""
    logo = logo_data_uri()
    img = f'<img src="{logo}" alt="AROL logo" class="arol-logo">' if logo else ""
    sub = f'<div class="arol-sub">{subtitle}</div>' if subtitle else ""
    return (f'<div class="arol-header"><div class="arol-brand">{img}'
            f'<div><div class="arol-title">{title}</div>{sub}</div></div>'
            f'<div class="arol-disclaimer">{DISCLAIMER}</div></div>')


HEADER_CSS = f"""
.arol-header {{ background:linear-gradient(90deg,{NAVY} 0%,{NAVY_2} 100%); color:#fff;
  border-radius:10px; padding:18px 24px; display:flex; align-items:center;
  justify-content:space-between; gap:16px; flex-wrap:wrap; margin-bottom:14px; }}
.arol-brand {{ display:flex; align-items:center; gap:20px; }}
.arol-logo {{ height:46px; }}
.arol-title {{ font-size:1.45rem; font-weight:700; letter-spacing:.01em; }}
.arol-sub {{ color:#C9D6E6; font-size:.9rem; margin-top:2px; }}
.arol-disclaimer {{ color:#C9D6E6; font-size:.75rem; border:1px solid rgba(255,255,255,.25);
  border-radius:999px; padding:4px 10px; }}
.kpi-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin:8px 0 16px; }}
.kpi {{ background:#fff; border:1px solid {RULE}; border-radius:10px; padding:12px 14px;
  border-top:4px solid var(--tone,{NAVY_2}); }}
.kpi-label {{ color:{MUTED}; font-size:.72rem; text-transform:uppercase; letter-spacing:.08em; }}
.kpi-value {{ color:{INK}; font-size:1.45rem; font-weight:700; margin-top:2px; font-variant-numeric:tabular-nums; }}
.kpi-note {{ color:{MUTED}; font-size:.8rem; margin-top:2px; }}
.pill {{ display:inline-block; border-radius:999px; padding:3px 12px; font-size:.8rem; font-weight:600; color:#fff; }}
.interp {{ background:#FFF4E8; border:1px solid #F7C99A; border-radius:8px; padding:8px 12px;
  color:{INK}; font-size:.88rem; margin:6px 0 10px; }}
"""


def kpi_cards_html(figures) -> str:
    """figures: dicts with label, value, note, tone."""
    cards = "".join(
        f'<div class="kpi" style="--tone:{TONES.get(f.get("tone"), NAVY_2)}">'
        f'<div class="kpi-label">{f["label"]}</div><div class="kpi-value">{f["value"]}</div>'
        f'<div class="kpi-note">{f.get("note", "")}</div></div>'
        for f in figures)
    return f'<div class="kpi-grid">{cards}</div>' if cards else ""


def status_pill(status: str) -> str:
    label, tone = {"ok": ("Analysis completed", "ok"),
                   "needs_clarification": ("Clarification needed", "warn"),
                   "partial": ("Partially completed", "warn"),
                   "degraded": ("Analysis failed", "bad")}.get(status, (status, "neutral"))
    return f'<span class="pill" style="background:{TONES[tone]}">{label}</span>'
