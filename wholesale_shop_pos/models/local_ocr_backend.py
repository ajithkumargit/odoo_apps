"""Offline English + Tamil OCR. No Odoo imports or runtime model downloads."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import threading
import csv
from io import BytesIO, StringIO
import subprocess


LANGUAGES = "eng+tam"
MIN_WORD_SCORE = 0.30
OCR_TIMEOUT = 45
OSD_TIMEOUT = 12
MODEL_DIR = Path(__file__).resolve().parents[1] / ".ocr-models" / "tessdata_best"
SETUP_COMMAND = "python custom_addons/wholesale_shop_pos/scripts/setup_local_ocr.py"
_TESSERACT_LOCK = threading.Lock()


class LocalOCRBackendError(Exception):
    """An actionable failure of the local OCR backend."""


class LocalOCRSetupError(LocalOCRBackendError):
    """The executable, Python wrapper or required language data is missing."""


class LocalOCRRuntimeError(LocalOCRBackendError):
    """An installed OCR engine could not read this image."""


def _find_tesseract():
    explicit = os.environ.get("SHOP_TESSERACT_CMD")
    candidates = [explicit] if explicit else [shutil.which("tesseract")]
    if not explicit:
        for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if root:
                candidates.append(str(Path(root) / "Tesseract-OCR" / "tesseract.exe"))
        if os.name == "nt":
            candidates.append(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise LocalOCRSetupError(
        "Tesseract 5 is required for local Tamil and English bill OCR. Install "
        "Tesseract and set SHOP_TESSERACT_CMD to its executable if it is not on PATH."
    )


def _tessdata_candidates(command):
    explicit = os.environ.get("SHOP_OCR_TESSDATA")
    if explicit:
        return [Path(explicit)]
    candidates = [MODEL_DIR]
    if os.environ.get("TESSDATA_PREFIX"):
        prefix = Path(os.environ["TESSDATA_PREFIX"])
        candidates.extend((prefix, prefix / "tessdata"))
    candidates.extend((
        Path(command).parent / "tessdata",
        Path("/usr/share/tesseract-ocr/5/tessdata"),
        Path("/usr/share/tesseract-ocr/4.00/tessdata"),
        Path("/usr/share/tessdata"),
        Path("/usr/local/share/tessdata"),
        Path("/opt/homebrew/share/tessdata"),
    ))
    return candidates


def get_backend_info():
    """Validate bilingual availability; never silently downgrade to English only."""
    command = _find_tesseract()
    for directory in _tessdata_candidates(command):
        if all((directory / f"{language}.traineddata").is_file() and
               (directory / f"{language}.traineddata").stat().st_size > 0
               for language in ("eng", "tam")):
            return {
                "command": command,
                "tessdata_dir": str(directory.resolve()),
                "languages": LANGUAGES,
                "has_osd": (directory / "osd.traineddata").is_file(),
                "backend": "tesseract",
            }
    raise LocalOCRSetupError(
        "Both English (eng) and Tamil (tam) Tesseract models are required. "
        f"Run the one-time setup: {SETUP_COMMAND}. "
        "For an existing installation, set SHOP_OCR_TESSDATA to the directory "
        "containing both eng.traineddata and tam.traineddata, then restart Odoo."
    )


def _recognize(image, info, psm, timeout, *, osd=False):
    """Use argument arrays and pipes, including for Windows paths with spaces.

    pytesseract's Windows config splitting retains literal quotes around the
    tessdata path. Passing each argument directly avoids that deployment bug.
    No temporary bill files or process-global environment changes are needed.
    """
    from PIL import Image

    rgb = _rgb_image(image)
    prepared = rgb if isinstance(rgb, Image.Image) else Image.fromarray(rgb)
    buffer = BytesIO()
    prepared.save(buffer, format="PNG")
    arguments = [
        info["command"], "stdin", "stdout", "--tessdata-dir", info["tessdata_dir"],
        "-l", "osd" if osd else LANGUAGES, "--oem", "0" if osd else "1",
        "--psm", str(int(psm)), "--dpi", "300",
    ]
    if not osd:
        arguments.extend(["-c", "tessedit_create_tsv=1"])
    environment = dict(os.environ, OMP_THREAD_LIMIT="2")
    try:
        result = subprocess.run(
            arguments, input=buffer.getvalue(), capture_output=True, check=False,
            timeout=max(.1, min(float(timeout), OSD_TIMEOUT if osd else OCR_TIMEOUT)),
            env=environment, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        raise LocalOCRRuntimeError("Tamil/English OCR timed out or could not start.") from error
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise LocalOCRRuntimeError("Tamil/English OCR failed: " + detail[:500])
    return result.stdout.decode("utf-8", errors="replace")


def _rgb_image(image):
    """The bill pipeline uses OpenCV BGR arrays; pytesseract expects RGB."""
    if getattr(image, "ndim", 0) == 3:
        if image.shape[2] == 3:
            return image[:, :, ::-1].copy()
        if image.shape[2] == 4:
            return image[:, :, [2, 1, 0, 3]].copy()
    return image


def run_tesseract(image, page=1, psm=11, source="tesseract", timeout=OCR_TIMEOUT):
    """Return positioned Unicode word tokens with confidence scores in [0, 1]."""
    info = get_backend_info()
    with _TESSERACT_LOCK:
        text_data = _recognize(image, info, psm, timeout)
    tokens = []
    for row in csv.DictReader(StringIO(text_data), delimiter="\t"):
        text = re.sub(r"\s+", " ", (row.get("text") or "").replace("\x00", " ")).strip()
        try:
            score = min(1.0, max(0.0, float(row["conf"]) / 100.0))
            x0, y0 = float(row["left"]), float(row["top"])
            x1 = x0 + float(row["width"])
            y1 = y0 + float(row["height"])
        except (IndexError, KeyError, TypeError, ValueError):
            continue
        if not text or score < MIN_WORD_SCORE or x1 <= x0 or y1 <= y0:
            continue
        tokens.append({
            "text": text, "score": score,
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "xc": (x0 + x1) / 2, "yc": (y0 + y1) / 2,
            "page": page, "source": source,
        })
    return tokens


def get_orientation(image, timeout=OSD_TIMEOUT):
    """Return a reliable clockwise page correction, or None for fallback trials."""
    info = get_backend_info()
    if not info["has_osd"]:
        return None
    try:
        with _TESSERACT_LOCK:
            output = _recognize(image, info, 0, timeout, osd=True)
        result = dict(line.split(":", 1) for line in output.splitlines() if ":" in line)
        rotation = int(result.get("Rotate", -1))
        if rotation in (0, 90, 180, 270) and float(result.get("Orientation confidence", 0)) >= 2.0:
            return rotation
    except (LocalOCRRuntimeError, TypeError, ValueError):
        # OSD often has too few characters in crops; recognition trials handle this.
        return None
    return None
