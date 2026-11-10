"""Small, bounded AKShare connectivity probe; no scheduler or production ingestion.

Run from any directory: python3 scripts/probe_sources.py
Each endpoint runs once in a separate process with a 35-second deadline.
Raw output is research evidence, never validated market data.
"""
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "stock_daily": ("stock_zh_a_hist", {"symbol": "000001", "period": "daily", "start_date": "20261001", "end_date": "20261109", "adjust": ""}),
    "etf_daily": ("fund_etf_hist_em", {"symbol": "510300", "period": "daily", "start_date": "20261001", "end_date": "20261109", "adjust": ""}),
    "etf_minute": ("fund_etf_hist_min_em", {"symbol": "510300", "period": "1", "adjust": ""}),
}


def child(name):
    import akshare as ak
    fn, params = CASES[name]
    frame = getattr(ak, fn)(**params)
    output = ROOT / "research" / "probes" / (name + ".csv")
    frame.to_csv(output, index=False)
    date_col = next((x for x in ["日期", "时间"] if x in frame.columns), None)
    print(json.dumps({"rows": len(frame), "columns": list(frame.columns),
                      "first_event": str(frame[date_col].min()) if date_col and len(frame) else None,
                      "last_event": str(frame[date_col].max()) if date_col and len(frame) else None,
                      "raw_file": str(output.relative_to(ROOT))}, ensure_ascii=False))


def main():
    (ROOT / "research" / "probes").mkdir(parents=True, exist_ok=True)
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
              "akshare_version": importlib.metadata.version("akshare"),
              "purpose": "Connectivity only; no freshness, coverage or unit certification", "results": []}
    for name, (fn, params) in CASES.items():
        started = time.monotonic()
        row = {"case": name, "function": fn, "parameters": params}
        try:
            proc = subprocess.run([sys.executable, __file__, "--child", name],
                                  capture_output=True, text=True, timeout=35)
            if proc.returncode:
                row.update(status="error", error=proc.stderr.strip().splitlines()[-1][:500] if proc.stderr.strip() else "No error detail")
            else:
                data = json.loads(proc.stdout.strip().splitlines()[-1])
                row.update(data, status="returned_rows" if data["rows"] else "empty")
        except subprocess.TimeoutExpired:
            row.update(status="timeout", error="35-second process deadline")
        except Exception as exc:
            row.update(status="error", error=type(exc).__name__ + ": " + str(exc)[:300])
        row["elapsed_seconds"] = round(time.monotonic() - started, 2)
        report["results"].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    (ROOT / "research" / "probes" / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        child(sys.argv[2])
    else:
        main()
