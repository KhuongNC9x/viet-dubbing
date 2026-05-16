# 🎙️ Viet Dubbing v3.1

**Auto Vietnamese dubbing from SRT subtitle using Microsoft Edge TTS**

Automatically generate Vietnamese voice-over from `.srt` subtitle files and sync it with your video. Built for content creators who work with Chinese animated videos and want to add Vietnamese dubbing quickly and for free.

---

## ✨ Features

- 🆓 **100% Free** — uses Microsoft Edge TTS (no API key required)
- 🎙️ **Natural Vietnamese voices** — female & male options (Southern accent)
- ⏱️ **Smart speed normalization** — automatically stretches/compresses each audio clip to fit the subtitle timestamp, with both speed-up and slow-down support
- 🎵 **Preserves original audio** — BGM and sound effects are kept, only voice is replaced
- ⚡ **Parallel pipeline** — concurrent ffprobe, decode, stretch, and TTS generation
- 🔧 **Tunable speed limits** — control how fast/slow the TTS voice can be adjusted
- 📊 **Beautiful CLI progress** — real-time progress bar with rich
- 🔁 **Auto retry** — retries failed lines automatically with exponential backoff
- ▶️ **Resume support** — if interrupted, continues from where it left off (TTS cache)
- 🏷️ **Auto output naming** — output file named after source video + timestamp
- 📝 **Daily log files** — detailed logs in `logs/` folder for debugging

---

## 🔧 Requirements

| Component | Version | Notes |
|-----------|---------|-------|
| Python    | 3.10+   | Must add to PATH during install |
| FFmpeg    | Any     | Must add `/bin` folder to PATH manually |
| edge-tts  | Latest  | Microsoft TTS library |
| pydub     | 0.25+   | Audio processing library |
| numpy     | Latest  | Fast audio mixing |
| rich      | Latest  | CLI progress UI |

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
5. Get output_dubbed_[timestamp].mp4 ✓
```

### Basic Command

```bash
# Audio only (minimum required)
python viet_dubbing.py --srt subtitle.srt

# With video muxing
python viet_dubbing.py --srt subtitle.srt --video no_voice.mp4
```

---

### All Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `--srt` | ✅ Yes | — | Path to Vietnamese `.srt` subtitle file |
| `--video` | ❌ No | (none) | Path to video file (with voice already removed) |
| `--voice` | ❌ No | `female` | Voice to use: `female` or `male` |
| `--bgm-volume` | ❌ No | `100` | Original audio volume to keep (0–100%) |
| `--out` | ❌ No | auto | Custom output filename (auto-generated if not set) |
| `--audio-only` | ❌ No | false | Export `.mp3` only, skip video muxing |
| `--workers` | ❌ No | `5` | Concurrent TTS requests (max recommended: 10) |
| `--speed-up-limit` | ❌ No | `2.0` | Max speed-up ratio. Higher = allow faster speech |
| `--slow-down-limit` | ❌ No | `0.7` | Max slow-down ratio. Lower = allow slower speech |
| `--no-slow-down` | ❌ No | false | Disable slow-down (v2 behavior) |

### Available Voices

| Parameter | Voice Name | Gender | Accent |
|-----------|-----------|--------|--------|
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

**Allow faster compression for dense subtitles:**

```bash
python viet_dubbing.py --srt subtitle.srt --video video.mp4 --speed-up-limit 2.5
```

**Allow more stretching for short TTS clips:**

```bash
python viet_dubbing.py --srt subtitle.srt --video video.mp4 --slow-down-limit 0.6
```

**Increase TTS concurrency for faster generation:**

```bash
python viet_dubbing.py --srt subtitle.srt --video video.mp4 --workers 8
```

---

### Output Files

Output files are automatically named after the source video + timestamp:

| Type | Example filename |
|------|-----------------|
| Video | `episode01_dubbed_20250115143022.mp4` |
| Audio | `episode01_audio_20250115143022.mp3` |

> Each run produces a unique filename — no risk of overwriting previous outputs.

---

## ⚡ Performance (v3.1 vs v2)

| Phase | v2 | v3.1 | Improvement |
|-------|------|------|-------------|
| Detect duration | ffprobe tuần tự | ffprobe song song (ThreadPool) | **~70-80% faster** |
| Stretch | ProcessPoolExecutor | ThreadPoolExecutor (8 workers) | **Faster startup, less overhead** |
| Decode MP3 | Tuần tự trong Phase 1 + Phase 2 | Song song (ThreadPool) + single-decode | **~50-60% faster** |
| BGM mixing | pydub `.overlay()` | Numpy array mixing | **10-50× faster** |
| BGM extraction | video → MP3 → decode | video → WAV (PCM) trực tiếp | **No encode+decode overhead** |
| Video mux | Export WAV → FFmpeg đọc file | Pipe raw PCM → FFmpeg stdin | **No temp file I/O** |

---

## 🔄 Speed Normalization (NEW in v3.1)

v2 chỉ nén (speed-up) câu TTS dài hơn slot, nhưng **không kéo dãn** câu TTS ngắn hơn slot. Điều này gây ra:

- **Câu nói nhanh:** TTS dài 6s cho slot 3s → bị nén gấp đôi, nghe vội
- **Khoảng im lặng:** TTS ngắn 1s cho slot 3s → nói xong rồi im 2s
- **Câu bị bỏ qua:** ratio < 0.6 thì không xử lý → audio tràn sang câu kế

v3.1 khắc phục bằng:

- **Slow-down:** Kéo dãn câu TTS ngắn cho lấp đầy slot (giới hạn mặc định ≥0.7×)
- **Bỏ COMPRESS_MIN:** Mọi câu đều được xử lý, không còn bị bỏ qua
- **Tolerance 3%:** Chỉ bỏ qua stretch khi ratio ±3% (thay vì ±5%)
- **Tùy chỉnh:** `--speed-up-limit` và `--slow-down-limit` cho phép điều chỉnh theo nhu cầu

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
- [ ] Collect output file from the same folder

---

## 💡 Tips

- **Interrupted mid-run?** Just run the same command again — the script skips lines already generated and continues from where it stopped.
- **TTS too loud vs BGM?** Use `--bgm-volume 40` to bring the background music up.
- **Quick test before full run?** Trim your `.srt` to the first 10 lines and test first.
- **Speed sounds unnatural?** Try `--speed-up-limit 1.8` for less compression, or `--slow-down-limit 0.8` for less stretching.
- **Upgrading from v2?** Delete the `tts_tmp/` or `tts_*` cache folder to start fresh. v3.1 uses a different caching structure.
- **Avoid spaces in filenames** — use `episode_01.mp4` instead of `episode 01.mp4` to prevent path errors.
- **Internet required** — Edge TTS uses Microsoft's servers to generate voice audio.

---

## 📄 License

MIT License — free to use, modify, and distribute.
