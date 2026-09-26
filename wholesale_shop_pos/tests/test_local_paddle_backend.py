"""Persistent reader protocol, timeout recovery and privacy regressions."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

spec = importlib.util.spec_from_file_location("paddle_backend", Path(__file__).parents[1] / "models/local_paddle_backend.py")
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)
POPEN = subprocess.Popen
READER = "import sys,json\nwhile True:\n h=sys.stdin.buffer.readline()\n if not h: break\n data=sys.stdin.buffer.read(int(h))\n print(json.dumps([{'png':data.startswith(bytes([137,80,78,71]))}]),flush=True)"


class TestPaddleBackend(unittest.TestCase):
    def tearDown(self):
        backend._stop_worker()

    def launch(self, code):
        def start(args, **kwargs):
            self.assertEqual(args[-1], '--serve')
            self.assertNotIn('shell', kwargs)
            self.assertEqual(Path(kwargs['cwd']), Path(backend.__file__).resolve().parents[1])
            return POPEN([sys.executable, '-u', '-c', code], **kwargs)
        return patch.object(backend.subprocess, 'Popen', side_effect=start)

    def test_linux_selects_existing_ocr_virtualenv(self):
        with patch.object(backend, "os", SimpleNamespace(name="posix", environ={})), \
             patch.object(backend.Path, "is_file", return_value=True):
            self.assertEqual(str(backend.python_path()).replace("\\", "/"), "/opt/kmlshop/ocr-venv/bin/python3")

    def test_models_work_without_manual_runtime_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            models = Path(temp)
            for name in ("PP-OCRv5_mobile_det", "ta_PP-OCRv5_mobile_rec"):
                folder = models / name
                folder.mkdir()
                for filename in ("inference.yml", "inference.json", "inference.pdiparams"):
                    (folder / filename).write_text("test")
            with patch.object(backend, "MODELS", models), \
                 patch.object(backend, "python_path", return_value=Path(sys.executable)):
                self.assertTrue(backend.available())
                (models / "ta_PP-OCRv5_mobile_rec" / "inference.pdiparams").unlink()
                self.assertFalse(backend.available())
                self.assertIn("incomplete", backend.unavailable_reason())

    def test_reuses_process_and_sends_png_privately(self):
        with self.launch(READER) as launch:
            for _ in range(2):
                self.assertEqual(backend.recognize(np.zeros((10,10,3),dtype=np.uint8), timeout=5), [{'png':True}])
            self.assertEqual(launch.call_count, 1)

    def test_timeout_then_next_request_recovers(self):
        with self.launch('import time; time.sleep(30)'):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                backend.recognize(np.zeros((10,10,3),dtype=np.uint8), timeout=.2)
        self.assertIsNone(backend._process)
        with self.launch(READER):
            self.assertTrue(backend.recognize(np.zeros((10,10,3),dtype=np.uint8), timeout=5))

    def test_crash_and_invalid_response_are_private(self):
        for code in ["import sys;sys.stderr.write('private bill');sys.exit(1)", "print('private bill',flush=True)"]:
            with self.launch(code):
                with self.assertRaisesRegex(RuntimeError, 'isolated installation') as result:
                    backend.recognize(np.zeros((10,10,3),dtype=np.uint8), timeout=5)
                self.assertNotIn('private bill', str(result.exception))
                self.assertIsNone(backend._process)

    def test_busy_reader_respects_deadline(self):
        backend._lock.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError, 'waiting'):
                backend.recognize(np.zeros((10,10,3),dtype=np.uint8), timeout=.1)
        finally:
            backend._lock.release()


if __name__ == '__main__':
    unittest.main()
