"""Build a standalone static website from web/ into site/ (for GitHub Pages or any static host).

The artifact page is written without <html>/<head>/<body> (claude.ai adds them); this wraps it in a full
document with the same small CSS reset, and copies tools.js and the data files.
Run: .venv/Scripts/python scripts/build_site.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB, SITE = ROOT / "web", ROOT / "site"
RESET = (":root{color-scheme:light;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}"
         "body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}")


def public_layout(page: str) -> str:
    """The public copy has no AI: hide the ask panel (kept in the DOM so the shared script runs unchanged),
    and split the map and the calculator into two full-height columns."""
    swaps = [
        ('<section class="panel" aria-labelledby="ask-h">', '<section class="panel" aria-labelledby="ask-h" hidden>'),
        ('      <h2>Calculator, no AI needed</h2>',
         '    </section>\n\n    <section class="panel" aria-labelledby="calc-h">\n      <h2 id="calc-h">Calculator</h2>'),
        ('<p class="lede">Ask where new Instabox and Budbee lockers should go. Claude reads the question and calls planning tools that run\n'
         '      <b>in this page</b> on OpenStreetMap, SCB and Overture data for all of Sweden. Every number comes from those tools.</p>',
         '<p class="lede">See where Instabox and Budbee lockers reach people today and where new ones would reach the most. '
         'Every number is computed <b>in your browser</b> from OpenStreetMap, SCB, Overture and Paketindex data for all of Sweden.</p>'),
        ('.desk { display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr);',
         '.desk { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr);'),
    ]
    for old, new in swaps:
        if old not in page:
            raise ValueError(f"layout anchor not found: {old[:60]!r}")
        page = page.replace(old, new, 1)
    return page


def main() -> None:
    page = public_layout((WEB / "index.html").read_text(encoding="utf-8"))
    split = page.index("<style>")
    head_tags, body = page[:split], page[split:]
    html = ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">\n"
            f"<style>{RESET}</style>\n{head_tags}</head>\n<body>\n{body}\n</body>\n</html>\n")
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "data").mkdir(parents=True)
    (SITE / "index.html").write_text(html, encoding="utf-8")
    shutil.copy2(WEB / "tools.js", SITE / "tools.js")
    for f in (WEB / "data").iterdir():
        shutil.copy2(f, SITE / "data" / f.name)
    (SITE / ".nojekyll").write_text("", encoding="utf-8")  # serve files as-is
    size = sum(f.stat().st_size for f in SITE.rglob("*") if f.is_file())
    print(f"built {SITE} ({size / 1e6:.1f} MB, {sum(1 for _ in SITE.rglob('*'))} entries)")


if __name__ == "__main__":
    main()
