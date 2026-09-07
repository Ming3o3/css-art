#!/usr/bin/env python3
"""Small local web UI for the css-art converter.

The server is intentionally stdlib-only. It reuses the existing CLI conversion
pipeline and keeps all uploaded/generated files in a temporary directory.
"""

import argparse
import json
import mimetypes
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlsplit
import uuid


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
SKILL_SCRIPTS = ROOT / "skills" / "css-art" / "scripts"
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.css": "app.css",
    "/app.js": "app.js",
}


def parse_multipart(content_type, body):
    """Return simple name -> (filename, bytes/text) fields from FormData."""
    if not content_type or not content_type.lower().startswith("multipart/form-data"):
        raise ValueError("需要使用 multipart 表单上传图片。")
    envelope = (
        f"Content-Type: {content_type}\r\n"
        "MIME-Version: 1.0\r\n\r\n"
    ).encode("utf-8") + body
    message = BytesParser(policy=default).parsebytes(envelope)
    if not message.is_multipart():
        raise ValueError("图片上传表单格式无效。")
    fields = {}
    for part in message.iter_parts():
        disposition = part.get("Content-Disposition", "")
        name = part.get_param("name", header="Content-Disposition")
        if not name or "form-data" not in disposition:
            continue
        filename = part.get_filename()
        value = part.get_payload(decode=True) or b""
        fields[name] = (filename, value) if filename is not None else value.decode("utf-8", "replace")
    return fields


def parse_settings(value):
    try:
        settings = json.loads(value or "{}")
    except json.JSONDecodeError as error:
        raise ValueError("转换设置不是有效的 JSON。") from error
    if not isinstance(settings, dict):
        raise ValueError("转换设置必须是对象。")

    preset = settings.get("preset", "faithful")
    if preset not in {"preview", "balanced", "faithful"}:
        raise ValueError("预设参数无效。")
    background = settings.get("background", "#ffffff")
    if not isinstance(background, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", background):
        raise ValueError("背景色必须是 #RRGGBB 格式。")

    def optional_int(name, low, high):
        raw = settings.get(name)
        if raw in (None, ""):
            return None
        try:
            result = int(raw)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name} 必须是整数。") from error
        if not low <= result <= high:
            raise ValueError(f"{name} 必须在 {low} 到 {high} 之间。")
        return result

    def optional_positive(name):
        raw = settings.get(name)
        if raw in (None, ""):
            return None
        try:
            result = float(raw)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name} 必须是正数。") from error
        if result <= 0:
            raise ValueError(f"{name} 必须是正数。")
        return result

    return {
        "preset": preset,
        "max_width": optional_int("max_width", 1, 2400),
        "colors": optional_int("colors", 2, 256),
        "background": background,
        "title": str(settings.get("title") or "CSS 轮廓插画")[:240],
        "no_gradients": bool(settings.get("no_gradients")),
        "no_underpainting": bool(settings.get("no_underpainting")),
        "max_output_mb": optional_positive("max_output_mb") or 64,
        "fit": optional_positive("fit"),
        "score": bool(settings.get("score")),
    }


def convert_upload(fields, temporary):
    file_field = fields.get("file")
    if not isinstance(file_field, tuple):
        raise ValueError("请先选择图片。")
    filename, content = file_field
    if not content:
        raise ValueError("选择的图片为空。")

    settings = parse_settings(fields.get("settings", "{}"))
    suffix = Path(filename or "reference.png").suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        suffix = ".png"
    source = temporary / f"reference-{uuid.uuid4().hex}{suffix}"
    output = temporary / f"result-{uuid.uuid4().hex}.html"
    source.write_bytes(content)

    import sys
    sys.path.insert(0, str(SKILL_SCRIPTS))
    from css_art.cli import convert

    background = tuple(int(settings["background"][index:index + 2], 16) for index in (1, 3, 5))
    cli_settings = dict(settings)
    cli_settings["background"] = background
    args = argparse.Namespace(
        input=source,
        output=output,
        report=None,
        force=True,
        quiet=True,
        **cli_settings,
    )
    report = convert(args)
    html = output.read_text(encoding="utf-8")
    return html, report, Path(filename or "reference.png").stem


class WebHandler(BaseHTTPRequestHandler):
    server_version = "ImageToCSSArtWeb/1.0"

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_json(self, payload, status=HTTPStatus.OK):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        filename = STATIC_FILES.get(path)
        if filename is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        target = WEB_ROOT / filename
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if urlsplit(self.path).path != "/api/convert":
            self.send_json({"error": "找不到请求的页面。"}, HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json({"error": "请求长度无效。"}, HTTPStatus.BAD_REQUEST)
            return
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            self.send_json({"error": "图片大小必须在 1 字节到 40 MiB 之间。"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        try:
            fields = parse_multipart(self.headers.get("Content-Type"), self.rfile.read(length))
            with tempfile.TemporaryDirectory(prefix="css-art-") as directory:
                html, report, source_name = convert_upload(fields, Path(directory))
            self.send_json({"html": html, "report": report, "source_name": source_name})
        except (OSError, ValueError, ImportError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self.send_json({"error": f"{type(error).__name__}: {error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main(argv=None):
    parser = argparse.ArgumentParser(description="运行本地图片转 CSS 艺术网页界面。")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), WebHandler)
    print(f"图片转 CSS 艺术网页界面：http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n网页服务已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
