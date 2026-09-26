"""Isolated local PaddleOCR CPU worker. --setup downloads models only, no bills."""
import contextlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
DEPS = ROOT / ".ocr-runtime" / "paddle-deps"
MODELS = Path(__file__).resolve().parents[1] / ".ocr-models" / "paddle"
DETECTOR = "PP-OCRv5_mobile_det"
RECOGNIZER = "ta_PP-OCRv5_mobile_rec"


def main():
    # Keep relative library paths out of /root when invoked with sudo -u.
    image_path = (Path(sys.argv[1]).absolute() if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else None)
    os.chdir(Path(__file__).resolve().parents[1])
    # Windows uses downloaded wheels; Linux uses its dedicated OCR virtualenv.
    if os.name == "nt" and DEPS.is_dir():
        sys.path.insert(0, str(DEPS))
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["OMP_NUM_THREADS"] = "2"
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(MODELS.parent / "paddle-cache"))
    if "--setup" in sys.argv:
        from huggingface_hub import snapshot_download
        for model in (DETECTOR, RECOGNIZER):
            snapshot_download(repo_id="PaddlePaddle/" + model, local_dir=str(MODELS / model))
        print("PaddleOCR Tamil/English models downloaded locally.")
        return
    os.environ["HF_HUB_OFFLINE"] = "1"
    for model in (DETECTOR, RECOGNIZER):
        if not (MODELS / model / "inference.yml").is_file():
            raise RuntimeError("Run paddle_bill_worker.py --setup before using PaddleOCR.")
    # Suppress library progress output in the JSON protocol, not diagnostic stderr.
    with contextlib.redirect_stdout(sys.stderr):
        import cv2
        import numpy as np
        from paddleocr import PaddleOCR
        engine = PaddleOCR(
            text_detection_model_name=DETECTOR,
            text_detection_model_dir=str(MODELS / DETECTOR),
            text_recognition_model_name=RECOGNIZER,
            text_recognition_model_dir=str(MODELS / RECOGNIZER),
            use_doc_orientation_classify=False, use_doc_unwarping=False,
            use_textline_orientation=False, device="cpu", cpu_threads=2,
            enable_mkldnn=True, text_det_limit_side_len=1280,
            text_det_limit_type="max", text_recognition_batch_size=8,
        )
    if "--check" in sys.argv:
        from importlib.metadata import version
        with contextlib.redirect_stdout(sys.stderr):
            # Exercise inference without needing a private bill or marker file.
            list(engine.predict(np.full((128, 256, 3), 255, dtype=np.uint8)))
        print(json.dumps({
            "ready": True, "python": sys.executable, "models": str(MODELS),
            "versions": {name: version(name) for name in ("paddleocr", "paddlepaddle", "paddlex")},
            "detector": DETECTOR, "recognizer": RECOGNIZER,
        }), flush=True)
        return

    def recognize(content):
        with contextlib.redirect_stdout(sys.stderr):
            image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError("Invalid image")
            output = []
            for result in engine.predict(image):
                data = result.json
                if isinstance(data, str):
                    data = json.loads(data)
                data = data.get("res", data)
                polygons = data.get("rec_polys", [])
                for text, score, polygon in zip(data.get("rec_texts", []), data.get("rec_scores", []), polygons):
                    output.append({"text": text, "score": float(score), "box": np.asarray(polygon).tolist()})
        print(json.dumps(output, ensure_ascii=True), flush=True)

    if "--serve" in sys.argv:
        while True:
            header = sys.stdin.buffer.readline(32)
            if not header:
                return
            size = int(header)
            if not 0 < size <= 100 * 1024 * 1024:
                raise ValueError("Invalid image size")
            content = sys.stdin.buffer.read(size)
            if len(content) != size:
                raise ValueError("Incomplete image")
            recognize(content)
    else:
        recognize(image_path.read_bytes() if image_path else sys.stdin.buffer.read())


if __name__ == "__main__":
    main()
