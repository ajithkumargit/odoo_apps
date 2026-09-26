"""Manually benchmark local OCR against bill photographs without Odoo.

Usage: python tests/ocr_samples_smoke.py BILL.jpeg [OTHER_BILL.jpeg]
Images are read only; this runner prints a compact extraction summary.
"""

import argparse
import importlib.util
import json
from pathlib import Path
import time


def load_ocr():
    spec = importlib.util.spec_from_file_location(
        "smoke_local_bill_ocr", Path(__file__).parents[1] / "models" / "local_bill_ocr.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summary(label, filename, started, result, **extra):
    fields = ("description", "quantity", "free_quantity", "purchase_rate", "gst_percent")
    lines = result.get("lines", [])
    return {
        "mode": label,
        "file": filename,
        "seconds": round(time.perf_counter() - started, 2),
        "bill_number": result.get("bill_number"),
        "bill_date": result.get("bill_date"),
        "line_count": len(lines),
        "gst_rates": sorted({line.get("gst_percent", 0) for line in lines}),
        "sample_lines": [{key: line.get(key) for key in fields} for line in lines[:10]],
        "audit": result.get("_audit"),
        **extra,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+")
    args = parser.parse_args()
    ocr = load_ocr()
    for filename in args.images:
        path = Path(filename)
        content = path.read_bytes()
        started = time.perf_counter()
        try:
            result, _engine, _fingerprint = ocr.extract_bill(content, "image/jpeg")
            audit = result.get("_audit", {})
            for page in audit.get("pages", []):
                page.pop("raw_text", None)
            report = summary("bilingual-pipeline", path.name, started, result)
        except Exception as error:
            report = {"mode": "bilingual-pipeline", "file": path.name,
                      "error": str(error), "seconds": round(time.perf_counter() - started, 2)}
        print(json.dumps(report, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
