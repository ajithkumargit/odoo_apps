"""Paddle inference stays outside Odoo's Python/dependency process."""
import atexit
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from concurrent.futures import Future, TimeoutError

ROOT = Path(__file__).resolve().parents[3]
MODELS = Path(__file__).resolve().parents[1] / ".ocr-models" / "paddle"


def python_path():
    return Path(os.environ.get("SHOP_PADDLE_PYTHON", str(ROOT / ".ocr-runtime" / "python312" / "python.exe")))


def available():
    return python_path().is_file() and (MODELS / "runtime-ready").is_file() and all(
        (MODELS / name / "inference.yml").is_file()
        for name in ("PP-OCRv5_mobile_det", "ta_PP-OCRv5_mobile_rec")
    )


_lock = threading.Lock()
_process = None


def _stop_worker():
    global _process
    process, _process = _process, None
    if process is not None:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdin.close()
        process.stdout.close()


atexit.register(_stop_worker)


def _exchange(process, content, future):
    try:
        process.stdin.write(str(len(content)).encode("ascii") + b"\n" + content)
        process.stdin.flush()
        response = process.stdout.readline(16 * 1024 * 1024)
        if not response.endswith(b"\n"):
            raise ValueError("Incomplete worker response")
        rows = json.loads(response)
        if not isinstance(rows, list):
            raise ValueError("Invalid worker response")
        future.set_result(rows)
    except Exception as error:
        future.set_exception(error)


def recognize(image, timeout=55):
    """Reuse one isolated model; serialize requests within their time budget."""
    global _process
    import cv2
    deadline = time.monotonic() + max(.1, timeout)
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("Could not encode the bill for PaddleOCR")
    if not _lock.acquire(timeout=max(0, deadline - time.monotonic())):
        raise RuntimeError("Local PaddleOCR timed out waiting for the reader.")
    try:
        if _process is None or _process.poll() is not None:
            _stop_worker()
            _process = subprocess.Popen(
                [str(python_path()), str(Path(__file__).with_name("paddle_bill_worker.py")), "--serve"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        future = Future()
        exchange = threading.Thread(target=_exchange, args=(_process, encoded.tobytes(), future), daemon=True)
        exchange.start()
        try:
            return future.result(timeout=max(0, deadline - time.monotonic()))
        except TimeoutError as error:
            _process.kill()
            _process.wait()
            exchange.join()
            _stop_worker()
            raise RuntimeError("Local PaddleOCR timed out and returned no result.") from error
        except Exception as error:
            _stop_worker()
            raise RuntimeError("Local PaddleOCR could not run; check its isolated installation.") from error
    finally:
        _lock.release()
