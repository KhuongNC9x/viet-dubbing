# 🎙️ Viet Dubbing v3

**Auto Vietnamese dubbing from SRT subtitle using Microsoft Edge TTS**

Automatically generate Vietnamese voice-over from `.srt` subtitle files and sync it with your video. Built for content creators who work with Chinese animated videos and want to add Vietnamese dubbing quickly and for free.

---

## ✨ Features

- 🆓 **100% Free** — uses Microsoft Edge TTS (no API key required)
- 🎙️ **Natural Vietnamese voices** — female & male options
- ⏱️ **Auto time-sync** — automatically stretches/compresses each audio clip to fit the subtitle timestamp
- 🎵 **Preserves original audio** — BGM and sound effects are kept, only voice is replaced
- ⚡ **High performance** — single-decode pipeline, numpy mixing, parallel stretch
- 📊 **Beautiful CLI progress** — real-time progress bar with rich
- 🔁 **Auto retry** — retries failed lines automatically
- ▶️ **Resume support** — if interrupted, continues from where it left off
- 🏷️ **Auto output naming** — output file named after source video + timestamp
- 📂 **Smart TTS caching** — TTS cache folder named after video for easy management

---

## 🔄 What's new in v3

| Improvement | Detail |
|---|---|
| **Single-decode pipeline** | Mỗi MP3 chỉ decode 1 lần — trước đó decode 2-3 lần/file |
| **ffprobe duration detect** | Dùng ffprobe lấy duration thay vì full decode ở phase detect |
| **Numpy BGM mixing** | Thay `pydub.overlay()` bằng numpy vectorized — nhanh hơn 10-50× |
| **WAV intermediate muxing** | Bỏ double encode MP3→AAC, dùng WAV→AAC encode 1 lần |
| **ThreadPool stretch** | Thay ProcessPoolExecutor bằng ThreadPool cho I/O-bound FFmpeg |
| **Chained atempo filter** | Hỗ trợ ratio > 2× hoặc < 0.5× (ví dụ: `atempo=2.0,atempo=1.5` cho 3×) |
| **BGM cache** | Skip extract BGM nếu file đã tồn tại từ lần chạy trước |
| **Smart TTS folder** | TTS cache đặt tên theo video (ví dụ: `tts_episode01/`) |
| **Retry script v4** | Đồng bộ naming, extract helpers, validate args, fix backoff |

---

## 🔧 Requirements

| Component | Version | Notes |
|---|---|---|
| Python | 3.10+ | Must add to PATH during install |
| FFmpeg | Any | Must add `/bin` folder to PATH manually |
| edge-tts | Latest | Microsoft TTS library |
| pydub | 0.25+ | Audio processing library |
| numpy | Latest | Fast array operations for audio mixing |
| rich | Latest | CLI progress UI |

---

## 📦 Installation

### 1. Install Python

Download from **[python.org/downloads](https://www.python.org/downloads/)**

> ⚠️ **Important:** Check **"Add Python to PATH"** before clicking Install Now.

Verify installation:

```bash
python --version
```

### 2. Install FFmpeg

Download from **[github.com/BtbN/FFmpeg-Builds/releases](https://github.com/BtbN/FFmpeg-Builds/releases)**

Get: `ffmpeg-master-latest-win64-gpl.zip` → extract to any folder (e.g. `D:\ffmpeg`)

Add the `bin` folder to your system PATH:

- Search **"Environment Variables"** in Windows
- Edit **Path** under User variables → **New** → paste path to `\bin` folder (e.g. `D:\ffmpeg-master-latest-win64-gpl\bin`)
- Click **OK → OK → OK**

Open a **new** CMD window and verify:

```bash
ffmpeg -version
```

### 3. Install Python packages

```bash
pip install edge-tts pydub rich numpy -i https://pypi.org/simple
```

> 💡 Always use `-i https://pypi.org/simple` to avoid connection issues with default mirrors in Vietnam.

---

## 🚀 Usage

### Recommended Workflow

```
1. Download Chinese animated video from YouTube
        ↓
2. Translate .srt subtitle to Vietnamese
        ↓
3. Use CapCut AI Voice Remover → export video (BGM + SFX only, no voice)
        ↓
4. Run viet_dubbing.py → auto-generate Vietnamese TTS + merge into video
        ↓
5. Get episode01_dubbed_[timestamp].mp4 ✓
```

### Basic Command

```bash
python viet_dubbing.py --srt subtitle.srt --video no_voice.mp4
```

### All Parameters

| Parameter | Required | Default | Description |
|---|---|---|---|
| `--srt` | ✅ Yes | — | Path to Vietnamese `.srt` subtitle file |
| `--video` | ❌ No | (none) | Path to video file (with voice already removed) |
| `--voice` | ❌ No | `female` | Voice to use: `female` or `male` |
| `--bgm-volume` | ❌ No | `100` | Original audio volume to keep (0–100%) |
| `--out` | ❌ No | auto | Custom output filename (auto-generated if not set) |
| `--audio-only` | ❌ No | false | Export `.mp3` only, skip video muxing |
| `--workers` | ❌ No | `5` | Concurrent TTS requests (max recommended: 10) |

### Available Voices

| Parameter | Voice Name | Gender | Accent |
|---|---|---|---|
| `--voice female` | HoaiMyNeural | Female | Southern Vietnamese *(default)* |
| `--voice male` | NamMinhNeural | Male | Southern Vietnamese |

---

### Examples

**Standard usage:**

```bash
python viet_dubbing.py --srt subtitle.srt --video episode01_no_voice.mp4
```

**Use male voice:**

```bash
python viet_dubbing.py --srt subtitle.srt --video episode01_no_voice.mp4 --voice male
```

**Reduce BGM volume to 70%:**

```bash
python viet_dubbing.py --srt subtitle.srt --video episode01_no_voice.mp4 --bgm-volume 70
```

**Mute original audio completely:**

```bash
python viet_dubbing.py --srt subtitle.srt --video episode01_no_voice.mp4 --bgm-volume 0
```

**Export audio only (for manual editing in CapCut):**

```bash
python viet_dubbing.py --srt subtitle.srt --audio-only
```

**Increase TTS concurrency (faster but higher rate limit risk):**

```bash
python viet_dubbing.py --srt subtitle.srt --video episode01_no_voice.mp4 --workers 8
```

---

### Output Files

Output files are automatically named after the source video + timestamp:

| Type | Example filename |
|---|---|
| Video | `episode01_dubbed_20250115143022.mp4` |
| Audio | `episode01_audio_20250115143022.mp3` |
| TTS cache | `tts_episode01/` (folder) |

> Each run produces a unique filename — no risk of overwriting previous outputs.

---

## 🔁 Retry Failed TTS

When some TTS cues fail (usually due to rate limiting), use the companion script to retry only the failed ones:

### How it works

```
1. viet_dubbing.py chạy → một số cue bị lỗi TTS
        ↓
2. retry_failed_tts.py → retry chỉ những cue lỗi vào đúng thư mục cache
        ↓
3. Chạy lại viet_dubbing.py → skip cue đã có, chỉ mix lại audio
```

### Commands

```bash
# Retry cue lỗi (tự tìm log mới nhất + suy ra thư mục từ tên video)
python retry_failed_tts.py --srt subtitle.srt --video episode01_no_voice.mp4

# Chỉ định thư mục TTS cache cụ thể
python retry_failed_tts.py --srt subtitle.srt --tmp-dir tts_episode01

# Tùy chỉnh workers và retries
python retry_failed_tts.py --srt subtitle.srt --video episode01_no_voice.mp4 --workers 2 --retries 8

# Sau khi retry xong, chạy lại viet_dubbing
python viet_dubbing.py --srt subtitle.srt --video episode01_no_voice.mp4
```

### Retry Parameters

| Parameter | Required | Default | Description |
|---|---|---|---|
| `--srt` | ✅ Yes | — | File SRT gốc |
| `--video` | ❌ No | (none) | File video gốc (suy ra tên thư mục TTS) |
| `--tmp-dir` | ❌ No | auto | Thư mục TTS cache (default: `tts_<tên video/srt>`) |
| `--log` | ❌ No | latest | File log cụ thể (default: log mới nhất trong `logs/`) |
| `--voice` | ❌ No | `female` | Giọng đọc: `female` hoặc `male` |
| `--workers` | ❌ No | `3` | Concurrent requests (thấp hơn main script để tránh rate limit) |
| `--retries` | ❌ No | `5` | Số lần retry mỗi cue (1–20) |

### How it finds failed cues

1. **Đọc log** (ưu tiên): Tìm session cuối trong `logs/log_YYYYMMDD.txt`, lấy danh sách cue `FAILED`
2. **Scan thư mục** (fallback): Quét `tts_<video>/` tìm file thiếu hoặc file rỗng

---

## 🏗️ Architecture (v3)

```
viet_dubbing.py pipeline:
┌─────────────────────────────────────────────────────────┐
│  1. Parse SRT                                           │
│  2. Generate TTS (async + semaphore concurrency)        │
│     └─ Cache: tts_<video>/cue_0001.mp3 ...              │
│  3. Build TTS track                                     │
│     ├─ Phase 1: ffprobe duration detect (cheap)         │
│     ├─ Phase 2: ThreadPool stretch (chained atempo)     │
│     └─ Phase 3: Single decode → numpy array mix         │
│  4. Mix BGM (numpy vectorized addition)                 │
│  5. Export MP3 (for user)                               │
│  6. Mux video: WAV → AAC (single encode)                │
└─────────────────────────────────────────────────────────┘
```

### Performance notes

- **TTS generation** is network-bound — increase `--workers` for faster generation (watch for rate limits)
- **Timeline sync** does one ffprobe per clip (fast), stretches in parallel threads, decodes each MP3 exactly once
- **BGM mixing** uses numpy vectorized `+` operator — handles 30-minute tracks in under 1 second
- **Video muxing** uses WAV intermediate so audio is encoded to AAC exactly once (no MP3→AAC double encoding)

---

## 📂 Project Structure

```
viet-dubbing/
├── viet_dubbing.py           # Main dubbing script (v3)
├── retry_failed_tts.py       # Retry companion script (v4)
├── README.md
├── LICENSE
├── logs/                     # Auto-generated log files
│   └── log_20250419.txt
└── tts_<video>/              # TTS cache (per video)
    ├── cue_0001.mp3
    ├── cue_0002.mp3
    └── ...
```

---

## 📋 Quick Setup Checklist

**First time only:**

- [ ] Install Python (tick Add to PATH)
- [ ] Install FFmpeg (add `/bin` to PATH)
- [ ] `pip install edge-tts pydub rich numpy -i https://pypi.org/simple`

**Every video:**

- [ ] Translate `.srt` to Vietnamese
- [ ] Remove voice from video using CapCut
- [ ] Copy `.srt` + `.mp4` + `viet_dubbing.py` into the same folder
- [ ] Run `python viet_dubbing.py --srt ... --video ...`
- [ ] If some TTS failed → run `python retry_failed_tts.py --srt ... --video ...` → re-run main script
- [ ] Collect output file from the same folder

---

## 💡 Tips

- **Interrupted mid-run?** Just run the same command again — the script skips lines already generated and continues from where it stopped.
- **TTS too loud vs BGM?** Use `--bgm-volume 40` to bring the background music up.
- **Quick test before full run?** Trim your `.srt` to the first 10 lines and test first.
- **Avoid spaces in filenames** — use `episode_01.mp4` instead of `episode 01.mp4` to prevent path errors.
- **Internet required** — Edge TTS uses Microsoft's servers to generate voice audio.
- **Rate limited?** Reduce `--workers` to 3 and wait a few minutes before retrying.
- **TTS cache reusable** — The `tts_<video>/` folder persists between runs. Delete it manually when no longer needed.

---

## 📄 License

MIT License — free to use, modify, and distribute.
