"""One-time download of pinned official Tesseract models; never runs in bill OCR.

Run using the Python executable that runs Odoo. No third-party Python packages
are required here. --check verifies an existing installation without networking.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sys
import tempfile
from urllib.error import URLError
from urllib.request import Request, urlopen


BEST_REVISION = "e12c65a915945e4c28e237a9b52bc4a8f39a0cec"
OSD_REVISION = "ced78752cc61322fb554c280d13360b35b8684e4"
MODELS = {
    "eng": (
        f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/{BEST_REVISION}/eng.traineddata",
        "8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba",
    ),
    "tam": (
        f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/{BEST_REVISION}/tam.traineddata",
        "4b9ce85987f629dd31eaf87443a1646452a43cdf91fcf05e017382ad595dcb9e",
    ),
    "osd": (
        f"https://raw.githubusercontent.com/tesseract-ocr/tessdata/{OSD_REVISION}/osd.traineddata",
        "e19f2ae860792fdf372cf48d8ce70ae5da3c4052962fe22e9de1f680c374bb0e",
    ),
}
DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[1] / ".ocr-models" / "tessdata_best"
MAX_MODEL_BYTES = 100 * 1024 * 1024


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_model(directory, language, url, expected_hash):
    destination = directory / f"{language}.traineddata"
    if destination.is_file() and file_hash(destination) == expected_hash:
        print(f"Verified {language}: {destination}", flush=True)
        return
    temporary_path = None
    try:
        print(f"Downloading official {language} model ...", flush=True)
        request = Request(url, headers={"User-Agent": "wholesale-shop-pos-local-ocr-setup"})
        with urlopen(request, timeout=60) as response:
            with tempfile.NamedTemporaryFile(
                dir=directory, prefix=f".{language}-", suffix=".download", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                digest = hashlib.sha256()
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_MODEL_BYTES:
                        raise ValueError(f"The {language} model exceeds the expected download size limit.")
                    digest.update(chunk)
                    temporary.write(chunk)
            if digest.hexdigest() != expected_hash:
                raise ValueError(f"Checksum verification failed for {language}; existing model was preserved.")
        os.replace(temporary_path, destination)
        print(f"Installed and verified {language}: {destination}", flush=True)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=Path(
        os.environ.get("SHOP_OCR_TESSDATA", str(DEFAULT_MODEL_DIR))
    ), help="Directory containing eng.traineddata, tam.traineddata and osd.traineddata")
    parser.add_argument("--check", action="store_true", help="Verify model hashes without downloading anything")
    args = parser.parse_args(argv)
    directory = args.model_dir.expanduser().resolve()
    try:
        if args.check:
            missing = []
            for language, (_url, expected_hash) in MODELS.items():
                target = directory / f"{language}.traineddata"
                valid = target.is_file() and file_hash(target) == expected_hash
                print(f"{language}: {'verified' if valid else 'missing or checksum mismatch'}", flush=True)
                if not valid:
                    missing.append(language)
            return 1 if missing else 0
        directory.mkdir(parents=True, exist_ok=True)
        for language, (url, expected_hash) in MODELS.items():
            install_model(directory, language, url, expected_hash)
    except (OSError, URLError, ValueError) as error:
        print(f"Local OCR setup failed: {error}", file=sys.stderr)
        return 1
    print("Tamil + English OCR models are ready. Restart Odoo manually if it is running.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
