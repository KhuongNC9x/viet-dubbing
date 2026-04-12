"""
=============================================================
  RETRY FAILED TTS v3 — Companion script for viet_dubbing.py
=============================================================
Workflow:
  Bước 1: Chạy viet_dubbing.py như bình thường
          → Nó sẽ tạo tts_tmp/ nhưng XÓA sau khi xong.
          → Cần sửa 1 dòng trong viet_dubbing.py:
             Tìm:   shutil.rmtree(tmp_dir)
             Sửa:   # shutil.rmtree(tmp_dir)   ← comment lại

  Bước 2: Chạy script này để retry cue bị lỗi vào đúng tts_tmp/
          python retry_failed_tts.py --srt sub.srt

  Bước 3: Chạy lại viet_dubbing.py — các file đã có sẽ được
          skip tự động, chỉ generate cue còn thiếu rồi mix.

Options:
  --srt       subtitle.srt      File SRT gốc (bắt buộc)
  --tmp-dir   tts_tmp           Thư mục TTS cache (default: tts_tmp)
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
from pathlib import Path
from datetime import datetime
import time as _time

# ── Check dependencies ──────────────────────────────────────
missing = []
try:
    import edge_tts
except ImportError:
    missing.append("edge-tts")

try:
    from rich.console import Console
    from rich.progress import (
        Progress, SpinnerColumn, BarColumn,
        TaskProgressColumn, TimeElapsedColumn,
        TextColumn, MofNCompleteColumn
    )
    from rich.table import Table
    from rich import box
except ImportError:
    missing.append("rich")

if missing:
    print(f"[ERROR] Missing packages: {', '.join(missing)}")
    print(f"Run: pip install {' '.join(missing)}")
    sys.exit(1)

console = Console()

VOICES = {
    "female": ("vi-VN-HoaiMyNeural",  "HoaiMy  | Female | Southern accent"),
    "male":   ("vi-VN-NamMinhNeural", "NamMinh | Male   | Southern accent"),
}
SPEED = "+0%"


# ============================================================
# PARSE SRT
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


def parse_srt(srt_path: str) -> list:
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
        end   = parse_srt_time(times[1])
        text  = " ".join(lines[2:]).strip()
        text  = re.sub(r"<[^>]+>", "", text)
        if text:
            cues.append({"index": idx, "start": start, "end": end, "text": text})
    return cues


# ============================================================
# TÌM CUE BỊ FAILED
# Ưu tiên: đọc log → fallback: scan thư mục tts_tmp
# ============================================================

def find_latest_log(log_dir: str = "logs") -> str | None:
    if not os.path.isdir(log_dir):
        return None
    logs = sorted(Path(log_dir).glob("log_*.txt"), reverse=True)
    return str(logs[0]) if logs else None


def parse_failed_indices_from_log(log_path: str) -> list:
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


def find_missing_by_scan(cues: list, tmp_dir: str) -> list:
    """Fallback: scan tts_tmp/ và tìm cue chưa có file hoặc file rỗng."""
    missing = []
    for cue in cues:
        path = os.path.join(tmp_dir, f"cue_{cue['index']:04d}.mp3")
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            missing.append(cue)
    return missing


# ============================================================
# TTS RETRY — lưu thẳng vào tmp_dir (tts_tmp/)
# ============================================================

async def tts_one(cue: dict, out_path: str, voice: str, max_retries: int) -> bool:
    for attempt in range(1, max_retries + 1):
        try:
            comm = edge_tts.Communicate(text=cue["text"], voice=voice, rate=SPEED)
            await comm.save(out_path)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return True
        except Exception as e:
            console.print(f"  [dim]#{cue['index']} attempt {attempt}/{max_retries}: {e}[/]")
            if attempt < max_retries:
                # Exponential backoff: 2s, 4s, 6s...
                await asyncio.sleep(2.0 * attempt)
    return False


async def retry_all(failed_cues: list, tmp_dir: str, voice: str,
                    max_workers: int, max_retries: int) -> tuple:
    os.makedirs(tmp_dir, exist_ok=True)
    semaphore = asyncio.Semaphore(max_workers)

    # Kiểm tra xem cue nào đã có file rồi (từ lần retry trước)
    to_run = []
    already_done = 0
    for cue in failed_cues:
        path = os.path.join(tmp_dir, f"cue_{cue['index']:04d}.mp3")
        if os.path.exists(path) and os.path.getsize(path) > 0:
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

    async def _worker(cue):
        path = os.path.join(tmp_dir, f"cue_{cue['index']:04d}.mp3")
        async with semaphore:
            return await tts_one(cue, path, voice, max_retries)

    with Progress(
        SpinnerColumn(spinner_name="dots", style="cyan"),
        TextColumn("[bold cyan]Retrying TTS → tts_tmp/[/]"),
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
            ok = await coro
            if ok:
                success += 1
            else:
                fail += 1
            progress.advance(task)

    return success, fail


# ============================================================
# MAIN
# ============================================================

async def main():
    parser = argparse.ArgumentParser(
        description="Retry failed TTS cues vào đúng tts_tmp/ để viet_dubbing.py skip được"
    )
    parser.add_argument("--srt",      required=True,
                        help="File SRT gốc")
    parser.add_argument("--tmp-dir",  default="tts_tmp",
                        help="Thư mục TTS cache (default: tts_tmp)")
    parser.add_argument("--log",      default=None,
                        help="File log cụ thể (default: log mới nhất trong logs/)")
    parser.add_argument("--voice",    default="female", choices=["female", "male"])
    parser.add_argument("--workers",  default=3, type=int,
                        help="Concurrent TTS requests (default: 3, thấp để tránh rate limit)")
    parser.add_argument("--retries",  default=5, type=int,
                        help="Số lần retry mỗi cue (default: 5)")
    args = parser.parse_args()

    voice_id, voice_label = VOICES[args.voice]
    workers = max(1, min(args.workers, 15))

    # ── HEADER ──────────────────────────────────────────────
    console.print()
    console.rule("[bold cyan]RETRY FAILED TTS v3[/]")
    info = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    info.add_column(style="dim", width=14)
    info.add_column(style="white")
    info.add_row("SRT",     args.srt)
    info.add_row("TMP dir", args.tmp_dir)
    info.add_row("Voice",   voice_label)
    info.add_row("Workers", f"{workers} concurrent")
    info.add_row("Retries", f"{args.retries} lần/cue")
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
    failed_cues = []
    source = ""

    # Ưu tiên 1: đọc log
    log_path = args.log or find_latest_log()
    if log_path and os.path.exists(log_path):
        failed_indices = parse_failed_indices_from_log(log_path)
        if failed_indices:
            failed_set  = set(failed_indices)
            failed_cues = [c for c in all_cues if c["index"] in failed_set]
            source = f"log ({os.path.basename(log_path)})"
            console.print(f"[dim]Nguồn: {log_path}[/]")

    # Fallback: scan thư mục tts_tmp
    if not failed_cues:
        if os.path.exists(args.tmp_dir):
            failed_cues = find_missing_by_scan(all_cues, args.tmp_dir)
            source = f"scan {args.tmp_dir}/"
            console.print(f"[dim]Không có log → scan {args.tmp_dir}/ để tìm file thiếu[/]")
        else:
            console.print(f"[red]ERROR: Không có log và thư mục {args.tmp_dir}/ cũng không tồn tại.[/]")
            console.print("[dim]Hãy comment dòng shutil.rmtree(tmp_dir) trong viet_dubbing.py rồi chạy lại.[/]")
            sys.exit(1)

    if not failed_cues:
        console.print("[green]✓ Không tìm thấy cue nào bị lỗi! Mọi thứ đã ổn.[/]")
        sys.exit(0)

    # Hiển thị danh sách
    console.print(f"[yellow]Tìm thấy [bold]{len(failed_cues)}[/] cue cần retry[/] [dim](nguồn: {source})[/]:\n")
    for cue in failed_cues[:20]:
        preview = cue["text"][:65] + ("..." if len(cue["text"]) > 65 else "")
        console.print(f"  [dim]#{cue['index']:04d}[/]  {preview}")
    if len(failed_cues) > 20:
        console.print(f"  [dim]... và {len(failed_cues) - 20} cue khác[/]")
    console.print()

    # ── RETRY ────────────────────────────────────────────────
    t_start = _time.perf_counter()
    success, fail = await retry_all(
        failed_cues, args.tmp_dir, voice_id, workers, args.retries
    )
    t_total = _time.perf_counter() - t_start

    # ── SUMMARY ─────────────────────────────────────────────
    console.print()
    console.rule("[bold green]KẾT QUẢ[/]")
    result = Table(box=box.ROUNDED, show_header=False,
                   border_style="green", padding=(0, 2))
    result.add_column(style="dim", width=18)
    result.add_column(style="bold white")
    result.add_row("Tổng cue retry",  str(len(failed_cues)))
    result.add_row("Thành công",      f"[green]{success}[/]")
    result.add_row("Vẫn lỗi",        f"[red]{fail}[/]" if fail else "[dim]0 ✓[/]")
    result.add_row("Thời gian",       f"{t_total:.1f}s")
    result.add_row("Đã lưu vào",      args.tmp_dir + "/")
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
        console.print(f"[dim]  --retries 8    Tăng số lần retry (hiện: {args.retries})[/]")
        console.print(f"[dim]  Chờ vài phút rồi chạy lại nếu bị rate limit[/]")

    console.print()


if __name__ == "__main__":
    asyncio.run(main())
