"""
=============================================================
  VIET DUBBING v1
  Auto Vietnamese dubbing from SRT subtitle file
=============================================================
Requirements:
  pip install edge-tts pydub rich -i https://pypi.org/simple
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
  --bgm-volume 80       BGM volume % to keep (default: 100)
  --out output.mp4      Output filename (auto-generated if not set)
  --audio-only          Export audio only, no video muxing
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
from pathlib import Path
from datetime import datetime

# Check dependencies
missing = []
try:
    import edge_tts
except ImportError:
    missing.append("edge-tts")

try:
    from pydub import AudioSegment
except ImportError:
    missing.append("pydub")

try:
    from rich.console import Console
    from rich.progress import (
        Progress, SpinnerColumn, BarColumn,
        TaskProgressColumn, TimeElapsedColumn,
        TextColumn, MofNCompleteColumn
    )
    from rich.table import Table
    from rich import box
    HAS_RICH = True
except ImportError:
    missing.append("rich")
    HAS_RICH = False

if missing:
    print(f"[ERROR] Missing packages: {', '.join(missing)}")
    print(f"Run: pip install {' '.join(missing)} -i https://pypi.org/simple")
    sys.exit(1)

console = Console()

# ============================================================
# VOICE LIST
# ============================================================
VOICES = {
    "female": ("vi-VN-HoaiMyNeural",  "HoaiMy  | Female | Southern accent"),
    "male":   ("vi-VN-NamMinhNeural", "NamMinh | Male   | Southern accent"),
}

# ============================================================
# CONFIG
# ============================================================
SPEED         = "+0%"
STRETCH_LIMIT = 2.0
COMPRESS_MIN  = 0.6
RETRY_COUNT   = 3
RETRY_DELAY   = 2
DELAY_BETWEEN = 0.3
# ============================================================


def make_output_name(video_path: str, suffix: str = "") -> str:
    """
    Tự động tạo tên file output theo tên video gốc + timestamp.
    Ví dụ: hoathinh.mp4 → hoathinh_dubbed_2025-01-15_14-30-22.mp4
    """
    stem      = Path(video_path).stem          # tên file không có đuôi
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    ext       = ".mp3" if suffix == "audio" else ".mp4"
    label     = "audio" if suffix == "audio" else "dubbed"
    return f"{stem}_{label}_{timestamp}{ext}"


def parse_srt_time(t: str) -> float:
    h, m, s_ms = t.strip().split(":")
    s, ms = s_ms.split(",")
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


async def tts_one(cue: dict, out_path: str, voice: str) -> bool:
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            comm = edge_tts.Communicate(text=cue["text"], voice=voice, rate=SPEED)
            await comm.save(out_path)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return True
        except Exception:
            if attempt < RETRY_COUNT:
                await asyncio.sleep(RETRY_DELAY)
    return False


async def generate_all_tts(cues: list, out_dir: str, voice: str) -> tuple:
    os.makedirs(out_dir, exist_ok=True)
    total   = len(cues)
    paths   = []
    success = 0
    fail    = 0
    skipped = 0

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
        task = progress.add_task("tts", total=total)

        for i, cue in enumerate(cues):
            out_path = os.path.join(out_dir, f"cue_{cue['index']:04d}.mp3")
            paths.append(out_path)

            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                success += 1
                skipped += 1
                progress.advance(task)
                continue

            ok = await tts_one(cue, out_path, voice)
            if ok:
                success += 1
            else:
                fail += 1

            progress.advance(task)
            await asyncio.sleep(DELAY_BETWEEN)

    return paths, success, fail, skipped


def stretch_clip(src_path: str, target_ms: int, idx: int) -> AudioSegment:
    clip  = AudioSegment.from_mp3(src_path)
    ratio = len(clip) / target_ms
    ratio = max(0.5, min(2.0, ratio))
    if 0.95 <= ratio <= 1.05:
        return clip
    tmp = src_path.replace(".mp3", f"_s{idx}.mp3")
    cmd = ["ffmpeg", "-y", "-i", src_path,
           "-filter:a", f"atempo={ratio:.4f}",
           "-vn", tmp, "-loglevel", "error"]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode == 0 and os.path.exists(tmp):
        out = AudioSegment.from_mp3(tmp)
        os.remove(tmp)
        return out
    return clip


def build_tts_track(cues: list, tts_paths: list, total_ms: int) -> tuple:
    track  = AudioSegment.silent(duration=total_ms)
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
        task = progress.add_task("sync", total=len(cues))

        for cue, tts_path in zip(cues, tts_paths):
            if not os.path.exists(tts_path) or os.path.getsize(tts_path) == 0:
                progress.advance(task)
                continue
            try:
                clip = AudioSegment.from_mp3(tts_path)
            except Exception:
                progress.advance(task)
                continue

            slot_ms = (cue["end"] - cue["start"]) * 1000
            clip_ms = len(clip)
            if clip_ms == 0 or slot_ms == 0:
                progress.advance(task)
                continue

            ratio = clip_ms / slot_ms
            if ratio > COMPRESS_MIN:
                target_ms = int(slot_ms) if ratio <= STRETCH_LIMIT else int(clip_ms / STRETCH_LIMIT)
                clip = stretch_clip(tts_path, target_ms, cue["index"])

            track = track.overlay(clip, position=int(cue["start"] * 1000))
            synced += 1
            progress.advance(task)

    return track, synced


def mix_with_bgm(video_path: str, tts_track: AudioSegment,
                 bgm_volume: int, total_ms: int, tmp_dir: str) -> AudioSegment:
    orig_path = os.path.join(tmp_dir, "bgm.mp3")
    cmd = ["ffmpeg", "-y", "-i", video_path,
           "-vn", "-acodec", "mp3", "-ab", "192k",
           orig_path, "-loglevel", "error"]
    result = subprocess.run(cmd, capture_output=True)

    if result.returncode != 0 or not os.path.exists(orig_path):
        console.print("  [yellow]No original audio found, using TTS only.[/]")
        return tts_track

    original = AudioSegment.from_mp3(orig_path)
    if len(original) < total_ms:
        original = original + AudioSegment.silent(duration=total_ms - len(original))
    else:
        original = original[:total_ms]

    if bgm_volume < 100:
        db = 20 * math.log10(max(bgm_volume, 1) / 100)
        original = original + db

    return original.overlay(tts_track)


def mux_to_video(video_path: str, audio_path: str, output_path: str) -> bool:
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
        "-loglevel", "error"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def format_duration(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}m {s:02d}s" if m > 0 else f"{s}s"


async def main():
    parser = argparse.ArgumentParser(description="Viet Dubbing - Auto Vietnamese TTS from SRT")
    parser.add_argument("--srt",        required=True)
    parser.add_argument("--video",      required=False, default=None)
    parser.add_argument("--out",        required=False, default=None,
                        help="Output filename (auto-generated from video name if not set)")
    parser.add_argument("--voice",      required=False, default="female",
                        choices=["female", "male"])
    parser.add_argument("--bgm-volume", required=False, default=100, type=int)
    parser.add_argument("--audio-only", action="store_true")
    args = parser.parse_args()

    voice_id, voice_label = VOICES[args.voice]

    # Tự động tạo tên output nếu không truyền --out
    base_name  = args.video if args.video else args.srt
    video_out  = args.out if args.out else make_output_name(base_name)
    audio_out  = make_output_name(base_name, suffix="audio")

    # ── HEADER ──────────────────────────────────────────────
    console.print()
    console.rule("[bold cyan]VIET DUBBING v4[/]")

    info = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    info.add_column(style="dim", width=14)
    info.add_column(style="white")
    info.add_row("SRT",        args.srt)
    info.add_row("Video",      args.video or "(none)")
    info.add_row("Voice",      voice_label)
    info.add_row("BGM volume", f"{args.bgm_volume}%")
    info.add_row("Output",     video_out)
    console.print(info)
    console.rule()
    console.print()

    # ── READ SRT ────────────────────────────────────────────
    with console.status("[cyan]Reading SRT file...[/]"):
        cues = parse_srt(args.srt)

    if not cues:
        console.print("[red]ERROR: Could not parse subtitle file.[/]")
        return

    duration_str = format_duration(cues[-1]["end"])
    console.print(f"[green]✓[/] Found [bold]{len(cues)}[/] subtitle lines  "
                  f"[dim](duration ~{duration_str})[/]\n")

    # ── VIDEO DURATION ──────────────────────────────────────
    if args.video and os.path.exists(args.video):
        with console.status("[cyan]Reading video duration...[/]"):
            try:
                total_sec = float(subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", args.video],
                    capture_output=True, text=True
                ).stdout.strip())
            except Exception:
                total_sec = cues[-1]["end"] + 2
    else:
        total_sec = cues[-1]["end"] + 2
    total_ms = int(total_sec * 1000)

    est_min = max(1, int(len(cues) * DELAY_BETWEEN / 60))
    console.print(f"[dim]Estimated TTS generation: ~{est_min} min[/]\n")

    tmp_dir = "tts_tmp"

    # ── GENERATE TTS ────────────────────────────────────────
    tts_paths, success, fail, skipped = await generate_all_tts(cues, tmp_dir, voice_id)

    summary = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    summary.add_column(style="dim", width=20)
    summary.add_column()
    summary.add_row("Succeeded", f"[green]{success}[/]")
    summary.add_row("Skipped",   f"[cyan]{skipped}[/]")
    summary.add_row("Failed",    f"[red]{fail}[/]" if fail else "[dim]0[/]")
    console.print(summary)
    console.print()

    # ── SYNC TIMELINE ───────────────────────────────────────
    tts_track, synced = build_tts_track(cues, tts_paths, total_ms)
    console.print(f"\n[green]✓[/] Synced [bold]{synced}[/] segments to timeline\n")

    # ── MIX WITH BGM ────────────────────────────────────────
    final_track = tts_track
    if args.video and os.path.exists(args.video) and not args.audio_only:
        with console.status("[magenta]Mixing with original audio...[/]"):
            final_track = mix_with_bgm(args.video, tts_track, args.bgm_volume, total_ms, tmp_dir)
        console.print(f"[green]✓[/] Audio mixed  [dim](BGM: {args.bgm_volume}%)[/]\n")

    # ── EXPORT AUDIO ────────────────────────────────────────
    with console.status("[yellow]Exporting audio...[/]"):
        final_track.export(audio_out, format="mp3", bitrate="192k")
    console.print(f"[green]✓[/] Audio exported: [bold]{audio_out}[/]\n")

    # ── MUX VIDEO ───────────────────────────────────────────
    if not args.audio_only and args.video and os.path.exists(args.video):
        with console.status("[yellow]Muxing audio into video...[/]"):
            ok = mux_to_video(args.video, audio_out, video_out)
        if ok:
            console.print(f"[green]✓[/] Video muxed: [bold]{video_out}[/]\n")
        else:
            console.print(f"[red]✗[/] Video mux failed. Check FFmpeg installation.\n")

    # ── CLEANUP ─────────────────────────────────────────────
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)

    # ── FINAL SUMMARY ───────────────────────────────────────
    console.print()
    console.rule("[bold green]DONE[/]")

    result = Table(box=box.ROUNDED, show_header=False,
                   border_style="green", padding=(0, 2))
    result.add_column(style="dim", width=16)
    result.add_column(style="bold white")
    if not args.audio_only and args.video:
        result.add_row("Video output", video_out)
    result.add_row("Audio output", audio_out)
    result.add_row("Lines synced", str(synced))
    console.print(result)

    console.print()
    console.print("[dim]Tips:[/]")
    console.print("[dim]  --voice male          Switch to male voice[/]")
    console.print("[dim]  --voice female        Switch to female voice (default)[/]")
    console.print("[dim]  --bgm-volume 80       Reduce BGM to 80%[/]")
    console.print("[dim]  --bgm-volume 0        Mute original audio completely[/]")
    console.print()


if __name__ == "__main__":
    asyncio.run(main())
