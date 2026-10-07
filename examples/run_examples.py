"""Generate the example runs in this folder by calling a running service over HTTP.

Usage:
    uvicorn app.main:app --port 8000          # in one terminal
    python examples/run_examples.py            # in another (writes examples/NN_*.json)

Options: --base-url (default http://127.0.0.1:8000), --planner auto|llm|rules.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

EXAMPLES: list[tuple[str, dict]] = [
    ("time_trend", {"query": "How has the number of trials for this drug changed per year since 2015?", "drug_name": "Pembrolizumab"}),
    ("distribution", {"query": "How are lupus trials distributed across phases?"}),
    ("comparison", {"query": "Compare sponsor categories across two conditions: psoriasis and asthma."}),
    ("geographic", {"query": "Which countries have the most recruiting trials for lupus?"}),
    ("network", {"query": "Show a network of sponsors ↔ drugs for breast cancer trials.", "status": "RECRUITING", "max_trials": 1000}),
    ("drug_network", {"query": "Which drugs frequently co-occur in combination studies for melanoma (drug ↔ drug network)?", "max_trials": 1000}),
    ("scatter", {"query": "Enrollment vs start year for phase 3 pembrolizumab trials", "max_citations_per_datum": 1}),
    ("histogram", {"query": "How large are trials for Alzheimer's disease that started in the last 5 years?"}),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--planner", default="auto", choices=["auto", "llm", "rules"])
    ap.add_argument("--citations", type=int, default=5, help="max citations per datum kept in the saved examples")
    args = ap.parse_args()
    out_dir = Path(__file__).parent
    with httpx.Client(base_url=args.base_url, timeout=180) as client:
        for i, (name, body) in enumerate(EXAMPLES, start=1):
            body = {"planner": args.planner, "max_citations_per_datum": args.citations, **body}
            r = client.post("/v1/visualize", json=body)
            path = out_dir / f"{i:02d}_{name}.json"
            payload = {"request": body, "status_code": r.status_code, "response": r.json()}
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            viz = payload["response"].get("visualization", {})
            meta = payload["response"].get("meta", {})
            size = len(viz.get("data", [])) if isinstance(viz.get("data"), list) else f"{len(viz['data']['nodes'])} nodes/{len(viz['data']['edges'])} edges" if viz else "-"
            print(f"{path.name}: {r.status_code} {viz.get('type')} | {viz.get('title')} | data={size} | planner={meta.get('planner_used')} | {meta.get('timing_ms', {}).get('total')} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
