# 🎙️ Viet Dubbing v2 — Performance Edition

**Auto Vietnamese dubbing from SRT subtitle using Microsoft Edge TTS**

Automatically generate Vietnamese voice-over from `.srt` subtitle files and sync it with your video. Built for content creators who work with Chinese animated videos and want to add Vietnamese dubbing quickly and for free.

---

## ✨ Features

- 🆓 **100% Free** — uses Microsoft Edge TTS (no API key required)
- 🎙️ **Natural Vietnamese voices** — female & male options
- ⏱️ **Auto time-sync** — automatically stretches/compresses each audio clip to fit the subtitle timestamp
- 🎵 **Preserves original audio** — BGM and sound effects are kept, only voice is replaced
- 📊 **Beautiful CLI progress** — real-time progress bar with rich
- 🔁 **Auto retry** — retries failed lines automatically
- ▶️ **Resume support** — if interrupted, continues from where it left off
- 🏷️ **Auto output naming** — output file named after source video + timestamp
- 🔔 **Done notification** — plays a sound when processing completes

### ⚡ v2 Performance Improvements

- **Concurrent TTS generation** — multiple API calls run in parallel via `asyncio.Semaphore` (configurable with `--workers`, default: 5). Reduces TTS time by **3–5x**
- **Numpy-based audio mixing** — replaces 200+ sequential `pydub.overlay()` calls with direct numpy array writes. **10–20x faster** sync phase
- **Parallel audio stretching** — FFmpeg stretch jobs run across multiple CPU cores via `ProcessPoolExecutor` instead of one-by-one
- **Built-in timing** — shows elapsed time for each phase (TTS, sync, total) so you can track performance

---

## 🔧 Requirements

| Component  | Version | Notes                                    |
|------------|---------|------------------------------------------|
| Python     | 3.8+    | Must add to PATH during install          |
| FFmpeg     | Any     | Must add `/bin` folder to PATH manually  |
| edge-tts   | Latest  | Microsoft TTS library                    |
| pydub      | 0.25+   | Audio processing library                 |
| numpy      | Latest  | Fast array operations for audio mixing   |
| rich       | Latest  | CLI progress UI                          |

---

## 📦 Installation

### 1. Install Python

Download from **python.org/downloads**

> ⚠️ **Important:** Check **"Add Python to PATH"** before clicking Install Now.

Verify installation:

```
python --version
```

---

### 2. Install FFmpeg

Download from **github.com/BtbN/FFmpeg-Builds/releases**

Get: `ffmpeg-master-latest-win64-gpl.zip` → extract to any folder (e.g. `D:\ffmpeg`)

Add the `bin` folder to your system PATH:

- Search **"Environment Variables"** in Windows
- Edit **Path** under User variables → **New** → paste path to `\bin` folder (e.g. `D:\ffmpeg-master-latest-win64-gpl\bin`)
- Click **OK → OK → OK**

Open a **new** CMD window and verify:

```
ffmpeg -version
```

---

### 3. Install Python packages

```
pip install edge-tts pydub numpy rich -i https://pypi.org/simple
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

---

### Basic Command

```
python viet_dubbing.py --srt subtitle.srt --video no_voice.mp4
```

---

### All Parameters

| Parameter      | Required | Default  | Description                                          |
|----------------|----------|----------|------------------------------------------------------|
| `--srt`        | ✅ Yes   | —        | Path to Vietnamese `.srt` subtitle file              |
| `--video`      | ❌ No    | (none)   | Path to video file (with voice already removed)      |
| `--voice`      | ❌ No    | `female` | Voice to use: `female` or `male`                     |
| `--bgm-volume` | ❌ No    | `100`    | Original audio volume to keep (0–100%)               |
| `--out`        | ❌ No    | auto     | Custom output filename (auto-generated if not set)   |
| `--audio-only` | ❌ No    | false    | Export `.mp3` only, skip video muxing                |
| `--workers`    | ❌ No    | `5`      | Number of concurrent TTS requests (max recommended: 10) |

---

### Available Voices

| Parameter        | Voice Name    | Gender | Accent                          |
|------------------|---------------|--------|---------------------------------|
| `--voice female` | HoaiMyNeural  | Female | Southern Vietnamese *(default)* |
| `--voice male`   | NamMinhNeural | Male   | Southern Vietnamese             |

---

### Examples

**Standard usage:**

```
python viet_dubbing.py --srt subtitle.srt --video episode01_no_voice.mp4
```

**Faster TTS with 8 concurrent workers:**

```
python viet_dubbing.py --srt subtitle.srt --video episode01_no_voice.mp4 --workers 8
```

**Use male voice:**

```
python viet_dubbing.py --srt subtitle.srt --video episode01_no_voice.mp4 --voice male
```

**Reduce BGM volume to 70%:**

```
python viet_dubbing.py --srt subtitle.srt --video episode01_no_voice.mp4 --bgm-volume 70
```

**Mute original audio completely:**

```
python viet_dubbing.py --srt subtitle.srt --video episode01_no_voice.mp4 --bgm-volume 0
```

**Export audio only (for manual editing in CapCut):**

```
python viet_dubbing.py --srt subtitle.srt --video episode01_no_voice.mp4 --audio-only
```

---

### Output Files

Output files are automatically named after the source video + timestamp:

| Type  | Example filename                      |
|-------|---------------------------------------|
| Video | `episode01_dubbed_20250115143022.mp4` |
| Audio | `episode01_audio_20250115143022.mp3`  |

> Each run produces a unique filename — no risk of overwriting previous outputs.

---

## 📋 Quick Setup Checklist

**First time only:**

- Install Python (tick Add to PATH)
- Install FFmpeg (add `/bin` to PATH)
- `pip install edge-tts pydub numpy rich -i https://pypi.org/simple`

**Every video:**

- Translate `.srt` to Vietnamese
- Remove voice from video using CapCut
- Copy `.srt` + `.mp4` + `viet_dubbing.py` into the same folder
- Run `python viet_dubbing.py --srt ... --video ...`
- Collect output file from the same folder

---

## 💡 Tips

- **Interrupted mid-run?** Just run the same command again — the script skips lines already generated and continues from where it stopped.
- **TTS too slow?** Increase `--workers 8` or `--workers 10` for faster generation. Don't go above 10 to avoid rate limiting from Microsoft servers.
- **TTS too loud vs BGM?** Use `--bgm-volume 40` to bring the background music up.
- **Quick test before full run?** Trim your `.srt` to the first 10 lines and test first.
- **Avoid spaces in filenames** — use `episode_01.mp4` instead of `episode 01.mp4` to prevent path errors.
- **Internet required** — Edge TTS uses Microsoft's servers to generate voice audio.

---

## 📄 License

MIT License — free to use, modify, and distribute.