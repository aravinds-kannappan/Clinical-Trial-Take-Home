"""Record a short demo video of the UI with Playwright (used to produce demo/demo.webm).

Usage: uvicorn app.main:app --port 8000 &  then  python demo/record_demo.py [--base http://127.0.0.1:8000]
"""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

QUERIES = [
    ("How has the number of trials for pembrolizumab changed per year since 2015?", {}),
    ("How are lupus trials distributed across phases?", {}),
    ("Compare phases for trials involving pembrolizumab vs nivolumab.", {}),
    ("Which countries have the most recruiting trials for lupus?", {}),
    ("Show a network of sponsors ↔ drugs for breast cancer trials.", {"status": "RECRUITING"}),
    ("Enrollment vs start year for phase 3 pembrolizumab trials", {}),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--out", default=str(Path(__file__).parent / "demo.webm"))
    args = ap.parse_args()
    tmp = Path(__file__).parent / "_video"
    tmp.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 860}, record_video_dir=str(tmp), record_video_size={"width": 1280, "height": 860}, color_scheme="light")
        page = ctx.new_page()
        page.goto(args.base + "/")
        page.wait_for_selector("#q")
        time.sleep(1.5)
        for query, fields in QUERIES:
            page.fill("#q", "")
            page.type("#q", query, delay=18)
            for k, v in fields.items():
                page.evaluate("([k, v]) => { document.getElementById(k).value = v; }", [k, v])
            page.click("#run")
            page.wait_for_function("document.getElementById('status').textContent.startsWith('Done') || document.getElementById('status').classList.contains('err')", timeout=120000)
            page.wait_for_timeout(600)
            page.evaluate("document.getElementById('chart').scrollIntoView({block: 'center', behavior: 'smooth'})")
            page.wait_for_timeout(2200)
            # Click a datum to show its citations.
            target = page.query_selector("#chart svg g.mark-rect path, #chart svg g.mark-symbol path, #chart svg g.mark-shape path, #chart svg circle")
            if target:
                try:
                    target.click(force=True)
                    page.wait_for_timeout(2600)
                except Exception:
                    pass
            page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
            page.wait_for_timeout(900)
            for k in fields:
                page.evaluate("k => { document.getElementById(k).value = ''; }", k)
        page.wait_for_timeout(800)
        ctx.close()
        browser.close()

    video = next(tmp.glob("*.webm"))
    shutil.move(str(video), args.out)
    shutil.rmtree(tmp, ignore_errors=True)
    print("saved", args.out)


if __name__ == "__main__":
    main()
