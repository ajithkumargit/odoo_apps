"""Offline backend contracts; runnable without Odoo or downloaded models."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "tested_local_ocr_backend", Path(__file__).parents[1] / "models" / "local_ocr_backend.py"
)
backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend)


class TestLocalOCRBackend(unittest.TestCase):
    def setUp(self):
        self.info = {"command": "tesseract", "tessdata_dir": "models with spaces",
                     "languages": "eng+tam", "has_osd": True}

    def test_missing_tamil_is_an_explicit_setup_error(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "eng.traineddata").write_bytes(b"test")
            with patch.object(backend, "_find_tesseract", return_value="tesseract"), \
                    patch.object(backend, "_tessdata_candidates", return_value=[Path(directory)]):
                with self.assertRaisesRegex(backend.LocalOCRSetupError, "Tamil.*required"):
                    backend.get_backend_info()

    def test_unicode_geometry_confidence_and_page(self):
        data = "left\ttop\twidth\theight\tconf\ttext\n10\t20\t60\t15\t92\tபால்\n80\t20\t40\t15\t81\tMILK\n100\t20\t10\t15\t20\t?\n"
        with patch.object(backend, "get_backend_info", return_value=self.info), \
                patch.object(backend, "_recognize", return_value=data):
            tokens = backend.run_tesseract(object(), page=2)
        self.assertEqual([token["text"] for token in tokens], ["பால்", "MILK"])
        self.assertEqual(tokens[0]["page"], 2)
        self.assertEqual(tokens[0]["score"], .92)
        self.assertEqual(tokens[0]["x1"], 70)

    def test_path_with_spaces_is_one_unquoted_process_argument(self):
        from PIL import Image
        with patch.object(backend.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout=b"", stderr=b"")) as run:
            backend._recognize(Image.new("RGB", (10, 10)), self.info, 6, 5)
        args, kwargs = run.call_args
        command = args[0]
        self.assertEqual(command[command.index("--tessdata-dir") + 1], "models with spaces")
        self.assertIn("eng+tam", command)
        self.assertNotIn("shell", kwargs)
        self.assertTrue(kwargs["input"].startswith(b"\x89PNG"))

    def test_timeout_is_user_correctable(self):
        from PIL import Image
        with patch.object(backend.subprocess, "run", side_effect=subprocess.TimeoutExpired("tesseract", 1)):
            with self.assertRaises(backend.LocalOCRRuntimeError):
                backend._recognize(Image.new("RGB", (10, 10)), self.info, 6, 1)

    def test_orientation_and_unreliable_orientation(self):
        with patch.object(backend, "get_backend_info", return_value=self.info), \
                patch.object(backend, "_recognize", return_value="Rotate: 270\nOrientation confidence: 4.5\n"):
            self.assertEqual(backend.get_orientation(object()), 270)
        with patch.object(backend, "get_backend_info", return_value=self.info), \
                patch.object(backend, "_recognize", return_value="Rotate: 180\nOrientation confidence: 0.5\n"):
            self.assertIsNone(backend.get_orientation(object()))


if __name__ == "__main__":
    unittest.main()
