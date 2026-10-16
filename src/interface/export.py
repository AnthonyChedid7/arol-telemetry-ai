"""Report export: Markdown, HTML, PDF - WP4.

Markdown is what the assembler produces. This converts it, so the same report
can be read in a terminal, opened in a browser, or attached to an email.

No new dependency. The Markdown subset the assembler emits is small and fixed
- headings, bullets, tables, bold, inline code, blockquotes - so a 90-line
converter is honest here where pulling in a full Markdown library would not be.
PDF goes through the browser's own print-to-PDF, which every machine already
has, rather than a reportlab/weasyprint stack that would need system libraries.
"""

from __future__ import annotations

import html as html_mod
import re
from datetime import datetime
from pathlib import Path

from . import brand

CSS = """
:root { --ink:#1B2430; --muted:#5F6B7A; --rule:#DDE3EA; --accent:#0C2340;
        --ok:#2E8B57; --warn:#D68910; --bad:#C0392B; --surface:#fff; --orange:#F28C28; }
* { box-sizing:border-box; }
body { background:#F4F6F9; color:var(--ink); margin:0; padding:28px 20px 64px;
       font:15px/1.6 "Segoe UI",-apple-system,Roboto,Helvetica,Arial,sans-serif; }
.page { max-width:960px; margin:0 auto; }
main { background:var(--surface); padding:36px 44px; border:1px solid var(--rule);
       border-radius:10px; }
h1 { font-size:1.7rem; margin:0 0 4px; letter-spacing:-.01em; color:var(--accent); }
h2 { font-size:1.12rem; margin:30px 0 12px; padding:6px 0 6px 12px; color:var(--accent);
     background:#F4F6F9; border-radius:6px; }
h3 { color:var(--accent); }
blockquote { margin:12px 0; padding:10px 14px; background:#FFF4E8;
             border:1px solid #F7C99A; border-radius:8px; }
.footer { color:var(--muted); font-size:.75rem; text-align:center; margin-top:18px; }""" + brand.HEADER_CSS + """
p, li { margin:0 0 9px; }
ul { padding-left:22px; }
ul ul { margin-top:6px; }
em { color:var(--muted); font-style:normal; }
code { background:#EDF1F5; padding:1px 5px; border-radius:3px;
       font:.87em "IBM Plex Mono",ui-monospace,Consolas,monospace; }
strong { font-weight:600; }
table { border-collapse:collapse; width:100%; margin:14px 0; font-size:.9rem;
        font-variant-numeric:tabular-nums; }
th { text-align:left; font-size:.72rem; letter-spacing:.09em;
     text-transform:uppercase; color:var(--muted); padding:0 10px 8px 0;
     border-bottom:1px solid var(--rule); }
td { padding:8px 10px 8px 0; border-bottom:1px solid var(--rule); }
figure { margin:18px 0; }
figure img { width:100%; border:1px solid var(--rule); border-radius:3px; }
figcaption { font-size:.8rem; color:var(--muted); margin-top:6px; }
hr { border:none; border-top:1px solid var(--rule); margin:28px 0; }
.meta { color:var(--muted); font-size:.85rem; margin-bottom:22px; }
@media print { body { background:#fff; padding:0; }
                main { border:none; padding:0; max-width:none; } }
"""

_INLINE = (
    (re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)"), r"<em>\1</em>"),
)


def _inline(text: str) -> str:
    out = html_mod.escape(text)
    for pattern, replacement in _INLINE:
        out = pattern.sub(replacement, out)
    return out


def markdown_to_html_body(markdown: str) -> str:
    """Convert the assembler's Markdown subset. Not a general converter."""
    lines = markdown.splitlines()
    out: list[str] = []
    i = 0
    list_depth = 0

    def close_lists(to: int = 0):
        nonlocal list_depth
        while list_depth > to:
            out.append("</ul>")
            list_depth -= 1

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            close_lists()
            i += 1
            continue

        # table: a header row followed by a |---| separator
        if stripped.startswith("|") and i + 1 < len(lines) \
                and set(lines[i + 1].strip()) <= set("|-: "):
            close_lists()
            header = [c.strip() for c in stripped.strip("|").split("|")]
            out.append("<table><thead><tr>" +
                       "".join(f"<th>{_inline(c)}</th>" for c in header) +
                       "</tr></thead><tbody>")
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>"
                                            for c in cells) + "</tr>")
                i += 1
            out.append("</tbody></table>")
            continue

        if stripped.startswith("#"):
            close_lists()
            level = len(stripped) - len(stripped.lstrip("#"))
            out.append(f"<h{level}>{_inline(stripped[level:].strip())}</h{level}>")
        elif stripped.startswith("---"):
            close_lists()
            out.append("<hr>")
        elif stripped.startswith(("- ", "* ")):
            depth = 1 + (len(line) - len(line.lstrip())) // 2
            while list_depth < depth:
                out.append("<ul>")
                list_depth += 1
            close_lists(depth)
            out.append(f"<li>{_inline(stripped[2:])}</li>")
        elif stripped.startswith(">"):
            close_lists()
            out.append(f"<blockquote>{_inline(stripped.lstrip('> '))}</blockquote>")
        else:
            close_lists()
            css = ' class="meta"' if stripped.startswith("*") else ""
            out.append(f"<p{css}>{_inline(stripped)}</p>")
        i += 1

    close_lists()
    return "\n".join(out)


def _image_src(path, embed):
    if not embed:
        return Path(path).name
    import base64
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")


def to_html(markdown: str, *, figures=None, title="Telemetry report",
            key_figures=None, embed=False) -> str:
    """Branded HTML report. embed=True puts the figures inside the file, so a
    single download can be emailed or printed; otherwise they are linked."""
    body = markdown_to_html_body(markdown)
    if figures:
        blocks = "\n".join(
            f'<figure><img src="{_image_src(p, embed)}" alt="{name}">'
            f'<figcaption>{name.replace("_", " ")}</figcaption></figure>'
            for name, p in figures
        )
        # Figures belong with the findings they illustrate.
        marker = "<h2>5. Confidence and limits</h2>"
        body = (body.replace(marker, f"{blocks}\n{marker}", 1)
                if marker in body else body + blocks)
    if key_figures:
        # The headline numbers sit under the report title, before section 1.
        cards = brand.kpi_cards_html(key_figures)
        body = body.replace("<h2>", cards + "<h2>", 1) if "<h2>" in body else cards + body
    header = brand.header_html(brand.PRODUCT, "Telemetry report")
    return (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{html_mod.escape(title)}</title><style>{CSS}</style></head>"
            f"<body><div class=\"page\">{header}<main>{body}</main>"
            f"<div class=\"footer\">{brand.DISCLAIMER}. Numbers are computed by deterministic tools "
            f"from recorded telemetry; see the limits section.</div></div></body></html>")


def save_html(markdown: str, out_dir, *, figures=None, stem=None,
              key_figures=None, embed=False) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or f"report-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    path = out_dir / f"{stem}.html"
    path.write_text(to_html(markdown, figures=figures, key_figures=key_figures, embed=embed),
                    encoding="utf-8")
    return path


def save_pdf(html_path, *, timeout=60) -> Path | None:
    """Print the HTML to PDF using a headless Chrome/Edge already installed.

    Returns None when no browser is found, rather than raising - a missing PDF
    must not cost the user the report they already have.
    """
    import shutil
    import subprocess

    html_path = Path(html_path)
    pdf_path = html_path.with_suffix(".pdf")
    candidates = [
        shutil.which("chrome"), shutil.which("msedge"), shutil.which("chromium"),
        r"C:/Program Files/Google/Chrome/Application/chrome.exe",
        r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
        "/usr/bin/chromium", "/usr/bin/google-chrome",
    ]
    browser = next((c for c in candidates if c and Path(c).exists()), None)
    if browser is None:
        return None
    try:
        subprocess.run(
            [browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
             f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()],
            capture_output=True, timeout=timeout, check=False)
    except Exception:
        return None
    return pdf_path if pdf_path.exists() else None
