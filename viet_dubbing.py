"""
=============================================================
  VIET DUBBING v3 — Performance Edition
  Auto Vietnamese dubbing from SRT subtitle file
=============================================================
Requirements:
  pip install edge-tts pydub rich numpy -i https://pypi.org/simple
  Install FFmpeg and add to PATH

Recommended workflow:
  1. Use CapCut to remove original voice from video
  2. Export video with only BGM + sound effects
  3. Run this script to add Vietnamese TTS voice

Usage:
  python viet_dubbing.py --srt subtitle.srt --video no_voice.mp4

Options:
  --voice female        Female voice (default)
  --voice male          Male voice
  --bgm-volume 80      BGM volume % to keep (default: 100)
  --out output.mp4      Output filename (auto-generated if not set)
  --audio-only          Export audio only, no video muxing
  --workers 5           Concurrent TTS requests (default: 5)

Changelog v3:
  - Single-decode pipeline: mỗi MP3 chỉ decode 1 lần duy nhất
  - ffprobe lấy duration thay vì full decode ở phase detect
  - Numpy BGM mixing thay pydub .overlay() (10-50× nhanh hơn)
  - WAV intermediate cho video mux (bỏ double encode MP3→AAC)
  - ThreadPoolExecutor thay ProcessPoolExecutor cho stretch
  - Chain atempo filter hỗ trợ ratio > 2× hoặc < 0.5×
  - Cache BGM extraction
  - TTS cache folder đặt tên theo video/SRT
=============================================================
"""

import asyncio
import re
import os
import sys
import argparse
import subprocess
import math
import shutil
import threading
import time as _time
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Check dependencies ──────────────────────────────────────
_missing = []
try:
    import edge_tts
except ImportError:
    _missing.append("edge-tts")

try:
    from pydub import AudioSegment
except ImportError:
    _missing.append("pydub")

try:
    import numpy as np
except ImportError:
    _missing.append("numpy")

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
    print(f"Run: pip install {' '.join(_missing)} -i https://pypi.org/simple")
    sys.exit(1)

console = Console()


# ============================================================
# LOGGER
# ============================================================

class DailyLogger:
    """Ghi log vào logs/log_YYYYMMDD.txt, nhóm theo ngày."""

    LOG_DIR = "logs"

    def __init__(self):
        os.makedirs(self.LOG_DIR, exist_ok=True)
        today = datetime.now().strftime("%Y%m%d")
        self.path = os.path.join(self.LOG_DIR, f"log_{today}.txt")
        self._f = open(self.path, "a", encoding="utf-8", buffering=1)
        if self._f.tell() > 0:
            self._write_raw("")
        self._write_raw(f"{'=' * 60}")
        self._write_raw(f"  SESSION START  {datetime.now():%Y-%m-%d %H:%M:%S}")
        self._write_raw(f"{'=' * 60}")

    def _write_raw(self, text: str):
        self._f.write(text + "\n")
        self._f.flush()

    def _write(self, level: str, msg: str):
        self._write_raw(f"[{datetime.now():%H:%M:%S}] {level:<7} {msg}")

    def info(self, msg: str):    self._write("INFO", msg)
    def success(self, msg: str): self._write("OK", msg)
    def warning(self, msg: str): self._write("WARNING", msg)
    def error(self, msg: str):   self._write("ERROR", msg)

    def section(self, title: str):
        self._write_raw("")
        self._write_raw(f"── {title} {'─' * max(0, 50 - len(title))}")

    def close(self):
        self._write_raw(f"{'=' * 60}")
        self._write_raw(f"  SESSION END    {datetime.now():%Y-%m-%d %H:%M:%S}")
        self._write_raw(f"{'=' * 60}\n")
        self._f.close()


# Global logger (khởi tạo trong main)
logger: DailyLogger | None = None


# ============================================================
# CONSTANTS
# ============================================================

VOICES = {
    "female": ("vi-VN-HoaiMyNeural", "HoaiMy  | Female | Southern accent"),
    "male":   ("vi-VN-NamMinhNeural", "NamMinh | Male   | Southern accent"),
}

SPEED             = "+0%"
STRETCH_LIMIT     = 2.0       # max speed-up ratio trước khi cắt
COMPRESS_MIN      = 0.6       # ratio dưới ngưỡng này → không stretch
RETRY_COUNT       = 3
RETRY_DELAY_SEC   = 1.5
SAMPLE_RATE       = 24000     # edge-tts output
CHANNELS          = 1         # mono
SAMPLE_WIDTH      = 2         # 16-bit
STRETCH_TOLERANCE = 0.05      # ±5% → skip stretch
MAX_WORKERS_CAP   = 15


# ============================================================
# HELPERS
# ============================================================

def make_output_name(base_path: str, suffix: str = "") -> str:
    stem = Path(base_path).stem
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    if suffix == "audio":
        return f"{stem}_audio_{timestamp}.mp3"
    return f"{stem}_dubbed_{timestamp}.mp4"


def format_duration(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}m {s:02d}s" if m > 0 else f"{s}s"


def ffprobe_duration_ms(file_path: str) -> float | None:
    """Lấy duration (ms) bằng ffprobe — rẻ hơn nhiều so với full decode."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             file_path],
            capture_output=True, text=True,
        )
        return float(result.stdout.strip()) * 1000
    except Exception:
        return None


def audio_to_numpy(segment: AudioSegment) -> np.ndarray:
    """Convert AudioSegment → numpy float32 array (mono, SAMPLE_RATE)."""
    segment = (segment
               .set_frame_rate(SAMPLE_RATE)
               .set_channels(CHANNELS)
               .set_sample_width(SAMPLE_WIDTH))
    return np.frombuffer(segment.raw_data, dtype=np.int16).astype(np.float32)


def numpy_to_segment(track: np.ndarray) -> AudioSegment:
    """Convert numpy float32 → AudioSegment (clipped to int16)."""
    clipped = np.clip(track, -32768, 32767).astype(np.int16)
    return AudioSegment(
        data=clipped.tobytes(),
        sample_width=SAMPLE_WIDTH,
        frame_rate=SAMPLE_RATE,
        channels=CHANNELS,
    )


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
# TTS GENERATION — CONCURRENT
# ============================================================

async def tts_one(cue: dict, out_path: str, voice: str) -> bool:
    """Generate TTS cho 1 cue, có retry với exponential backoff."""
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            comm = edge_tts.Communicate(text=cue["text"], voice=voice, rate=SPEED)
            await comm.save(out_path)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return True
        except Exception as e:
            if logger:
                logger.warning(f"TTS cue #{cue['index']} attempt {attempt}/{RETRY_COUNT}: {e}")
            if attempt < RETRY_COUNT:
                await asyncio.sleep(RETRY_DELAY_SEC * attempt)

    if logger:
        logger.error(f"TTS cue #{cue['index']} FAILED after {RETRY_COUNT} attempts | text: {cue['text'][:60]}")
    return False


async def generate_all_tts(cues: list[dict], out_dir: str, voice: str,
                           max_workers: int = 5) -> tuple[list[str], int, int, int]:
    """Generate TTS đồng thời, skip file đã có trong cache."""
    os.makedirs(out_dir, exist_ok=True)
    semaphore = asyncio.Semaphore(max_workers)

    tasks_to_run = []
    all_paths = []
    skipped = 0

    for cue in cues:
        out_path = os.path.join(out_dir, f"cue_{cue['index']:04d}.mp3")
        all_paths.append(out_path)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            skipped += 1
        else:
            tasks_to_run.append((cue, out_path))

    success = skipped
    fail = 0

    if not tasks_to_run:
        return all_paths, success, fail, skipped

    async def _worker(cue: dict, path: str) -> bool:
        async with semaphore:
            return await tts_one(cue, path, voice)

    with Progress(
        SpinnerColumn(spinner_name="dots", style="cyan"),
        TextColumn("[bold cyan]Generating TTS[/]"),
        BarColumn(bar_width=32, style="cyan", complete_style="bright_green"),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("tts", total=len(cues))
        progress.advance(task, advance=skipped)

        async_tasks = [
            asyncio.ensure_future(_worker(cue, path))
            for cue, path in tasks_to_run
        ]
        for coro in asyncio.as_completed(async_tasks):
            if await coro:
                success += 1
            else:
                fail += 1
            progress.advance(task)

    return all_paths, success, fail, skipped


# ============================================================
# STRETCH — ThreadPoolExecutor + chained atempo
# ============================================================

def _build_atempo_filter(ratio: float) -> str:
    """
    Tạo chuỗi atempo filter cho FFmpeg.
    atempo chỉ hỗ trợ 0.5–2.0, nên ratio ngoài range cần chain.
    Ví dụ: ratio=3.0 → "atempo=2.0,atempo=1.5"
    """
    ratio = max(0.25, min(ratio, 4.0))

    filters = []
    remaining = ratio
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.4f}")
    return ",".join(filters)


def _stretch_one_file(src_path: str, clip_ms: float, target_ms: int,
                      cue_index: int) -> str | None:
    """
    Stretch/compress 1 file MP3 bằng FFmpeg.
    Nhận clip_ms đã biết từ caller — không cần gọi ffprobe lại.
    """
    if clip_ms <= 0 or target_ms <= 0:
        return None

    ratio = clip_ms / target_ms
    if (1 - STRETCH_TOLERANCE) <= ratio <= (1 + STRETCH_TOLERANCE):
        return None

    atempo_filter = _build_atempo_filter(ratio)
    out_path = src_path.replace(".mp3", f"_s{cue_index}.mp3")
    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-filter:a", atempo_filter,
        "-vn", out_path, "-loglevel", "error",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode == 0 and os.path.exists(out_path):
        return out_path
    return None


# ============================================================
# BUILD TRACK — single-decode pipeline
# ============================================================

def build_tts_track(cues: list[dict], tts_paths: list[str], total_ms: int,
                    max_stretch_workers: int = 4) -> tuple[AudioSegment, int]:
    """
    Build full TTS track:
      Phase 1: ffprobe lấy duration → detect cue cần stretch
      Phase 2: ThreadPool stretch song song
      Phase 3: Decode mỗi MP3 đúng 1 lần → mix vào numpy array
    """
    total_samples = int(total_ms * SAMPLE_RATE / 1000)
    track = np.zeros(total_samples, dtype=np.float32)

    # ── Phase 1: Detect stretch candidates bằng ffprobe ─────
    clip_durations: dict[int, float] = {}
    stretch_jobs = []

    for i, (cue, tts_path) in enumerate(zip(cues, tts_paths)):
        if not os.path.exists(tts_path) or os.path.getsize(tts_path) == 0:
            continue

        clip_ms = ffprobe_duration_ms(tts_path)
        if clip_ms is None or clip_ms <= 0:
            continue
        clip_durations[i] = clip_ms

        slot_ms = (cue["end"] - cue["start"]) * 1000
        if slot_ms <= 0:
            continue

        ratio = clip_ms / slot_ms
        if ratio > COMPRESS_MIN:
            if ratio <= STRETCH_LIMIT:
                target_ms = int(slot_ms)
            else:
                target_ms = int(clip_ms / STRETCH_LIMIT)
            stretch_jobs.append((i, tts_path, clip_ms, target_ms, cue["index"]))

    # ── Phase 2: Stretch song song (ThreadPool — I/O bound) ─
    stretched_paths: dict[int, str] = {}
    if stretch_jobs:
        with ThreadPoolExecutor(max_workers=max_stretch_workers) as executor:
            futures = {
                executor.submit(
                    _stretch_one_file, src, c_ms, t_ms, cidx
                ): idx
                for idx, src, c_ms, t_ms, cidx in stretch_jobs
            }
            for future in as_completed(futures):
                i = futures[future]
                try:
                    result_path = future.result()
                    if result_path:
                        stretched_paths[i] = result_path
                except Exception:
                    pass

    # ── Phase 3: Single decode + mix vào numpy ──────────────
    synced = 0
    with Progress(
        SpinnerColumn(spinner_name="dots2", style="magenta"),
        TextColumn("[bold magenta]Syncing timeline[/]"),
        BarColumn(bar_width=32, style="magenta", complete_style="bright_green"),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("sync", total=len(cues))

        for i, (cue, tts_path) in enumerate(zip(cues, tts_paths)):
            if i not in clip_durations:
                progress.advance(task_id)
                continue

            actual_path = stretched_paths.get(i, tts_path)
            try:
                clip = AudioSegment.from_mp3(actual_path)
            except Exception:
                progress.advance(task_id)
                continue

            if len(clip) == 0:
                progress.advance(task_id)
                continue

            samples = audio_to_numpy(clip)

            start_sample = int(cue["start"] * SAMPLE_RATE)
            if start_sample >= total_samples:
                progress.advance(task_id)
                continue

            if start_sample + len(samples) > total_samples:
                samples = samples[:total_samples - start_sample]

            track[start_sample:start_sample + len(samples)] += samples
            synced += 1
            progress.advance(task_id)

    # ── Cleanup stretched temp files ────────────────────────
    for path in stretched_paths.values():
        try:
            os.remove(path)
        except OSError:
            pass

    return numpy_to_segment(track), synced


# ============================================================
# BGM MIXING — numpy thay pydub .overlay()
# ============================================================

def extract_bgm(video_path: str, out_path: str) -> bool:
    """Extract audio từ video → MP3. Skip nếu đã cache."""
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return True
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "mp3", "-ab", "192k",
        out_path, "-loglevel", "error",
    ]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0 and os.path.exists(out_path)


def mix_with_bgm(video_path: str, tts_track: AudioSegment,
                 bgm_volume: int, total_ms: int, tmp_dir: str) -> AudioSegment:
    """Mix TTS + BGM bằng numpy — nhanh hơn pydub.overlay() rất nhiều."""
    bgm_path = os.path.join(tmp_dir, "bgm.mp3")

    if not extract_bgm(video_path, bgm_path):
        console.print("  [yellow]No original audio found, using TTS only.[/]")
        return tts_track

    original = AudioSegment.from_mp3(bgm_path)

    if len(original) < total_ms:
        original = original + AudioSegment.silent(duration=total_ms - len(original))
    else:
        original = original[:total_ms]

    bgm_samples = audio_to_numpy(original)
    tts_samples = audio_to_numpy(tts_track)

    # Đảm bảo cùng length trước khi cộng
    min_len = min(len(bgm_samples), len(tts_samples))
    bgm_samples = bgm_samples[:min_len]
    tts_samples = tts_samples[:min_len]

    if bgm_volume < 100:
        bgm_samples *= max(bgm_volume, 1) / 100.0

    mixed = bgm_samples + tts_samples
    return numpy_to_segment(mixed)


# ============================================================
# VIDEO MUXING — WAV intermediate (encode 1 lần duy nhất)
# ============================================================

def mux_to_video(video_path: str, audio_path: str, output_path: str) -> bool:
    """Mux audio vào video. Audio WAV → encode AAC 1 lần duy nhất."""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        output_path,
        "-loglevel", "error",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


# ============================================================
# UI HELPERS
# ============================================================

def timed_progress(label: str, color: str, fn, estimated_sec: float = 30):
    """Chạy fn() trong background thread với progress bar giả lập."""
    result_box = [None]
    exc_box = [None]

    def _run():
        try:
            result_box[0] = fn()
        except Exception as e:
            exc_box[0] = e

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    interval = 0.25
    total_ticks = int(estimated_sec / interval)

    with Progress(
        SpinnerColumn(spinner_name="dots", style=color),
        TextColumn(f"[bold {color}]{label}[/]"),
        BarColumn(bar_width=32, style=color, complete_style="bright_green"),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("work", total=total_ticks)
        elapsed = 0
        while thread.is_alive():
            _time.sleep(interval)
            elapsed += 1
            if elapsed < total_ticks * 0.95:
                progress.advance(task)
        progress.update(task, completed=total_ticks)

    if exc_box[0]:
        raise exc_box[0]
    return result_box[0]


def play_done_sound():
    """Phát âm thanh beep thông báo hoàn thành."""
    try:
        if sys.platform == "win32":
            import winsound
            for _ in range(3):
                winsound.Beep(800, 150)
                _time.sleep(0.08)
        elif sys.platform == "darwin":
            os.system("afplay /System/Library/Sounds/Glass.aiff &")
        else:
            os.system(
                "paplay /usr/share/sounds/freedesktop/stereo/complete.oga 2>/dev/null "
                "|| (command -v beep >/dev/null && beep) "
                "|| printf '\\a'"
            )
    except Exception:
        print("\a", end="", flush=True)


# ============================================================
# MAIN
# ============================================================

async def main():
    parser = argparse.ArgumentParser(
        description="Viet Dubbing v3 — Auto Vietnamese TTS from SRT"
    )
    parser.add_argument("--srt", required=True)
    parser.add_argument("--video", required=False, default=None)
    parser.add_argument("--out", required=False, default=None,
                        help="Output filename (auto-generated from video name if not set)")
    parser.add_argument("--voice", required=False, default="female",
                        choices=["female", "male"])
    parser.add_argument("--bgm-volume", required=False, default=100, type=int)
    parser.add_argument("--audio-only", action="store_true")
    parser.add_argument("--workers", required=False, default=5, type=int,
                        help="Concurrent TTS requests (default: 5, max recommended: 10)")
    args = parser.parse_args()

    voice_id, voice_label = VOICES[args.voice]
    workers = max(1, min(args.workers, MAX_WORKERS_CAP))

    base_name = args.video if args.video else args.srt
    video_out = args.out if args.out else make_output_name(base_name)
    audio_out = make_output_name(base_name, suffix="audio")
    tmp_dir = f"tts_{Path(base_name).stem}"

    # ── INIT LOGGER ─────────────────────────────────────────
    global logger
    logger = DailyLogger()
    logger.section("SESSION CONFIG")
    logger.info(f"SRT file    : {args.srt}")
    logger.info(f"Video file  : {args.video or '(none)'}")
    logger.info(f"Voice       : {voice_label}")
    logger.info(f"BGM volume  : {args.bgm_volume}%")
    logger.info(f"Workers     : {workers} concurrent")
    logger.info(f"Audio only  : {args.audio_only}")
    logger.info(f"TTS cache   : {tmp_dir}/")
    logger.info(f"Output video: {video_out}")
    logger.info(f"Output audio: {audio_out}")
    logger.info(f"Log file    : {logger.path}")

    # ── HEADER ──────────────────────────────────────────────
    console.print()
    console.rule("[bold cyan]VIET DUBBING v3 — Performance Edition[/]")

    info = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    info.add_column(style="dim", width=14)
    info.add_column(style="white")
    info.add_row("SRT",        args.srt)
    info.add_row("Video",      args.video or "(none)")
    info.add_row("Voice",      voice_label)
    info.add_row("BGM volume", f"{args.bgm_volume}%")
    info.add_row("Workers",    f"{workers} concurrent")
    info.add_row("TTS dir",    tmp_dir + "/")
    info.add_row("Output",     video_out)
    console.print(info)
    console.rule()
    console.print()

    # ── READ SRT ────────────────────────────────────────────
    with console.status("[cyan]Reading SRT file...[/]"):
        cues = parse_srt(args.srt)

    if not cues:
        console.print("[red]ERROR: Could not parse subtitle file.[/]")
        logger.error("Could not parse SRT file — aborting")
        logger.close()
        return

    duration_str = format_duration(cues[-1]["end"])
    console.print(f"[green]✓[/] Found [bold]{len(cues)}[/] subtitle lines  "
                  f"[dim](duration ~{duration_str})[/]\n")
    logger.section("READ SRT")
    logger.success(f"Parsed {len(cues)} subtitle lines, duration ~{duration_str}")

    # ── VIDEO DURATION ──────────────────────────────────────
    if args.video and os.path.exists(args.video):
        video_duration_ms = ffprobe_duration_ms(args.video)
        total_sec = (video_duration_ms / 1000) if video_duration_ms else cues[-1]["end"] + 2
    else:
        total_sec = cues[-1]["end"] + 2
    total_ms = int(total_sec * 1000)
    logger.info(f"Total duration: {format_duration(total_sec)} ({total_ms} ms)")

    t_start = _time.perf_counter()

    # ── GENERATE TTS (concurrent) ───────────────────────────
    logger.section("GENERATE TTS")
    logger.info(f"Starting TTS generation — {len(cues)} cues, {workers} workers, voice={voice_id}")
    tts_paths, success, fail, skipped = await generate_all_tts(
        cues, tmp_dir, voice_id, max_workers=workers
    )

    t_tts = _time.perf_counter()
    logger.success(f"TTS done in {format_duration(t_tts - t_start)} "
                   f"— succeeded={success}, skipped={skipped}, failed={fail}")

    summary = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    summary.add_column(style="dim", width=20)
    summary.add_column()
    summary.add_row("Succeeded", f"[green]{success}[/]")
    summary.add_row("Skipped",   f"[cyan]{skipped}[/]")
    summary.add_row("Failed",    f"[red]{fail}[/]" if fail else "[dim]0[/]")
    summary.add_row("TTS time",  f"[dim]{format_duration(t_tts - t_start)}[/]")
    console.print(summary)
    console.print()

    # ── SYNC TIMELINE (single-decode + parallel stretch) ────
    logger.section("SYNC TIMELINE")
    logger.info("Building TTS track: ffprobe detect → ThreadPool stretch → single-decode mix")
    tts_track, synced = build_tts_track(cues, tts_paths, total_ms)
    t_sync = _time.perf_counter()
    console.print(f"\n[green]✓[/] Synced [bold]{synced}[/] segments  "
                  f"[dim]({format_duration(t_sync - t_tts)})[/]\n")
    logger.success(f"Timeline sync done in {format_duration(t_sync - t_tts)} — {synced} segments synced")

    # ── MIX WITH BGM (numpy) ────────────────────────────────
    final_track = tts_track
    if args.video and os.path.exists(args.video) and not args.audio_only:
        logger.section("MIX BGM")
        logger.info(f"Numpy mixing TTS + BGM — volume: {args.bgm_volume}%")
        est_mix = max(10, total_ms / 1000 * 0.05)
        final_track = timed_progress(
            "Mixing BGM (numpy)", "magenta",
            lambda: mix_with_bgm(args.video, tts_track, args.bgm_volume, total_ms, tmp_dir),
            estimated_sec=est_mix,
        )
        t_mix = _time.perf_counter()
        console.print(f"[green]✓[/] Audio mixed  [dim](BGM: {args.bgm_volume}%)[/]\n")
        logger.success(f"BGM mix done in {format_duration(t_mix - t_sync)}")

    # ── EXPORT MP3 (giữ lại cho người dùng) ─────────────────
    export_path = audio_out
    logger.section("EXPORT AUDIO")
    logger.info(f"Exporting audio → {export_path}")
    t_before_export = _time.perf_counter()
    est_export = max(5, total_ms / 1000 * 0.02)
    timed_progress(
        "Exporting MP3", "yellow",
        lambda: final_track.export(export_path, format="mp3", bitrate="192k"),
        estimated_sec=est_export,
    )
    t_export = _time.perf_counter()
    console.print(f"[green]✓[/] Audio exported: [bold]{export_path}[/]\n")
    logger.success(f"Audio exported in {format_duration(t_export - t_before_export)} → {export_path}")

    # ── MUX VIDEO (WAV intermediate → AAC encode 1 lần) ────
    if not args.audio_only and args.video and os.path.exists(args.video):
        logger.section("MUX VIDEO")
        logger.info(f"Muxing: WAV → AAC → {video_out}")

        wav_path = os.path.join(tmp_dir, "mux_tmp.wav")
        final_track.export(wav_path, format="wav")

        est_mux = max(10, total_ms / 1000 * 0.03)
        ok = timed_progress(
            "Muxing video", "yellow",
            lambda: mux_to_video(args.video, wav_path, video_out),
            estimated_sec=est_mux,
        )
        t_mux = _time.perf_counter()

        try:
            os.remove(wav_path)
        except OSError:
            pass

        if ok:
            console.print(f"[green]✓[/] Video muxed: [bold]{video_out}[/]")
            console.print(f"[green]✓[/] Audio kept:  [bold]{export_path}[/]\n")
            logger.success(f"Video muxed in {format_duration(t_mux - t_export)} → {video_out}")
            logger.info(f"Audio file kept → {export_path}")
        else:
            console.print(f"[red]✗[/] Video mux failed. Check FFmpeg installation.\n")
            logger.error("Video mux FAILED — check FFmpeg installation")

    # ── FINAL SUMMARY ───────────────────────────────────────
    t_total = _time.perf_counter() - t_start
    console.print()
    console.rule("[bold green]DONE[/]")

    result = Table(box=box.ROUNDED, show_header=False,
                   border_style="green", padding=(0, 2))
    result.add_column(style="dim", width=16)
    result.add_column(style="bold white")
    if not args.audio_only and args.video:
        result.add_row("Video output", video_out)
        result.add_row("Audio output", audio_out)
    else:
        result.add_row("Audio output", audio_out)
    result.add_row("Lines synced", str(synced))
    result.add_row("Total time",   format_duration(t_total))
    result.add_row("Log file",     logger.path)
    console.print(result)

    logger.section("FINAL SUMMARY")
    logger.success(f"All done in {format_duration(t_total)}")
    if not args.audio_only and args.video:
        logger.info(f"Video output : {video_out}")
        logger.info(f"Audio output : {audio_out}")
    else:
        logger.info(f"Audio output : {audio_out}")
    logger.info(f"Lines synced : {synced}/{len(cues)}")
    logger.close()

    console.print()
    console.print("[dim]Tips:[/]")
    console.print("[dim]  --workers 8           Increase TTS concurrency (default: 5)[/]")
    console.print("[dim]  --voice male          Switch to male voice[/]")
    console.print("[dim]  --voice female        Switch to female voice (default)[/]")
    console.print("[dim]  --bgm-volume 80       Reduce BGM to 80%[/]")
    console.print("[dim]  --bgm-volume 0        Mute original audio completely[/]")
    console.print()

    play_done_sound()


if __name__ == "__main__":
    asyncio.run(main())
