#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Smoke tests for render_tldraw_image.py.

Covers what is checkable without the tldraw Desktop app or an OpenAI key:
CLI argument parsing, missing-server.json handling, stale-server.json
handling (file present, port dead), and JPEG-to-PNG conversion given a
fixture JPEG. Runs with no tldraw app and no OpenAI key present.

Usage:
    uv run scripts/test_render_tldraw_image.py
"""

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parent
RENDER_SCRIPT = SCRIPT_DIR / "render_tldraw_image.py"

sys.path.insert(0, str(SCRIPT_DIR))
import render_tldraw_image as rti

TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class ArgParsingTests(unittest.TestCase):
    def test_missing_args_exits_nonzero(self):
        result = subprocess.run(
            [sys.executable, str(RENDER_SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_invalid_size_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.png"
            result = subprocess.run(
                [sys.executable, str(RENDER_SCRIPT), "spec", str(out), "--size", "not-a-size"],
                capture_output=True,
                text=True,
                env={**os.environ, "HOME": tmp},
                check=False,
            )
            self.assertEqual(result.returncode, rti.EXIT_ERROR)
            self.assertNotIn("Traceback", result.stderr)

    def test_parse_size(self):
        self.assertEqual(rti.parse_size("1536x1024"), (1536, 1024))
        with self.assertRaises(ValueError):
            rti.parse_size("bogus")


class ServerAvailabilityTests(unittest.TestCase):
    def test_missing_server_json(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(rti.TldrawUnavailable):
            rti.resolve_server(Path(tmp))

    def test_stale_server_json_dead_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            server_dir = fake_home / "Library" / "Application Support" / "tldraw"
            server_dir.mkdir(parents=True)

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            dead_port = sock.getsockname()[1]
            sock.close()  # nothing listens on this port once closed

            (server_dir / "server.json").write_text(json.dumps({"port": dead_port, "token": "fake"}))

            with self.assertRaises(rti.TldrawUnavailable):
                rti.resolve_server(fake_home)

    def test_missing_server_json_cli_exits_with_distinct_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.png"
            result = subprocess.run(
                [sys.executable, str(RENDER_SCRIPT), "a diagram", str(out)],
                capture_output=True,
                text=True,
                env={**os.environ, "HOME": tmp},
                check=False,
            )
            self.assertEqual(result.returncode, rti.EXIT_UNAVAILABLE)
            self.assertIn("tldraw not available", result.stderr)
            self.assertNotIn("Traceback", result.stderr)


class JpegToPngConversionTests(unittest.TestCase):
    def test_convert_jpeg_to_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            png_fixture = tmp_path / "fixture.png"
            png_fixture.write_bytes(base64.b64decode(TINY_PNG_B64))
            jpeg_fixture = tmp_path / "fixture.jpg"
            subprocess.run(
                ["sips", "-s", "format", "jpeg", str(png_fixture), "--out", str(jpeg_fixture)],
                check=True,
                capture_output=True,
            )

            output_png = tmp_path / "output.png"
            rti.jpeg_to_png(jpeg_fixture, output_png, 64, 64)

            self.assertTrue(output_png.exists())
            self.assertEqual(output_png.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            self.assertFalse(jpeg_fixture.exists(), "temp JPEG should be cleaned up")


class BackupIfExistsTests(unittest.TestCase):
    def _chdir_tmp(self, tmp):
        cwd = os.getcwd()
        os.chdir(tmp)
        self.addCleanup(os.chdir, cwd)

    def test_backs_up_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._chdir_tmp(tmp)
            out = Path(tmp) / "out.png"
            out.write_bytes(base64.b64decode(TINY_PNG_B64))

            rti.backup_if_exists(str(out))

            backup_dir = Path(tmp) / "backup"
            self.assertEqual((backup_dir / ".gitignore").read_text(), "*\n")
            backups = list(backup_dir.glob("out_*.png"))
            self.assertEqual(len(backups), 1)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.assertEqual(backups[0].name, f"out_{ts}.png")
            self.assertEqual(out.read_bytes(), backups[0].read_bytes())

    def test_no_backup_when_output_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._chdir_tmp(tmp)
            out = Path(tmp) / "out.png"

            with patch("builtins.print") as mock_print:
                rti.backup_if_exists(str(out))

            self.assertFalse((Path(tmp) / "backup").exists())
            mock_print.assert_not_called()

    def test_collision_suffixes_with_counter(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._chdir_tmp(tmp)
            out = Path(tmp) / "out.png"
            out.write_bytes(base64.b64decode(TINY_PNG_B64))

            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_dir = Path(tmp) / "backup"
            backup_dir.mkdir()
            (backup_dir / f"out_{ts}.png").write_bytes(b"existing-backup")

            rti.backup_if_exists(str(out))

            collided = backup_dir / f"out_{ts}_1.png"
            self.assertTrue(collided.exists())
            self.assertEqual(collided.read_bytes(), out.read_bytes())
            self.assertEqual((backup_dir / f"out_{ts}.png").read_bytes(), b"existing-backup")


def main():
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    discovered = suite.countTestCases()

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    ran = result.testsRun
    failed = len(result.failures) + len(result.errors)
    passed = ran - failed

    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=SCRIPT_DIR, check=False
    ).stdout.strip()

    print(f"shepard-tests: DISCOVERED={discovered} RAN={ran} PASS={passed} FAIL={failed} sha={sha}")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
