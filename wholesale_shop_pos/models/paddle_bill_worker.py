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
    sys.path.insert(0, str(DEPS))
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["OMP_NUM_THREADS"] = "2"
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(ROOT / ".ocr-runtime" / "paddle-cache")
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
        if output:
            (MODELS / "runtime-ready").write_text("Local inference completed. Review bill accuracy separately.\n", encoding="utf-8")
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
        recognize(Path(sys.argv[1]).read_bytes() if len(sys.argv) > 1 else sys.stdin.buffer.read())


if __name__ == "__main__":
    main()
