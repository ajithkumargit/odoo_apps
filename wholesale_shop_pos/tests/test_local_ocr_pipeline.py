"""Image/selection regressions, independent of Odoo and OCR model inference."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

import cv2
import numpy as np

SPEC = importlib.util.spec_from_file_location(
    "pipeline_regression_ocr", Path(__file__).parents[1] / "models" / "local_bill_ocr.py"
)
ocr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ocr)


class TestLocalOCRPipeline(unittest.TestCase):
    def test_successful_paddle_skips_fallback_preprocessing(self):
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        paddle = Mock()
        paddle.available.return_value = True
        paddle.recognize.return_value = []
        parsed = {"lines": [{"description": "Product"}], "warnings": []}
        with patch.object(ocr, "_get_paddle_backend", return_value=paddle), \
             patch.object(ocr, "_get_tesseract_backend") as backend, \
             patch.object(ocr, "_parse_page", return_value=parsed), \
             patch.object(ocr, "_crop_and_rectify_document") as crop, \
             patch.object(ocr, "_preprocess_variants") as preprocess:
            result = ocr._ocr_page_candidate(image, 1, True, "test")
        self.assertEqual(result[1]["lines"], parsed["lines"])
        crop.assert_not_called()
        preprocess.assert_not_called()
        backend.return_value.run_tesseract.assert_not_called()

    def test_paddle_extraction_does_not_require_tesseract_installation(self):
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        backend = Mock()
        backend.LocalOCRBackendError = type("BackendError", (Exception,), {})
        backend.get_backend_info.side_effect = backend.LocalOCRBackendError("Tesseract missing")
        parsed = ocr._parse_page([], 100, 100)
        parsed.update({"bill_number": "TEST", "lines": [{"description": "Product"}]})
        with patch.object(ocr, "_image_from_bytes", return_value=image), \
             patch.object(ocr, "_get_tesseract_backend", return_value=backend), \
             patch.object(ocr, "_get_paddle_backend") as paddle, \
             patch.object(ocr, "_ocr_page_candidate", return_value=("PaddleOCR Tamil+English", parsed, 1)):
            paddle.return_value.available.return_value = True
            result, engine, fingerprint = ocr.extract_bill(b"image", "image/jpeg")
        self.assertEqual(len(result["lines"]), 1)
        backend.get_orientation.assert_not_called()

    def test_paddle_reserves_fallback_time(self):
        with patch.object(ocr.time, "monotonic", return_value=100):
            self.assertEqual(ocr._paddle_time_budget(205), 85)
            self.assertEqual(ocr._paddle_time_budget(155), 35)
            self.assertEqual(ocr._paddle_time_budget(120), 0)

    def test_paddle_timeout_reaches_fallback(self):
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        paddle = Mock()
        paddle.available.return_value = True
        paddle.recognize.side_effect = RuntimeError("Local PaddleOCR timed out and returned no result.")
        backend = Mock()
        backend.LocalOCRSetupError = type("SetupError", (Exception,), {})
        backend.LocalOCRBackendError = type("BackendError", (Exception,), {})
        backend.run_tesseract.return_value = []
        with patch.object(ocr, "_get_paddle_backend", return_value=paddle), \
             patch.object(ocr, "_get_tesseract_backend", return_value=backend), \
             patch.object(ocr, "_preprocess_variants", return_value=(image, image)), \
             patch.object(ocr.time, "monotonic", return_value=100):
            result = ocr._ocr_page_candidate(image, 1, False, "test", deadline=205)
        self.assertEqual(paddle.recognize.call_args.kwargs["timeout"], 85)
        backend.run_tesseract.assert_called()
        self.assertTrue(any("timed out" in warning for warning in result[1]["warnings"]))

    def test_failed_retry_returns_candidate_instead_of_discarding_prior_results(self):
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        with patch.object(ocr, "_preprocess_variants", return_value=(image, image)), \
             patch.object(ocr, "_get_tesseract_backend") as backend, \
             patch.object(ocr, "_get_paddle_backend") as paddle, \
             patch.object(ocr.time, "monotonic", return_value=100):
            result = ocr._ocr_page_candidate(image, 1, False, "retry", deadline=99, allow_paddle=False)
        self.assertEqual(result[1]["lines"], [])
        self.assertTrue(result[1]["warnings"])
        paddle.return_value.recognize.assert_not_called()
        backend.return_value.run_tesseract.assert_not_called()

    def test_rotation_includes_upside_down_and_does_not_modify_original(self):
        original = np.full((60, 100, 3), 255, dtype=np.uint8)
        original[10:15,20:25] = 0
        snapshot = original.copy()
        with patch.object(ocr, "_likely_sideways", return_value=False):
            candidates = list(ocr._orientation_variants(original, 180))
        self.assertEqual(len(candidates), 4)
        self.assertEqual(candidates[0][0], "auto-rotate 180 degrees")
        np.testing.assert_array_equal(candidates[0][1], cv2.rotate(original, cv2.ROTATE_180))
        np.testing.assert_array_equal(original, snapshot)

    def test_resize_enlarges_small_text_but_enforces_max_side(self):
        small = np.full((400,300,3),255,dtype=np.uint8)
        self.assertGreater(ocr._resize_for_ocr(small).shape[1],300)
        oversized = np.full((3500,100,3),255,dtype=np.uint8)
        self.assertLessEqual(max(ocr._resize_for_ocr(oversized).shape[:2]),ocr.MAX_IMAGE_SIDE)

    def test_remove_rules_keeps_letter_stems(self):
        image = np.full((1000,1800,3),255,dtype=np.uint8)
        cv2.rectangle(image,(300,200),(306,240),(0,0,0),-1)
        cv2.line(image,(50,500),(1700,500),(0,0,0),2)
        _enhanced, cleaned = ocr._preprocess_variants(image,rectify=False,deskew=False)
        sx,sy=cleaned.shape[1]/1800,cleaned.shape[0]/1000
        self.assertLess(int(cleaned[int(220*sy),int(303*sx),0]),80)
        self.assertGreater(int(cleaned[int(500*sy),int(800*sx),0]),200)

    def test_tamil_product_names_are_not_replaced_by_english_only_candidate(self):
        tamil = {"lines":[{"description":"பால்","quantity":1,"purchase_rate":10}],
                 "_tamil_reading":True}
        english = {"lines":[{"description":"wrong","quantity":1,"purchase_rate":10}]*3}
        selected=ocr._best_candidate([("english",english,10),("tamil",tamil,8)])
        self.assertEqual(selected[0],"tamil")

    def test_disagreement_warnings_reduce_candidate_rank(self):
        good={"lines":[{"description":"Milk","quantity":1,"purchase_rate":10}],
              "warnings":[]}
        bad=dict(good,warnings=["tax disagreement","skipped row"])
        self.assertGreater(ocr._candidate_score(good),ocr._candidate_score(bad))

    def test_detected_text_angle_keeps_sloped_numbers_on_their_row(self):
        slope=-.09
        tokens=[]
        for baseline in (150,200,250):
            for x in (180,380,580):
                points=[[x,baseline+slope*x],[x+100,baseline+slope*(x+100)],
                        [x+100,baseline+16+slope*(x+100)],[x,baseline+16+slope*x]]
                tokens.append(ocr._token("Product",.99,points))
        anchors=[{"y":y+8,"x":0,"number":i+1} for i,y in enumerate((150,200,250))]
        found=ocr._estimate_row_slope(tokens,anchors,0,50,50,300,20)
        self.assertAlmostEqual(found,slope,delta=.01)


if __name__=="__main__":
    unittest.main()
