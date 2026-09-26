"""Read-only bill-folder benchmark with reusable JSON OCR evidence.

Run with a folder and --output pointing to a separate report directory.
Reports contain bill data: keep them private. Source images are never changed.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import time

def load_ocr():
    spec = importlib.util.spec_from_file_location(
        "folder_local_bill_ocr", Path(__file__).parents[1] / "models" / "local_bill_ocr.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pattern", default="*")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    ocr = load_ocr()
    for path in sorted(args.folder.glob(args.pattern)):
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            continue
        started = time.monotonic()
        report = {"file": path.name}
        try:
            result, _, _ = ocr.extract_bill(path.read_bytes(), "image/jpeg")
            report["result"] = result
            report["line_count"] = len(result["lines"])
        except Exception as error:
            report["error"] = str(error)
        report["seconds"] = round(time.monotonic() - started, 2)
        (args.output / (path.name + ".json")).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({key: value for key, value in report.items() if key != "result"}), flush=True)


if __name__ == "__main__":
    main()
