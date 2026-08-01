#!/usr/local/bin/python
"""Small, request-driven RAW preview service for OpenList iframe previews."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


PORT = env_int("RAW_PREVIEW_PORT", 5260)
MAX_EDGE = env_int("RAW_PREVIEW_MAX_EDGE", 2048)
MIN_EMBEDDED_EDGE = env_int("RAW_PREVIEW_MIN_EMBEDDED_EDGE", 1024)
CACHE_MAX_BYTES = env_int("RAW_PREVIEW_CACHE_MAX_BYTES", 5 * 1024**3)
MAX_REMOTE_BYTES = env_int("RAW_PREVIEW_MAX_REMOTE_BYTES", 512 * 1024**2)
DECODE_TIMEOUT = env_int("RAW_PREVIEW_DECODE_TIMEOUT", 120)
METADATA_TIMEOUT = env_int("RAW_PREVIEW_METADATA_TIMEOUT", 30)
PROBE_TIMEOUT = env_int("RAW_PREVIEW_PROBE_TIMEOUT", 15)
DOWNLOAD_TIMEOUT = env_int("RAW_PREVIEW_DOWNLOAD_TIMEOUT", 30)
METADATA_CACHE_VERSION = "exifread-3.5.1-rawpy-0.27.0-v1"

ALLOWED_EXTENSIONS = {
    "3fr",
    "arw",
    "ari",
    "bay",
    "cap",
    "cr2",
    "cr3",
    "crw",
    "dcr",
    "dng",
    "erf",
    "fff",
    "iiq",
    "k25",
    "kdc",
    "mef",
    "mos",
    "mrw",
    "nef",
    "nrw",
    "orf",
    "pef",
    "raf",
    "raw",
    "rw2",
    "rwl",
    "sr2",
    "srf",
    "srw",
    "x3f",
}

OPENLIST_ORIGINS = {
    value.strip().rstrip("/").lower()
    for value in os.environ.get(
        "RAW_PREVIEW_OPENLIST_ORIGINS",
        "",
    ).split(",")
    if value.strip()
}


def parse_fetch_origin(raw_value: str) -> str:
    """Validate an optional private OpenList origin used by the sidecar.

    The browser-facing signed URL is still checked against OPENLIST_ORIGINS.
    Only the origin is replaced for the server-side probe/download, preserving
    the exact signed path and query string.
    """
    value = raw_value.strip().rstrip("/")
    if not value:
        return ""
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise RuntimeError("RAW_PREVIEW_FETCH_ORIGIN 格式错误") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("RAW_PREVIEW_FETCH_ORIGIN 必须是纯 HTTP(S) origin")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


OPENLIST_FETCH_ORIGIN = parse_fetch_origin(
    os.environ.get("RAW_PREVIEW_FETCH_ORIGIN", "")
)

CACHE_DIR = Path(os.environ.get("RAW_PREVIEW_CACHE_DIR", "/var/lib/raw-preview/cache"))
TMP_DIR = Path(os.environ.get("RAW_PREVIEW_TMP_DIR", "/var/lib/raw-preview/tmp"))


def parse_source_mounts(raw_value: str) -> dict[str, Path]:
    """Parse a JSON object mapping OpenList mount names to read-only paths.

    Example: {"Photos": "/sources/Photos"}
    """
    if not raw_value.strip():
        return {}
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise RuntimeError("RAW_PREVIEW_SOURCE_MOUNTS 必须是 JSON 对象") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("RAW_PREVIEW_SOURCE_MOUNTS 必须是 JSON 对象")

    sources: dict[str, Path] = {}
    for mount, root in payload.items():
        if (
            not isinstance(mount, str)
            or mount in {".", ".."}
            or not re.fullmatch(r"[A-Za-z0-9._-]+", mount)
        ):
            raise RuntimeError(f"无效的 OpenList 存储名称：{mount!r}")
        if not isinstance(root, str) or not root.strip():
            raise RuntimeError(f"存储 {mount!r} 的本地路径无效")
        path = Path(root).expanduser()
        if not path.is_absolute():
            raise RuntimeError(f"存储 {mount!r} 的本地路径必须是绝对路径")
        sources[mount] = path
    return sources


LOCAL_SOURCES = parse_source_mounts(
    os.environ.get("RAW_PREVIEW_SOURCE_MOUNTS", "{}")
)

NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
STATE_LOCK = threading.Lock()
DECODE_LOCK = threading.BoundedSemaphore(1)
ACTIVE_FETCHES = 0
ACTIVE_DECODES = 0
LAST_ERROR = ""


class PreviewError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class SourceSpec:
    url: str
    path: str
    mount: str
    relative_parts: tuple[str, ...]
    extension: str
    local_path: Optional[Path]
    content_length: Optional[int]
    etag: str
    last_modified: str

    @property
    def local(self) -> bool:
        return self.local_path is not None


def set_error(message: str) -> None:
    global LAST_ERROR
    with STATE_LOCK:
        LAST_ERROR = message[:240]


def adjust_counter(name: str, delta: int) -> None:
    global ACTIVE_FETCHES, ACTIVE_DECODES
    with STATE_LOCK:
        if name == "fetch":
            ACTIVE_FETCHES = max(0, ACTIVE_FETCHES + delta)
        else:
            ACTIVE_DECODES = max(0, ACTIVE_DECODES + delta)


def source_url_from_query(raw_value: str) -> str:
    # OpenList's $e_url is decoded once by parse_qs. Accepting one extra layer
    # only when the URL is still encoded makes the endpoint tolerant without
    # double-decoding literal percent signs in filenames.
    value = raw_value
    if "://" not in value:
        value = urllib.parse.unquote(value)
    if len(value) > 8192:
        raise PreviewError(414, "预览链接过长")
    return value


def fetch_url_for(validated_url: str) -> str:
    """Route a validated signed URL through an optional private origin."""
    if not OPENLIST_FETCH_ORIGIN:
        return validated_url
    source = urllib.parse.urlsplit(validated_url)
    target = urllib.parse.urlsplit(OPENLIST_FETCH_ORIGIN)
    return urllib.parse.urlunsplit(
        (target.scheme, target.netloc, source.path, source.query, "")
    )


def parse_openlist_url(raw_value: str) -> tuple[str, str, str, tuple[str, ...], str]:
    url = source_url_from_query(raw_value)
    try:
        parsed = urllib.parse.urlsplit(url)
        origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    except ValueError as exc:
        raise PreviewError(400, "预览链接格式错误") from exc

    if origin not in OPENLIST_ORIGINS or parsed.username or parsed.password:
        raise PreviewError(403, "只允许使用本机 OpenList 的预览链接")
    if parsed.scheme.lower() not in {"http", "https"}:
        raise PreviewError(403, "预览链接协议不受支持")
    if not urllib.parse.parse_qs(parsed.query).get("sign"):
        raise PreviewError(403, "预览链接缺少 OpenList 签名")

    decoded_path = urllib.parse.unquote(parsed.path)
    raw_parts = decoded_path.split("/")
    if len(raw_parts) < 4 or raw_parts[1] != "p":
        raise PreviewError(403, "只允许访问 OpenList 文件路径")
    parts = tuple(raw_parts[2:])
    if any(not part or part in {".", ".."} or "\x00" in part for part in parts):
        raise PreviewError(403, "文件路径不安全")
    if any("/" in part for part in parts):
        raise PreviewError(403, "文件路径不安全")

    filename = parts[-1]
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension not in ALLOWED_EXTENSIONS:
        raise PreviewError(415, f"暂不支持 {extension or '未知'} 文件格式")
    return fetch_url_for(url), decoded_path, parts[0], parts[1:], extension


def response_metadata(response: object) -> tuple[Optional[int], str, str]:
    headers = response.headers  # type: ignore[attr-defined]
    content_length: Optional[int] = None
    value = headers.get("Content-Length")
    if value:
        try:
            content_length = int(value)
        except ValueError:
            content_length = None
    return content_length, headers.get("ETag", ""), headers.get("Last-Modified", "")


def probe_signed_url(url: str) -> tuple[Optional[int], str, str]:
    request = urllib.request.Request(url, method="HEAD")
    try:
        with NO_PROXY_OPENER.open(request, timeout=PROBE_TIMEOUT) as response:
            if response.status not in {200, 206}:
                raise PreviewError(502, "OpenList 文件预览验证失败")
            metadata = response_metadata(response)
            if metadata[0] is not None and metadata[0] > MAX_REMOTE_BYTES:
                raise PreviewError(413, "远端 RAW 文件超过 512MB 预览上限")
            return metadata
    except urllib.error.HTTPError as exc:
        if exc.code not in {405, 501}:
            raise PreviewError(502, f"OpenList 文件预览验证失败（HTTP {exc.code}）") from exc
    except urllib.error.URLError as exc:
        raise PreviewError(502, "无法连接 OpenList 文件链接") from exc

    # A few storage providers do not implement HEAD. A one-byte range still
    # validates the signature while avoiding a full RAW transfer.
    request = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
    try:
        with NO_PROXY_OPENER.open(request, timeout=PROBE_TIMEOUT) as response:
            if response.status not in {200, 206}:
                raise PreviewError(502, "OpenList 文件预览验证失败")
            response.read(1)
            content_length, etag, last_modified = response_metadata(response)
            content_range = response.headers.get("Content-Range", "")
            match = re.search(r"/([0-9]+)$", content_range)
            if match:
                content_length = int(match.group(1))
            if content_length is not None and content_length > MAX_REMOTE_BYTES:
                raise PreviewError(413, "远端 RAW 文件超过 512MB 预览上限")
            return content_length, etag, last_modified
    except urllib.error.HTTPError as exc:
        raise PreviewError(502, f"OpenList 文件预览验证失败（HTTP {exc.code}）") from exc
    except urllib.error.URLError as exc:
        raise PreviewError(502, "无法连接 OpenList 文件链接") from exc


def safe_local_path(mount: str, relative_parts: tuple[str, ...]) -> Optional[Path]:
    root = LOCAL_SOURCES.get(mount)
    if root is None:
        return None
    try:
        root_real = root.resolve(strict=False)
        candidate = root.joinpath(*relative_parts).resolve(strict=False)
        if os.path.commonpath((str(root_real), str(candidate))) != str(root_real):
            raise PreviewError(403, "文件路径越界")
        if not candidate.is_file():
            raise PreviewError(404, "源文件不存在或对应存储未在线")
        return candidate
    except PermissionError as exc:
        raise PreviewError(503, "当前存储不可访问，可能处于安全下线状态") from exc


def build_source(raw_value: str) -> SourceSpec:
    url, decoded_path, mount, relative_parts, extension = parse_openlist_url(raw_value)
    content_length, etag, last_modified = probe_signed_url(url)
    local_path = safe_local_path(mount, relative_parts)
    if local_path is not None:
        try:
            stat = local_path.stat()
            content_length = stat.st_size
            last_modified = str(stat.st_mtime_ns)
            etag = "local"
        except FileNotFoundError as exc:
            raise PreviewError(404, "源文件不存在或硬盘已离线") from exc
        except PermissionError as exc:
            raise PreviewError(503, "当前存储不可访问，可能处于安全下线状态") from exc
    return SourceSpec(
        url=url,
        path=decoded_path,
        mount=mount,
        relative_parts=relative_parts,
        extension=extension,
        local_path=local_path,
        content_length=content_length,
        etag=etag,
        last_modified=last_modified,
    )


def cache_key(source: SourceSpec) -> str:
    identity = {
        "path": source.path,
        "mount": source.mount,
        "size": source.content_length,
        "etag": source.etag,
        "modified": source.last_modified,
        "decoder": "rawpy-0.27.0-libraw-0.22.1",
        "edge": MAX_EDGE,
        "min_thumb": MIN_EMBEDDED_EDGE,
    }
    raw = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def cache_path_for(source: SourceSpec) -> Path:
    return CACHE_DIR / f"{cache_key(source)}.jpg"


def metadata_cache_path_for(source: SourceSpec) -> Path:
    identity = f"{cache_key(source)}:{METADATA_CACHE_VERSION}".encode("utf-8")
    key = hashlib.sha256(identity).hexdigest()
    return CACHE_DIR / f"{key}.json"


def prune_cache() -> None:
    try:
        entries = []
        total = 0
        for item in CACHE_DIR.iterdir():
            if item.suffix.lower() not in {".jpg", ".json"} or not item.is_file():
                continue
            try:
                stat = item.stat()
            except OSError:
                continue
            total += stat.st_size
            entries.append((stat.st_mtime_ns, stat.st_size, item))
        if total <= CACHE_MAX_BYTES:
            return
        target = int(CACHE_MAX_BYTES * 0.9)
        for _, size, item in sorted(entries):
            try:
                item.unlink()
                total -= size
            except OSError:
                pass
            if total <= target:
                break
    except OSError:
        pass


def download_remote(source: SourceSpec) -> Path:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "." + source.extension
    temp = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix="raw-",
        suffix=suffix,
        dir=TMP_DIR,
        delete=False,
    )
    temp_path = Path(temp.name)
    adjust_counter("fetch", 1)
    try:
        request = urllib.request.Request(source.url)
        with temp, NO_PROXY_OPENER.open(request, timeout=DOWNLOAD_TIMEOUT) as response:
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_REMOTE_BYTES:
                    raise PreviewError(413, "远端 RAW 文件超过 512MB 预览上限")
                temp.write(chunk)
        return temp_path
    except urllib.error.HTTPError as exc:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise PreviewError(502, f"远端 RAW 下载失败（HTTP {exc.code}）") from exc
    except urllib.error.URLError as exc:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise PreviewError(502, "远端 RAW 下载失败") from exc
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise
    finally:
        adjust_counter("fetch", -1)


def decode_to_cache(source: SourceSpec, output: Path) -> str:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temp_output = CACHE_DIR / f".{output.name}.{os.getpid()}.{threading.get_ident()}.part"
    input_path: Optional[Path] = None
    remove_input = False
    try:
        if source.local_path is not None:
            input_path = source.local_path
        else:
            input_path = download_remote(source)
            remove_input = True

        adjust_counter("decode", 1)
        env = os.environ.copy()
        env.update(
            {
                "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "NUMEXPR_NUM_THREADS": "1",
            }
        )
        result = subprocess.run(
            [
                sys.executable,
                "/app/decode_worker.py",
                str(input_path),
                str(temp_output),
                "--max-edge",
                str(MAX_EDGE),
                "--min-embedded-edge",
                str(MIN_EMBEDDED_EDGE),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=DECODE_TIMEOUT,
            check=False,
        )
        if result.returncode != 0 or not temp_output.is_file():
            detail = (result.stderr or result.stdout or "未知解码错误").strip()
            raise PreviewError(422, f"RAW 解码失败：{detail[:180]}")
        os.replace(temp_output, output)
        prune_cache()
        return (result.stdout or "decoded").strip().splitlines()[-1][:32]
    except subprocess.TimeoutExpired as exc:
        raise PreviewError(504, "RAW 解码超时，已终止本次任务") from exc
    finally:
        adjust_counter("decode", -1)
        if temp_output.exists():
            try:
                temp_output.unlink()
            except OSError:
                pass
        if remove_input and input_path is not None:
            try:
                input_path.unlink()
            except OSError:
                pass


def image_bytes(source: SourceSpec) -> tuple[bytes, str, bool]:
    output = cache_path_for(source)
    try:
        cached = output.read_bytes()
        if cached:
            return cached, "cached", True
    except OSError:
        pass

    if not DECODE_LOCK.acquire(timeout=5):
        raise PreviewError(429, "已有另一个 RAW 正在解码，请稍后重试")
    try:
        try:
            cached = output.read_bytes()
            if cached:
                return cached, "cached", True
        except OSError:
            pass
        mode = decode_to_cache(source, output)
        return output.read_bytes(), mode, False
    finally:
        DECODE_LOCK.release()


def cached_metadata(output: Path) -> Optional[dict[str, object]]:
    try:
        payload = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("metadata_version") != "1":
        return None
    return payload


def metadata_to_cache(source: SourceSpec, output: Path) -> dict[str, object]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temp_output = CACHE_DIR / f".{output.name}.{os.getpid()}.{threading.get_ident()}.part"
    input_path: Optional[Path] = None
    remove_input = False
    counted = False
    try:
        if source.local_path is not None:
            input_path = source.local_path
        else:
            input_path = download_remote(source)
            remove_input = True

        adjust_counter("decode", 1)
        counted = True
        env = os.environ.copy()
        env.update(
            {
                "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "NUMEXPR_NUM_THREADS": "1",
            }
        )
        result = subprocess.run(
            [
                sys.executable,
                "/app/metadata_worker.py",
                str(input_path),
                str(temp_output),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=METADATA_TIMEOUT,
            check=False,
        )
        if result.returncode != 0 or not temp_output.is_file():
            detail = (result.stderr or result.stdout or "未知 EXIF 读取错误").strip()
            raise PreviewError(422, f"EXIF 读取失败：{detail[:180]}")
        payload = json.loads(temp_output.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or len(json.dumps(payload, ensure_ascii=False)) > 65536:
            raise PreviewError(422, "EXIF 元数据结果无效")
        os.replace(temp_output, output)
        prune_cache()
        return payload
    except subprocess.TimeoutExpired as exc:
        raise PreviewError(504, "EXIF 读取超时，已终止本次任务") from exc
    finally:
        if counted:
            adjust_counter("decode", -1)
        if temp_output.exists():
            try:
                temp_output.unlink()
            except OSError:
                pass
        if remove_input and input_path is not None:
            try:
                input_path.unlink()
            except OSError:
                pass


def metadata_for_source(source: SourceSpec) -> dict[str, object]:
    output = metadata_cache_path_for(source)
    cached = cached_metadata(output)
    if cached is not None:
        return cached

    if not DECODE_LOCK.acquire(timeout=5):
        raise PreviewError(429, "已有另一个 RAW 任务正在处理，请稍后重试")
    try:
        cached = cached_metadata(output)
        if cached is not None:
            return cached
        return metadata_to_cache(source, output)
    finally:
        DECODE_LOCK.release()


def cleanup_tmp() -> None:
    try:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - 24 * 60 * 60
        for item in TMP_DIR.iterdir():
            try:
                if item.is_file() and item.stat().st_mtime < cutoff:
                    item.unlink()
            except OSError:
                pass
    except OSError:
        pass


def html_page(source: str) -> bytes:
    # Relative URLs keep working both at /view and behind a same-origin
    # /__raw_preview/ reverse-proxy prefix.
    image_url = "image?src=" + urllib.parse.quote(source, safe="")
    metadata_url = "metadata?src=" + urllib.parse.quote(source, safe="")
    escaped = html.escape(image_url, quote=True)
    metadata_url_js = (
        json.dumps(metadata_url, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RAW 预览</title>
<style>
html,body{{margin:0;min-height:100%;background:#171717;color:#ddd;font:14px system-ui,sans-serif}}
body{{overflow:auto}}
main{{min-height:100vh;display:flex;flex-direction:column}}
figure{{flex:1 1 auto;min-height:260px;margin:0;display:flex;align-items:center;justify-content:center;position:relative;padding:12px;box-sizing:border-box}}
img{{max-width:100%;max-height:calc(100vh - 190px);object-fit:contain;user-select:none}}
figcaption{{position:absolute;left:10px;bottom:8px;padding:4px 8px;background:#0009;border-radius:4px;color:#ccc}}
.metadata{{flex:0 0 auto;max-height:30vh;overflow:auto;margin:0 12px 12px;padding:10px 12px;background:#242424;border:1px solid #3a3a3a;border-radius:8px;box-sizing:border-box}}
.metadata h2{{margin:0 0 8px;font-size:14px;font-weight:600;color:#f0f0f0}}
.metadata-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:6px 18px}}
.metadata-row{{display:flex;gap:8px;min-width:0;line-height:1.45}}
.metadata-label{{flex:0 0 auto;color:#999}}
.metadata-value{{min-width:0;overflow-wrap:anywhere;color:#eee}}
.metadata-empty{{color:#999}}
@media (max-height:600px){{figure{{min-height:180px}}img{{max-height:calc(100vh - 145px)}}.metadata{{max-height:35vh}}}}
</style>
</head>
<body><main>
<figure><img id="preview" src="{escaped}" alt="RAW 预览" loading="eager"><figcaption>RAW 预览</figcaption></figure>
<section class="metadata" id="metadata" aria-live="polite">
<h2>照片信息</h2><div class="metadata-empty">正在读取 EXIF 元数据…</div>
</section>
</main>
<script>
const metadataUrl = {metadata_url_js};
const image = document.getElementById("preview");
const panel = document.getElementById("metadata");

function addRow(grid, label, value) {{
  if (value === undefined || value === null || value === "") return;
  const row = document.createElement("div");
  row.className = "metadata-row";
  const name = document.createElement("span");
  name.className = "metadata-label";
  name.textContent = label;
  const content = document.createElement("span");
  content.className = "metadata-value";
  content.textContent = String(value);
  row.append(name, content);
  grid.append(row);
}}

function renderMetadata(metadata) {{
  panel.replaceChildren();
  const title = document.createElement("h2");
  title.textContent = "照片信息";
  panel.append(title);
  const grid = document.createElement("div");
  grid.className = "metadata-grid";
  const camera = [metadata.camera_make, metadata.camera_model].filter(Boolean).join(" ");
  const lens = [metadata.lens_make, metadata.lens_model].filter(Boolean).join(" ");
  const size = metadata.width && metadata.height ? `${{metadata.width}} × ${{metadata.height}}` : "";
  addRow(grid, "相机", camera);
  addRow(grid, "镜头", lens);
  addRow(grid, "拍摄时间", metadata.date_time_original);
  addRow(grid, "尺寸", size);
  addRow(grid, "快门", metadata.exposure_time);
  addRow(grid, "光圈", metadata.f_number ? `f/${{metadata.f_number}}` : "");
  addRow(grid, "ISO", metadata.iso);
  addRow(grid, "焦距", metadata.focal_length_mm ? `${{metadata.focal_length_mm}} mm` : "");
  addRow(grid, "等效焦距", metadata.focal_length_35mm ? `${{metadata.focal_length_35mm}} mm` : "");
  if (metadata.gps && metadata.gps.latitude !== undefined && metadata.gps.longitude !== undefined) {{
    const altitude = metadata.gps.altitude_m === undefined ? "" : `，海拔 ${{metadata.gps.altitude_m}} m`;
    addRow(grid, "GPS", `${{metadata.gps.latitude}}, ${{metadata.gps.longitude}}${{altitude}}`);
  }}
  if (!grid.children.length) {{
    const empty = document.createElement("div");
    empty.className = "metadata-empty";
    empty.textContent = "没有读取到可用的 EXIF 信息";
    panel.append(empty);
  }} else {{
    panel.append(grid);
  }}
}}

async function loadMetadata() {{
  try {{
    const response = await fetch(metadataUrl, {{credentials: "omit", cache: "no-store"}});
    const payload = await response.json();
    if (!response.ok || payload.status !== "ok") throw new Error("metadata unavailable");
    renderMetadata(payload.metadata || {{}});
  }} catch (_error) {{
    panel.replaceChildren();
    const title = document.createElement("h2");
    title.textContent = "照片信息";
    const empty = document.createElement("div");
    empty.className = "metadata-empty";
    empty.textContent = "暂时无法读取 EXIF 信息；RAW 预览本身不受影响";
    panel.append(title, empty);
  }}
}}

if (image.complete) loadMetadata();
else {{
  image.addEventListener("load", loadMetadata, {{once: true}});
  image.addEventListener("error", loadMetadata, {{once: true}});
}}
</script>
</body>
</html>""".encode("utf-8")


class PreviewHandler(BaseHTTPRequestHandler):
    server_version = "OpenListRawPreview/0.2"

    def log_message(self, fmt: str, *args: object) -> None:
        # Never log query strings: they contain OpenList signed URLs.
        print(f"{self.client_address[0]} {self.command} {self.path.split('?', 1)[0]} " + fmt % args)

    def headers_common(self, *, frameable: bool = False) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if frameable:
            ancestors = " ".join(["'self'", *sorted(OPENLIST_ORIGINS)])
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; img-src 'self'; connect-src 'self'; "
                f"script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors {ancestors}",
            )

    def send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.headers_common()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        try:
            if parsed.path == "/healthz":
                body = b"ok\n"
                self.send_response(200)
                self.headers_common()
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return

            if parsed.path == "/api/status":
                with STATE_LOCK:
                    payload = {
                        "status": "ok",
                        "active_fetches": ACTIVE_FETCHES,
                        "active_decodes": ACTIVE_DECODES,
                        "last_error": LAST_ERROR,
                    }
                self.send_json(200, payload)
                return

            source_values = query.get("src", [])
            if not source_values or not source_values[0]:
                raise PreviewError(400, "缺少 OpenList 文件链接")
            source_value = source_values[0]

            if parsed.path == "/view":
                body = html_page(source_value)
                self.send_response(200)
                self.headers_common(frameable=True)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return

            if parsed.path == "/image":
                source = build_source(source_value)
                body, mode, cached = image_bytes(source)
                self.send_response(200)
                self.headers_common()
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "private, max-age=86400")
                self.send_header("X-RAW-Preview-Mode", mode)
                self.send_header("X-RAW-Preview-Cache", "hit" if cached else "miss")
                self.end_headers()
                self.wfile.write(body)
                return

            if parsed.path == "/metadata":
                source = build_source(source_value)
                payload = metadata_for_source(source)
                self.send_json(200, {"status": "ok", "metadata": payload})
                return

            raise PreviewError(404, "预览接口不存在")
        except PreviewError as exc:
            set_error(exc.message)
            self.send_json(exc.status, {"error": exc.message})
        except Exception as exc:  # noqa: BLE001 - keep HTTP process alive
            message = f"预览服务内部错误：{type(exc).__name__}"
            set_error(message)
            self.send_json(500, {"error": message})


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 16


def main() -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_tmp()
    server = PreviewServer(("0.0.0.0", PORT), PreviewHandler)
    print(f"raw-preview listening on {PORT}; allowed extensions={len(ALLOWED_EXTENSIONS)}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
