<img width="823" height="757" alt="{A20A2649-1E2E-432D-B8AB-443A55C0C202}" src="https://github.com/user-attachments/assets/b159e4a9-a3af-4f7b-a80c-1db9ba2cf528" />

# AniChapters

Automatic anime OP/ED detection and MKV chapter generator.

AniChapters analyzes the audio of your local anime episodes, detects the Opening and Ending segments, and generates MKV-compatible chapter files automatically.

---

## Features

- Automatic OP/ED detection using audio correlation
- Fetches theme songs directly from [animethemes.moe](https://animethemes.moe)
- Generates MKV-compatible XML chapter files
- Chapter review and manual editing before export
- Merge chapters into MKV files via mkvmerge
- Optional in-place editing using mkvpropedit
- Theme cache per series for faster re-analysis
- Batch processing for multiple episodes at once

---

## Download

Download the latest release from the [Releases](../../releases) page.  
Extract the ZIP and run **AniChapters.exe** — no installation required.

---

## How to Use

1. Run **AniChapters.exe**
2. Click **Add…** and select your anime episodes
3. Click **Search & Fetch…** and select your anime from the results
4. Click **Analyze All** and wait for detection to finish
5. Click **Review** to check and edit the detected chapters
6. Click **Merge MKV** to embed chapters into your video files

---

## Requirements

The following external tools must be available in your system PATH or placed in the same folder as the exe:

| Tool | Required | Purpose |
|------|----------|---------|
| ffmpeg | ✅ Yes | Audio extraction |
| ffprobe | ✅ Yes | Video metadata |
| mkvmerge | ✅ Yes | Chapter merging |
| mkvpropedit | ⬜ Optional | In-place chapter editing |

**FFmpeg** → [ffmpeg.org](https://ffmpeg.org/download.html)  
**MKVToolNix** (includes mkvmerge + mkvpropedit) → [mkvtoolnix.download](https://mkvtoolnix.download)

---

## Running from Source

```bash
git clone https://github.com/56cla/AniChapters
cd AniChapters
pip install -r Requirements.txt
python main.py
```

---

## Notes

- Themes are cached in a `.themes` folder next to your video files. Use **Clear Cache** in the app to delete them.
- Some episodes may require manual review if the OP/ED differs between episodes.
- AniChapters does **not** download anime — it only analyzes video files you already have.

---

## License

MIT License
