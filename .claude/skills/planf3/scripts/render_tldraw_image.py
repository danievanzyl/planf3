#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Render an offline diagram via the local tldraw Desktop HTTP server and save
it as a PNG. Fallback image backend used when OpenAI image generation is
unavailable.

Usage:
    python render_tldraw_image.py "<diagram spec>" output.png [options]

Examples:
    python render_tldraw_image.py "Auth flow: client -> API -> DB" auth.png
    python render_tldraw_image.py "Hero overview" hero.png --size 1536x1024

Requires tldraw Desktop running locally. Port + bearer token are read from
~/Library/Application Support/tldraw/server.json on every invocation.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNAVAILABLE = 3


class TldrawUnavailable(Exception):
    """Raised when the tldraw Desktop HTTP server cannot be reached."""


def parse_size(size: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)x(\d+)", size)
    if not match:
        raise ValueError(f"invalid --size {size!r}, expected WxH e.g. 1536x1024")
    return int(match.group(1)), int(match.group(2))


def _port_responds(port: int, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/readme", timeout=timeout) as resp:
            return resp.status < 500
    except (urllib.error.URLError, OSError):
        return False


def resolve_server(home: Path | None = None) -> tuple[int, str]:
    """Resolve the tldraw server's port + bearer token from server.json."""
    if home is None:
        home = Path.home()
    server_json = home / "Library" / "Application Support" / "tldraw" / "server.json"

    if not server_json.exists():
        raise TldrawUnavailable(
            f"no server.json found at {server_json} — is tldraw Desktop running?"
        )

    try:
        data = json.loads(server_json.read_text())
        port = data["port"]
        token = data["token"]
    except (json.JSONDecodeError, KeyError) as e:
        raise TldrawUnavailable(f"malformed server.json at {server_json}: {e}") from e

    if not _port_responds(port):
        raise TldrawUnavailable(
            f"server.json found but port {port} is not responding "
            "— the app likely quit uncleanly"
        )

    return port, token


def http_post_json(url: str, token: str, payload: dict, timeout: float = 60.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def create_doc(port: int, token: str, name: str) -> dict:
    return http_post_json(f"http://localhost:{port}/api/docs/create", token, {"name": name})


def exec_code(port: int, token: str, doc_id: str, code: str) -> dict:
    return http_post_json(f"http://localhost:{port}/api/doc/{doc_id}/exec", token, {"code": code})


def screenshot(port: int, token: str, doc_id: str) -> dict:
    code = (
        f"return await api.getScreenshot({json.dumps(doc_id)}, "
        "{ size: 'large', mode: 'canvas' })"
    )
    return http_post_json(f"http://localhost:{port}/api/search", token, {"code": code})


def render_diagram_code(spec: str, width: int, height: int) -> str:
    """Build the /exec JS that draws `spec` as a titled overview box."""
    return f"""
const {{ createShapeId, toRichText }} = await import('tldraw')
const id = createShapeId('planf3-diagram')
editor.createShape({{
  id,
  type: 'geo',
  x: 0,
  y: 0,
  props: {{
    geo: 'rectangle',
    w: {width},
    h: {height},
    richText: toRichText({json.dumps(spec)}),
    align: 'middle',
    verticalAlign: 'middle',
    size: 'xl',
    fill: 'solid',
    color: 'blue',
  }},
}})
editor.selectAll()
editor.zoomToFit()
await helpers.saveDoc()
return {{ id }}
"""


def backup_if_exists(output_path: str) -> None:
    """Copy an existing output file into ./backup/ before it gets overwritten.

    Re-running a render often targets a path that already holds an image —
    losing it silently and unrecoverably is the failure mode this guards
    against. backup/ self-ignores via a backup/.gitignore of "*".
    """
    out = Path(output_path)
    if not out.exists():
        return
    backup_dir = Path.cwd() / "backup"
    backup_dir.mkdir(exist_ok=True)
    gitignore = backup_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"{out.stem}_{ts}{out.suffix}"
    counter = 1
    while dest.exists():
        dest = backup_dir / f"{out.stem}_{ts}_{counter}{out.suffix}"
        counter += 1
    shutil.copy2(out, dest)
    print(f"Backed up existing {output_path} -> {dest}")


def jpeg_to_png(src: Path, dst: Path, width: int, height: int) -> None:
    """Convert a tldraw screenshot JPEG to PNG at the requested pixel size."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["sips", "-s", "format", "png", str(src), "--out", str(dst)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["sips", "-z", str(height), str(width), str(dst)],
        check=True,
        capture_output=True,
    )
    src.unlink(missing_ok=True)


def render_tldraw_image(spec: str, output_path: str, size: str = "1536x1024") -> None:
    width, height = parse_size(size)
    port, token = resolve_server()

    doc_name = f"planf3-{uuid.uuid4().hex[:8]}"
    print(f"Creating doc:  {doc_name}")
    doc = create_doc(port, token, doc_name)
    doc_id = doc["id"]

    print(f"Drawing diagram: {spec[:120]}{'...' if len(spec) > 120 else ''}")
    exec_code(port, token, doc_id, render_diagram_code(spec, width, height))

    print("Capturing screenshot...")
    shot = screenshot(port, token, doc_id)
    jpeg_path = Path(shot["filePath"])

    out = Path(output_path)
    backup_if_exists(output_path)
    jpeg_to_png(jpeg_path, out, width, height)
    print(f"Saved: {out}")


def main():
    parser = argparse.ArgumentParser(
        description="Render an offline diagram via tldraw Desktop",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("spec", help="Diagram spec / prompt describing the image")
    parser.add_argument("output", help="Output file path (e.g., output.png)")
    parser.add_argument(
        "--size",
        "-s",
        default="1536x1024",
        help="Image size WxH (default: 1536x1024)",
    )

    args = parser.parse_args()

    try:
        parse_size(args.size)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(EXIT_ERROR)

    try:
        render_tldraw_image(spec=args.spec, output_path=args.output, size=args.size)
    except TldrawUnavailable as e:
        print(f"tldraw not available: {e}", file=sys.stderr)
        sys.exit(EXIT_UNAVAILABLE)
    except (OSError, urllib.error.URLError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(EXIT_ERROR)


if __name__ == "__main__":
    main()
