#!/usr/bin/env python3
"""Generate videos/INDEX.md and videos/INDEX.json cataloguing every VMAF project.

Scans each videos/videoN/ project, infers identity from the workflow
manifest/media-inventory where present and filename patterns otherwise,
reads the pooled metrics of every *_vmaf.json result, probes every media
stream's codec/resolution/fps/bitrate, and fills in the Bilibili 稿件标题
and uploader (name + uid) via the public view API. Writes a
human-readable markdown index plus a machine-readable JSON index.

videos/ is local data only and never committed to git; the generated
INDEX files and the Bilibili API cache live beside the projects.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

VIDEOS_DIR = Path(__file__).resolve().parent.parent / "videos"
INDEX_MD = VIDEOS_DIR / "INDEX.md"
INDEX_JSON = VIDEOS_DIR / "INDEX.json"
METADATA_TXT = VIDEOS_DIR / "metadata.txt"
BILI_CACHE_PATH = VIDEOS_DIR / "catalog_bili_cache.json"

BILI_VIEW_API = "https://api.bilibili.com/x/web-interface/view"
BILI_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

MEDIA_SUFFIXES = {".mkv", ".mov", ".mp4", ".webm"}
MAIN_METRICS = ("vmaf_hd", "vmaf_4k", "vmaf")
LOW_MIN_WARN = 40.0

BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
YT_RE = re.compile(r"[0-9A-Za-z_-]{11}")
URL_BV_RE = re.compile(r"https?://www\.bilibili\.com/video/(BV[0-9A-Za-z]+)")
URL_YT_RE = re.compile(r"https?://www\.youtube\.com/watch\?v=([0-9A-Za-z_-]{11})")

DOWNLOAD_HINTS = (
    "proxy",
    "m3u8",
    "-avc",
    "-av1",
    "-av01",
    "-hevc",
    "-vp9",
    "-2160p",
    "-1440p",
    "-1080p",
    "高码率",
    "高帧率",
    "超清",
)


def log(message: str) -> None:
    print(message, file=sys.stderr)


def natural_key(path: Path) -> int:
    digits = re.sub(r"\D", "", path.name)
    return int(digits) if digits else 0


def parse_metadata_titles() -> dict[str, str]:
    """Map bvid/ytid -> 稿件标题 from the legacy metadata.txt file."""
    if not METADATA_TXT.exists():
        return {}
    titles: dict[str, str] = {}
    current_title: str | None = None
    for line in METADATA_TXT.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        bv_match = URL_BV_RE.search(stripped)
        yt_match = URL_YT_RE.search(stripped)
        if bv_match:
            titles[bv_match.group(1)] = current_title or ""
        elif yt_match:
            titles[yt_match.group(1)] = current_title or ""
        elif not re.match(r"^\d", stripped) and not stripped.startswith("["):
            current_title = stripped
    return titles


def youtube_id_from_name(name: str) -> str | None:
    """Return a leading YouTube video id from a download filename, if any.

    A download always carries a '-' right after the id (e.g.
    ``LltfPE6aWU8-avc.mp4``, ``-zXsKkyzWms-1080p-...``). Requiring the
    separator avoids false positives like ``TougenRenka.mp4`` (11 chars,
    no dash). YouTube ids may start with '-' and are 11 chars total.
    """
    stem = Path(name).stem
    match = re.match(r"([0-9A-Za-z_-]{11})-", stem)
    if match and not BV_RE.search(match.group(1)):
        return match.group(1)
    return None


def is_download_name(name: str) -> bool:
    if "_proxy" in name.lower():
        return True
    if BV_RE.search(name):
        return True
    if youtube_id_from_name(name):
        return True
    lowered = name.lower()
    return any(hint in lowered for hint in DOWNLOAD_HINTS)


def clean_title(reference_name: str) -> str:
    stem = Path(reference_name).stem
    stem = re.sub(r"(_ref|_reference|_fixed)$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"^ref[_-]", "", stem)
    return stem or reference_name


def media_files(project: Path) -> list[Path]:
    files = []
    for path in project.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in MEDIA_SUFFIXES:
            continue
        relative = path.relative_to(project)
        if any(part.startswith(".") for part in relative.parts[:-1]):
            continue
        files.append(path)
    return sorted(files, key=lambda p: p.name.lower())


def result_files(project: Path) -> list[Path]:
    return sorted(
        (p for p in project.glob("*_vmaf.json") if p.is_file()),
        key=lambda p: p.name.lower(),
    )


def load_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def project_identity(project: Path, media: list[Path]) -> dict:
    """Resolve bvid/ytid/reference. Workflow manifests win; else infer."""
    identity: dict = {"bvid": None, "ytid": None, "reference": None, "source": None}

    manifest = load_json(project / ".workflow" / "manifest.json")
    inventory = load_json(project / ".workflow" / "media-inventory.json")

    if isinstance(manifest, dict):
        bilibili = manifest.get("bilibili")
        youtube = manifest.get("youtube")
        if isinstance(bilibili, dict) and bilibili.get("bvid"):
            identity["bvid"] = bilibili["bvid"]
        if isinstance(youtube, dict) and youtube.get("url"):
            match = URL_YT_RE.search(str(youtube["url"]))
            if match:
                identity["ytid"] = match.group(1)
    if isinstance(inventory, dict) and inventory.get("reference"):
        identity["reference"] = Path(inventory["reference"]).name
        identity["source"] = "workflow"

    # Infer ids from filenames when the manifest did not provide them.
    if not identity["bvid"] or not identity["ytid"]:
        for path in media:
            if not identity["bvid"]:
                match = BV_RE.search(path.name)
                if match:
                    identity["bvid"] = match.group(0)
            if not identity["ytid"]:
                ytid = youtube_id_from_name(path.name)
                if ytid:
                    identity["ytid"] = ytid

    if identity["reference"] is None:
        candidates = [p.name for p in media if not is_download_name(p.name)]
        if len(candidates) == 1:
            identity["reference"] = candidates[0]
            identity["source"] = identity["source"] or "legacy"
        elif len(candidates) > 1:
            identity["reference"] = candidates[0]
            identity["source"] = identity["source"] or "legacy-ambiguous"
    return identity


def read_result_metrics(path: Path) -> dict:
    data = load_json(path)
    if data is None:
        return {}
    pooled = data.get("pooled_metrics")
    if not isinstance(pooled, dict):
        return {}
    metrics: dict = {}
    for metric in MAIN_METRICS:
        entry = pooled.get(metric)
        if not isinstance(entry, dict):
            continue
        metric_min = entry.get("min")
        mean = entry.get("mean")
        if isinstance(metric_min, (int, float)):
            metrics[metric] = {"min": float(metric_min)}
        if isinstance(mean, (int, float)):
            metrics.setdefault(metric, {})["mean"] = float(mean)
    return metrics


def probe_media(path: Path) -> dict:
    """Container-level ffprobe of the first video stream (no decoding)."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height,avg_frame_rate,bit_rate",
                "-show_entries",
                "format=bit_rate,duration",
                "-of",
                "json",
                str(path),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
    except OSError:
        return {}
    if result.returncode != 0:
        return {}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    stream = next(iter(data.get("streams") or []), {})
    fmt = data.get("format") or {}
    return {
        "codec": stream.get("codec_name"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "fps": stream.get("avg_frame_rate"),
        "stream_bitrate": stream.get("bit_rate"),
        "format_bitrate": fmt.get("bit_rate"),
        "duration": fmt.get("duration"),
    }


def parse_fps(raw: object) -> float | None:
    if not isinstance(raw, str) or raw in {"", "0/0"}:
        return None
    num, _, den = raw.partition("/")
    try:
        numerator = float(num)
        denominator = float(den) if den else 1.0
    except ValueError:
        return None
    return numerator / denominator if denominator else None


def select_bitrate(probe: dict, size_bytes: int) -> int | None:
    """Stream bitrate, else container bitrate, else size/duration estimate."""
    for key in ("stream_bitrate", "format_bitrate"):
        value = probe.get(key)
        if isinstance(value, str) and value.isdigit() and int(value) > 0:
            return int(value)
    duration = probe.get("duration")
    if duration:
        try:
            seconds = float(duration)
        except (TypeError, ValueError):
            seconds = 0.0
        if seconds > 0:
            return int(size_bytes * 8 / seconds)
    return None


def load_bili_cache() -> dict:
    if not BILI_CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(BILI_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_bili_cache(cache: dict) -> None:
    BILI_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fetch_bili_info(bvid: str, cache: dict) -> dict | None:
    """Return {title, owner_name, owner_uid} from the Bilibili view API.

    Successful results are cached in *cache* (persisted by the caller).
    Failures return None and are not cached, so a transient network blip
    is retried on the next run.
    """
    if bvid in cache:
        return cache[bvid] or None
    req = urllib.request.Request(
        f"{BILI_VIEW_API}?bvid={bvid}",
        headers={"User-Agent": BILI_UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
        return None
    data = payload["data"]
    owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
    info = {
        "title": data.get("title"),
        "owner_name": owner.get("name"),
        "owner_uid": owner.get("mid"),
    }
    if not info["title"]:
        return None
    cache[bvid] = info
    return info


def fetch_all_bili(bvids: set[str], cache: dict) -> dict[str, dict | None]:
    def get(bvid: str) -> tuple[str, dict | None]:
        log(f"  bili api {bvid}")
        return bvid, fetch_bili_info(bvid, cache)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = dict(pool.map(get, sorted(bvids)))
    save_bili_cache(cache)
    return results


def scan_project(
    project: Path,
    titles: dict[str, str],
    bili_info: dict[str, dict | None],
) -> dict:
    media_paths = media_files(project)
    results = result_files(project)
    identity = project_identity(project, media_paths)

    reference_name = identity["reference"]
    bvid = identity["bvid"]

    log(f"  probing {len(media_paths)} media files")
    media_entries = []
    for path in media_paths:
        probe = probe_media(path)
        bitrate = select_bitrate(probe, path.stat().st_size)
        media_entries.append(
            {
                "file": path.name,
                "role": "参考" if path.name == reference_name else "对比",
                "codec": probe.get("codec"),
                "width": probe.get("width"),
                "height": probe.get("height"),
                "fps": parse_fps(probe.get("fps")),
                "bitrate_bps": bitrate,
                "size_bytes": path.stat().st_size,
            }
        )

    bili = bili_info.get(bvid) if bvid else None
    title = identity["reference"] and clean_title(reference_name) or project.name
    if bvid and titles.get(bvid):
        title = titles[bvid]
    elif bvid and bili and bili.get("title"):
        title = bili["title"]
    elif identity["ytid"] and titles.get(identity["ytid"]):
        title = titles[identity["ytid"]]

    distorted_count = (
        len([p for p in media_paths if p.name != reference_name])
        if reference_name
        else len(media_paths)
    )

    result_rows = []
    for path in results:
        log(f"  reading {path.name} ({path.stat().st_size / 1e6:.0f}MB)")
        metrics = read_result_metrics(path)
        main = next((m for m in MAIN_METRICS if m in metrics), None)
        entry = {
            "file": path.name,
            "size_bytes": path.stat().st_size,
            "metrics": metrics,
        }
        if main:
            entry["main_metric"] = main
            entry["min"] = metrics[main].get("min")
            entry["mean"] = metrics[main].get("mean")
        result_rows.append(entry)

    mins = [r["min"] for r in result_rows if r.get("min") is not None]
    means = [r["mean"] for r in result_rows if r.get("mean") is not None]
    worst_min = min(mins) if mins else None
    mean_range = (min(means), max(means)) if means else None

    if not results:
        status = "未运行"
    elif len(results) < distorted_count:
        status = f"缺 {distorted_count - len(results)} 个结果"
    elif len(results) > distorted_count:
        status = "完成（含额外）"
    else:
        status = "完成"

    total_bytes = sum(p.stat().st_size for p in project.rglob("*") if p.is_file())
    newest = max(
        (p.stat().st_mtime for p in project.rglob("*") if p.is_file()),
        default=0.0,
    )
    updated = datetime.fromtimestamp(newest).strftime("%Y-%m-%d")

    return {
        "project": project.name,
        "title": title,
        "bvid": bvid,
        "ytid": identity["ytid"],
        "reference": reference_name,
        "source": identity["source"],
        "owner_name": bili.get("owner_name") if bili else None,
        "owner_uid": bili.get("owner_uid") if bili else None,
        "media_count": len(media_paths),
        "distorted_count": distorted_count,
        "result_count": len(results),
        "worst_min": worst_min,
        "worst_metric": (
            next(
                (r["main_metric"] for r in result_rows if r.get("min") == worst_min),
                None,
            )
            if worst_min is not None
            else None
        ),
        "mean_range": mean_range,
        "size_bytes": total_bytes,
        "updated": updated,
        "status": status,
        "warnings": (
            ["worst_min < 40（疑似未对齐或异常）"]
            if worst_min is not None and worst_min < LOW_MIN_WARN
            else []
        ),
        "media": media_entries,
        "results": result_rows,
    }


def format_fps(fps: float | None) -> str:
    if fps is None:
        return "–"
    if abs(fps - round(fps)) < 0.01:
        return f"{round(fps):.0f}"
    return f"{fps:.2f}"


def format_bitrate(bitrate: int | None) -> str:
    if bitrate is None:
        return "–"
    return f"{bitrate / 1e6:.1f} Mbps"


def md_cell(value: object) -> str:
    """Escape a markdown table cell. Bilibili titles can contain '|'."""
    return str(value).replace("|", r"\|")


def render_markdown(projects: list[dict]) -> str:
    lines: list[str] = []
    lines.append("# VMAF 项目索引")
    lines.append("")
    lines.append(f"由 `devscripts/catalog_videos.py` 生成：{datetime.now():%Y-%m-%d %H:%M}")
    lines.append("")
    lines.append("> `videos/` 为本地数据，不入库。此索引与 `INDEX.json` 仅供本地参考。")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append("| 项目 | 视频标题 | B站 | YouTube | UP主 | 参考文件 | 对比 | 结果 | 最差 min | mean 范围 | 大小 | 状态 | 更新 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for p in projects:
        warn = " ⚠" if p["warnings"] else ""
        mean_txt = (
            f"{p['mean_range'][0]:.1f}–{p['mean_range'][1]:.1f}"
            if p["mean_range"]
            else "–"
        )
        min_txt = (
            f"{p['worst_min']:.1f} ({p['worst_metric']})"
            if p["worst_min"] is not None
            else "–"
        )
        owner = p["owner_name"] or "–"
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {}{} | {} |".format(
                md_cell(p["project"]),
                md_cell(p["title"]),
                md_cell(p["bvid"] or "–"),
                md_cell(p["ytid"] or "–"),
                md_cell(owner),
                md_cell(p["reference"] or "?"),
                p["distorted_count"],
                p["result_count"],
                md_cell(min_txt),
                md_cell(mean_txt),
                md_cell(f"{p['size_bytes'] / 1e9:.1f}G"),
                md_cell(p["status"]),
                md_cell(warn),
                md_cell(p["updated"]),
            )
        )
    lines.append("")
    for p in projects:
        lines.append(f"## {p['project']} — {p['title']}")
        lines.append("")
        for label, value in (
            ("B站", p["bvid"]),
            ("YouTube", p["ytid"]),
            ("UP主", (
                f"{p['owner_name']}（uid {p['owner_uid']}）"
                if p["owner_name"]
                else None
            )),
            ("参考文件", p["reference"]),
            ("状态", p["status"]),
            ("大小", f"{p['size_bytes'] / 1e9:.2f}G"),
        ):
            if value:
                lines.append(f"- {label}：{value}")
        if p["warnings"]:
            lines.append(f"- 警告：{', '.join(p['warnings'])}")
        lines.append("")
        lines.append("### 媒体流")
        lines.append("")
        lines.append("| 文件 | 角色 | 编码 | 分辨率 | fps | 码率 |")
        lines.append("|---|---|---|---|---|---|")
        for m in p["media"]:
            res = (
                f"{m['width']}x{m['height']}"
                if m.get("width") and m.get("height")
                else "–"
            )
            lines.append(
                f"| {md_cell(m['file'])} | {m['role']} | {m['codec'] or '–'} | {res} | "
                f"{format_fps(m.get('fps'))} | {format_bitrate(m.get('bitrate_bps'))} |"
            )
        lines.append("")
        if not p["results"]:
            lines.append("### VMAF 结果")
            lines.append("")
            lines.append("_无 VMAF 结果。_")
            lines.append("")
            continue
        lines.append("### VMAF 结果")
        lines.append("")
        lines.append("| 结果文件 | 指标 | min | mean |")
        lines.append("|---|---|---:|---:|")
        for r in p["results"]:
            if not r["metrics"]:
                lines.append(f"| {r['file']} | _解析失败_ | – | – |")
                continue
            metric_label = r.get("main_metric") or ", ".join(r["metrics"].keys())
            min_txt = f"{r['min']:.1f}" if r.get("min") is not None else "–"
            mean_txt = f"{r['mean']:.1f}" if r.get("mean") is not None else "–"
            lines.append(
                f"| {md_cell(r['file'])} | {md_cell(metric_label)} | "
                f"{min_txt} | {mean_txt} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    titles = parse_metadata_titles()
    projects = sorted(
        (p for p in VIDEOS_DIR.glob("video*") if p.is_dir()),
        key=natural_key,
    )
    if not projects:
        log("no projects found under videos/")
        sys.exit(1)

    bili_cache = load_bili_cache()
    bvids = {
        project_identity(p, media_files(p))["bvid"]
        for p in projects
    }
    bvids.discard(None)
    log(f"fetching {len(bvids)} bvids from bilibili api")
    bili_info = fetch_all_bili(bvids, bili_cache) if bvids else {}

    scanned = []
    for project in projects:
        log(f"scanning {project.name}")
        scanned.append(scan_project(project, titles, bili_info))

    INDEX_MD.write_text(render_markdown(scanned), encoding="utf-8")
    INDEX_JSON.write_text(
        json.dumps(
            {"generated_at": datetime.now().isoformat(), "projects": scanned},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log(f"wrote {INDEX_MD.relative_to(VIDEOS_DIR.parent)}")
    log(f"wrote {INDEX_JSON.relative_to(VIDEOS_DIR.parent)}")
    log(f"bilibili cache: {BILI_CACHE_PATH.relative_to(VIDEOS_DIR.parent)}")

    summary = "\n".join(
        f"  {p['project']:<9} {p['title'][:36]:<38} {p['owner_name'] or '':<12} "
        f"{p['status']:<12} {p['result_count']:>2} 结果"
        for p in scanned
    )
    print(summary)


if __name__ == "__main__":
    main()
