"""Record the demo video of the UI with Playwright (produces demo/demo.webm).

A cursor overlay is injected into the page so clicks are visible in the recording.
For each question the script clicks several marks (so the citations panel changes)
and at the end opens the Plan & meta and Raw JSON tabs.

Usage: uvicorn app.main:app --port 8000 &  then  python demo/record_demo.py [--base http://127.0.0.1:8000]
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

QUERIES = [
    ("How has the number of trials for pembrolizumab changed per year since 2015?", {}),
    ("How are lupus trials distributed across phases?", {}),
    ("Compare phases for trials involving pembrolizumab vs nivolumab.", {}),
    ("Which countries have the most recruiting trials for lupus?", {}),
    ("Show a network of sponsors ↔ drugs for breast cancer trials.", {"status": "RECRUITING"}),
    ("Enrollment vs start year for phase 3 pembrolizumab trials", {}),
    ("How large are trials for Alzheimer's disease?", {}),
]

CURSOR_JS = """
() => {
  if (document.getElementById('__cursor')) return;
  const c = document.createElement('div'); c.id = '__cursor';
  c.style.cssText = 'position:fixed;left:-50px;top:-50px;width:22px;height:30px;z-index:2147483647;pointer-events:none;' +
    "background:url(\\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 22 30'><path d='M2 1 L2 23 L8 17 L12 28 L16 26 L12 16 L20 16 Z' fill='white' stroke='black' stroke-width='1.6'/></svg>\\") no-repeat;";
  document.body.appendChild(c);
  document.addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
  document.addEventListener('mousedown', e => {
    const r = document.createElement('div');
    r.style.cssText = `position:fixed;left:${e.clientX}px;top:${e.clientY}px;width:34px;height:34px;margin:-17px 0 0 -17px;border:3px solid #1f6feb;border-radius:50%;z-index:2147483646;pointer-events:none;opacity:.9;transition:transform .45s ease-out,opacity .45s ease-out;transform:scale(.3)`;
    document.body.appendChild(r);
    requestAnimationFrame(() => { r.style.transform = 'scale(1.3)'; r.style.opacity = '0'; });
    setTimeout(() => r.remove(), 500);
  }, true);
}
"""

TARGETS_JS = """
(type) => {
  const svg = document.querySelector('#chart svg'); if (!svg) return [];
  const center = el => { const b = el.getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2 }; };
  const inView = p => p.y > 90 && p.y < window.innerHeight - 10 && p.x > 0 && p.x < window.innerWidth;
  const pick = (arr, n) => { if (arr.length <= n) return arr; const out = []; for (let i = 0; i < n; i++) out.push(arr[Math.round(i * (arr.length - 1) / (n - 1))]); return out; };
  let els;
  if (type === 'choropleth_map') {
    els = [...svg.querySelectorAll('g.mark-shape path')].filter(p => {
      const f = p.getAttribute('fill') || ''; if (f.includes('8a949e')) return false;
      const b = p.getBBox(); const pt = svg.createSVGPoint(); pt.x = b.x + b.width / 2; pt.y = b.y + b.height / 2;
      try { return p.isPointInFill(pt) && b.width > 12; } catch (e) { return false; }
    });
    els.sort((a, b) => b.getBBox().width - a.getBBox().width);
    return pick(els.slice(0, 6), 3).map(center).filter(inView);
  }
  if (type === 'network_graph') {
    const nodes = [...svg.querySelectorAll('circle')].sort((a, b) => +b.getAttribute('r') - +a.getAttribute('r'));
    const lines = [...svg.querySelectorAll('line')].sort((a, b) => +b.getAttribute('stroke-width') - +a.getAttribute('stroke-width'));
    const pts = [center(nodes[0]), center(nodes[Math.min(3, nodes.length - 1)])];
    if (lines[0]) { const l = lines[0]; const b = l.getBoundingClientRect(); pts.push({ x: (+l.getAttribute('x1') + +l.getAttribute('x2')) / 2, y: (+l.getAttribute('y1') + +l.getAttribute('y2')) / 2, svgLocal: true, el: l }); }
    return pts.map(p => { if (!p.svgLocal) return p; const m = p.el.getScreenCTM(); const q = svg.createSVGPoint(); q.x = p.x; q.y = p.y; const s = q.matrixTransform(m); return { x: s.x, y: s.y }; }).filter(inView);
  }
  const sel = (type === 'time_series' || type === 'scatter_plot') ? 'g.mark-symbol path' : 'g.mark-rect path';
  els = [...svg.querySelectorAll(sel)];
  if (type === 'scatter_plot') els = els.filter((e, i) => i % 7 === 0);
  return pick(els, 3).map(center).filter(inView);
}
"""


def move_click(page: Page, x: float, y: float, settle_ms: int = 1900) -> None:
    page.mouse.move(x, y, steps=28)
    page.wait_for_timeout(350)
    page.mouse.down()
    page.mouse.up()
    page.wait_for_timeout(settle_ms)


def click_el(page: Page, selector: str, settle_ms: int = 2200) -> None:
    box = page.locator(selector).first.bounding_box()
    move_click(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, settle_ms)


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
        page.evaluate(CURSOR_JS)
        page.mouse.move(640, 300, steps=10)
        page.wait_for_timeout(1200)

        for query, fields in QUERIES:
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(300)
            click_el(page, "#q", 300)
            page.fill("#q", "")
            page.type("#q", query, delay=14)
            for k, v in fields.items():
                page.evaluate("([k, v]) => { document.getElementById(k).value = v; }", [k, v])
            page.wait_for_timeout(300)
            click_el(page, "#run", 200)
            page.wait_for_function("document.getElementById('status').textContent.startsWith('Done') || document.getElementById('status').classList.contains('err')", timeout=120000)
            page.wait_for_timeout(500)
            page.evaluate("document.getElementById('results').scrollIntoView({block: 'start'})")
            page.wait_for_timeout(2300 if "network" in query.lower() else 1300)
            viz_type = page.evaluate("lastResponse.visualization.type")
            for t in page.evaluate(TARGETS_JS, viz_type):
                move_click(page, t["x"], t["y"])
            for k in fields:
                page.evaluate("k => { document.getElementById(k).value = ''; }", k)

        # Show the other two tabs on the last result, then return to citations.
        click_el(page, ".tab[data-tab='plan']", 3000)
        click_el(page, ".tab[data-tab='raw']", 3000)
        click_el(page, ".tab[data-tab='cites']", 1500)
        page.wait_for_timeout(800)
        ctx.close()
        browser.close()

    video = next(tmp.glob("*.webm"))
    shutil.move(str(video), args.out)
    shutil.rmtree(tmp, ignore_errors=True)
    print("saved", args.out)


if __name__ == "__main__":
    main()
