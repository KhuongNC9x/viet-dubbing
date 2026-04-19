"""
=============================================================
  RETRY FAILED TTS v4 — Companion script for viet_dubbing.py
=============================================================
Workflow:
  Bước 1: Chạy viet_dubbing.py như bình thường
          → TTS cache nằm trong thư mục tts_<tên video>/
          → viet_dubbing v3 đã giữ lại folder này mặc định.

  Bước 2: Chạy script này để retry cue bị lỗi
          python retry_failed_tts.py --srt sub.srt --video video.mp4

  Bước 3: Chạy lại viet_dubbing.py — các file đã có sẽ được
          skip tự động, chỉ generate cue còn thiếu rồi mix.

Options:
  --srt       subtitle.srt      File SRT gốc (bắt buộc)
  --video     video.mp4         File video gốc (suy ra tên thư mục TTS)
  --tmp-dir   tts_<video>       Thư mục TTS cache (default: tts_<tên video/srt>)
  --log       logs/log_xxx.txt  File log cụ thể (default: log mới nhất)
  --voice     female/male       Giọng đọc (default: female)
  --workers   3                 Số request đồng thời (default: 3)
  --retries   5                 Số lần retry mỗi cue (default: 5)
=============================================================
"""

import asyncio
import re
import os
import sys
import argparse
import time as _time
from pathlib import Path

# ── Check dependencies ──────────────────────────────────────
_missing = []
try:
    import edge_tts
except ImportError:
    _missing.append("edge-tts")

try:
    from rich.console import Console
    from rich.progress import (
        Progress, SpinnerColumn, BarColumn,
        TaskProgressColumn, TimeElapsedColumn,
        TextColumn, MofNCompleteColumn,
    )
    from rich.table import Table
    from rich import box
except ImportError:
    _missing.append("rich")

if _missing:
    print(f"[ERROR] Missing packages: {', '.join(_missing)}")
    print(f"Run: pip install {' '.join(_missing)}")
    sys.exit(1)

console = Console()


# ============================================================
# CONSTANTS
# ============================================================

VOICES = {
    "female": ("vi-VN-HoaiMyNeural", "HoaiMy  | Female | Southern accent"),
    "male":   ("vi-VN-NamMinhNeural", "NamMinh | Male   | Southern accent"),
}

SPEED            = "+0%"
MAX_WORKERS_CAP  = 15
MIN_RETRIES      = 1
MAX_RETRIES_CAP  = 20
PREVIEW_LIMIT    = 20     # số cue hiển thị preview
TEXT_TRUNCATE    = 65     # độ dài text preview


# ============================================================
# HELPERS
# ============================================================

def cue_path(tmp_dir: str, cue_index: int) -> str:
    """Tạo path file MP3 cho cue — single source of truth."""
    return os.path.join(tmp_dir, f"cue_{cue_index:04d}.mp3")


def file_exists_and_valid(path: str) -> bool:
    """Kiểm tra file tồn tại và không rỗng."""
    return os.path.exists(path) and os.path.getsize(path) > 0


# ============================================================
# SRT PARSER
# ============================================================

def parse_srt_time(t: str) -> float:
    h, m, s_ms = t.strip().split(":")
    if "," in s_ms:
        s, ms = s_ms.split(",")
    elif "." in s_ms:
        s, ms = s_ms.split(".")
    else:
        s, ms = s_ms, "0"
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_srt(srt_path: str) -> list[dict]:
    cues = []
    content = Path(srt_path).read_text(encoding="utf-8-sig")
    blocks = re.split(r"\n\s*\n", content.strip())
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue
        times = lines[1].split("-->")
        if len(times) != 2:
            continue
        start = parse_srt_time(times[0])
        end = parse_srt_time(times[1])
        text = " ".join(lines[2:]).strip()
        text = re.sub(r"<[^>]+>", "", text)
        if text:
            cues.append({"index": idx, "start": start, "end": end, "text": text})
    return cues


# ============================================================
# TÌM CUE BỊ FAILED
# Ưu tiên: đọc log → fallback: scan thư mục TTS cache
# ============================================================

def find_latest_log(log_dir: str = "logs") -> str | None:
    if not os.path.isdir(log_dir):
        return None
    logs = sorted(Path(log_dir).glob("log_*.txt"), reverse=True)
    return str(logs[0]) if logs else None


def parse_failed_indices_from_log(log_path: str) -> list[int]:
    """Đọc log, lấy SESSION cuối, trả về list index cue bị FAILED."""
    pattern = re.compile(r"ERROR\s+TTS cue #(\d+) FAILED")
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="ignore")
        sessions = text.split("SESSION START")
        last_session = sessions[-1] if len(sessions) > 1 else text
        indices = [int(m.group(1)) for m in pattern.finditer(last_session)]
        return sorted(set(indices))
    except Exception as e:
        console.print(f"[red]Cannot read log: {e}[/]")
        return []


def find_missing_by_scan(cues: list[dict], tmp_dir: str) -> list[dict]:
    """Scan thư mục TTS cache, tìm cue chưa có file hoặc file rỗng."""
    return [
        cue for cue in cues
        if not file_exists_and_valid(cue_path(tmp_dir, cue["index"]))
    ]


# ============================================================
# TTS RETRY
# ============================================================

async def tts_one(cue: dict, out_path: str, voice: str,
                  max_retries: int) -> bool:
    """Generate TTS cho 1 cue, có retry với linear backoff."""
    for attempt in range(1, max_retries + 1):
        try:
            comm = edge_tts.Communicate(text=cue["text"], voice=voice, rate=SPEED)
            await comm.save(out_path)
            if file_exists_and_valid(out_path):
                return True
        except Exception as e:
            console.print(f"  [dim]#{cue['index']} attempt {attempt}/{max_retries}: {e}[/]")
            if attempt < max_retries:
                await asyncio.sleep(2.0 * attempt)
    return False


async def retry_all(failed_cues: list[dict], tmp_dir: str, voice: str,
                    max_workers: int, max_retries: int) -> tuple[int, int]:
    """Retry tất cả cue bị lỗi, skip cue đã có file từ lần trước."""
    os.makedirs(tmp_dir, exist_ok=True)
    semaphore = asyncio.Semaphore(max_workers)

    to_run = []
    already_done = 0
    for cue in failed_cues:
        if file_exists_and_valid(cue_path(tmp_dir, cue["index"])):
            already_done += 1
        else:
            to_run.append(cue)

    if already_done:
        console.print(f"  [cyan]Skip {already_done} cue đã có file từ lần retry trước[/]\n")

    if not to_run:
        console.print("  [green]Tất cả cue đã có file! Không cần generate thêm.[/]")
        return len(failed_cues), 0

    success = already_done
    fail = 0

    async def _worker(cue: dict) -> bool:
        path = cue_path(tmp_dir, cue["index"])
        async with semaphore:
            return await tts_one(cue, path, voice, max_retries)

    with Progress(
        SpinnerColumn(spinner_name="dots", style="cyan"),
        TextColumn("[bold cyan]Retrying TTS[/]"),
        BarColumn(bar_width=32, style="cyan", complete_style="bright_green"),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("retry", total=len(to_run))

        async_tasks = [asyncio.ensure_future(_worker(cue)) for cue in to_run]
        for coro in asyncio.as_completed(async_tasks):
            if await coro:
                success += 1
            else:
                fail += 1
            progress.advance(task)

    return success, fail


# ============================================================
# MAIN
# ============================================================

def _print_failed_preview(failed_cues: list[dict], source: str):
    """Hiển thị preview danh sách cue bị lỗi."""
    console.print(
        f"[yellow]Tìm thấy [bold]{len(failed_cues)}[/] cue cần retry[/] "
        f"[dim](nguồn: {source})[/]:\n"
    )
    for cue in failed_cues[:PREVIEW_LIMIT]:
        text = cue["text"]
        preview = text[:TEXT_TRUNCATE] + ("..." if len(text) > TEXT_TRUNCATE else "")
        console.print(f"  [dim]#{cue['index']:04d}[/]  {preview}")

    remaining = len(failed_cues) - PREVIEW_LIMIT
    if remaining > 0:
        console.print(f"  [dim]... và {remaining} cue khác[/]")
    console.print()


def _find_failed_cues(all_cues: list[dict], log_path: str | None,
                      tmp_dir: str) -> tuple[list[dict], str]:
    """
    Tìm cue bị failed theo ưu tiên:
      1. Đọc log file → lấy danh sách FAILED
      2. Fallback: scan thư mục TTS cache tìm file thiếu
    Returns (failed_cues, source_description).
    """
    # Ưu tiên 1: đọc log
    if log_path and os.path.exists(log_path):
        failed_indices = parse_failed_indices_from_log(log_path)
        if failed_indices:
            failed_set = set(failed_indices)
            failed_cues = [c for c in all_cues if c["index"] in failed_set]
            source = f"log ({os.path.basename(log_path)})"
            console.print(f"[dim]Nguồn: {log_path}[/]")
            return failed_cues, source

    # Fallback: scan thư mục TTS cache
    if os.path.exists(tmp_dir):
        failed_cues = find_missing_by_scan(all_cues, tmp_dir)
        source = f"scan {tmp_dir}/"
        console.print(f"[dim]Không có log → scan {tmp_dir}/ để tìm file thiếu[/]")
        return failed_cues, source

    console.print(f"[red]ERROR: Không có log và thư mục {tmp_dir}/ cũng không tồn tại.[/]")
    console.print("[dim]Chạy viet_dubbing.py trước để tạo thư mục TTS cache.[/]")
    sys.exit(1)


async def main():
    parser = argparse.ArgumentParser(
        description="Retry failed TTS cues vào thư mục TTS cache để viet_dubbing.py skip được"
    )
    parser.add_argument("--srt", required=True,
                        help="File SRT gốc")
    parser.add_argument("--video", default=None,
                        help="File video gốc (dùng để suy ra tên thư mục TTS cache)")
    parser.add_argument("--tmp-dir", default=None,
                        help="Thư mục TTS cache (default: tts_<tên video/srt>)")
    parser.add_argument("--log", default=None,
                        help="File log cụ thể (default: log mới nhất trong logs/)")
    parser.add_argument("--voice", default="female", choices=["female", "male"])
    parser.add_argument("--workers", default=3, type=int,
                        help="Concurrent TTS requests (default: 3, thấp để tránh rate limit)")
    parser.add_argument("--retries", default=5, type=int,
                        help="Số lần retry mỗi cue (default: 5)")
    args = parser.parse_args()

    # Derive tmp_dir: --tmp-dir > --video > --srt
    if args.tmp_dir:
        tmp_dir = args.tmp_dir
    else:
        base_name = args.video if args.video else args.srt
        tmp_dir = f"tts_{Path(base_name).stem}"

    voice_id, voice_label = VOICES[args.voice]
    workers = max(1, min(args.workers, MAX_WORKERS_CAP))
    retries = max(MIN_RETRIES, min(args.retries, MAX_RETRIES_CAP))

    # ── HEADER ──────────────────────────────────────────────
    console.print()
    console.rule("[bold cyan]RETRY FAILED TTS v4[/]")
    info = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    info.add_column(style="dim", width=14)
    info.add_column(style="white")
    info.add_row("SRT",     args.srt)
    info.add_row("TTS dir", tmp_dir + "/")
    info.add_row("Voice",   voice_label)
    info.add_row("Workers", f"{workers} concurrent")
    info.add_row("Retries", f"{retries} lần/cue")
    console.print(info)
    console.rule()
    console.print()

    # ── VALIDATE ────────────────────────────────────────────
    if not os.path.exists(args.srt):
        console.print(f"[red]ERROR: Không tìm thấy SRT file: {args.srt}[/]")
        sys.exit(1)

    # ── PARSE SRT ───────────────────────────────────────────
    with console.status("[cyan]Đọc SRT...[/]"):
        all_cues = parse_srt(args.srt)

    if not all_cues:
        console.print("[red]ERROR: Không parse được SRT.[/]")
        sys.exit(1)

    console.print(f"[green]✓[/] SRT: [bold]{len(all_cues)}[/] cues\n")

    # ── TÌM CUE FAILED ──────────────────────────────────────
    log_path = args.log or find_latest_log()
    failed_cues, source = _find_failed_cues(all_cues, log_path, tmp_dir)

    if not failed_cues:
        console.print("[green]✓ Không tìm thấy cue nào bị lỗi! Mọi thứ đã ổn.[/]")
        sys.exit(0)

    _print_failed_preview(failed_cues, source)

    # ── RETRY ────────────────────────────────────────────────
    t_start = _time.perf_counter()
    success, fail = await retry_all(
        failed_cues, tmp_dir, voice_id, workers, retries
    )
    t_total = _time.perf_counter() - t_start

    # ── SUMMARY ─────────────────────────────────────────────
    console.print()
    console.rule("[bold green]KẾT QUẢ[/]")
    result = Table(box=box.ROUNDED, show_header=False,
                   border_style="green", padding=(0, 2))
    result.add_column(style="dim", width=18)
    result.add_column(style="bold white")
    result.add_row("Tổng cue retry", str(len(failed_cues)))
    result.add_row("Thành công",     f"[green]{success}[/]")
    result.add_row("Vẫn lỗi",       f"[red]{fail}[/]" if fail else "[dim]0 ✓[/]")
    result.add_row("Thời gian",      f"{t_total:.1f}s")
    result.add_row("Đã lưu vào",    tmp_dir + "/")
    console.print(result)
    console.print()

    if fail == 0:
        console.print("[green]✓ Tất cả retry thành công![/]")
        console.print()
        console.print("[cyan]Bước tiếp theo:[/] Chạy lại viet_dubbing.py:")
        console.print(f"[dim]  python viet_dubbing.py --srt {args.srt} --video <video.mp4>[/]")
        console.print(f"[dim]  → {len(all_cues)} cue sẽ được skip, chỉ mix lại audio[/]")
    else:
        console.print(f"[yellow]⚠  Vẫn còn {fail} cue lỗi.[/] Thử:")
        console.print(f"[dim]  --workers 2    Giảm concurrency (hiện: {workers})[/]")
        console.print(f"[dim]  --retries 8    Tăng số lần retry (hiện: {retries})[/]")
        console.print(f"[dim]  Chờ vài phút rồi chạy lại nếu bị rate limit[/]")

    console.print()


if __name__ == "__main__":
    asyncio.run(main())
