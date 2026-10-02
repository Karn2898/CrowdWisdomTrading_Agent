# CrowdWisdomTrading_Agent

Automated pipeline for generating CrowdWisdomTrading ad scripts and videos.

## Fresh clone setup

### Prerequisites

- Python 3.10+
- `hermes` CLI available on `PATH`
- `ffmpeg` and `ffprobe` available on `PATH` (or set `FFMPEG_PATH` / `FFPROBE_PATH`)

### Environment variables

Copy [.env.example](./.env.example) to `.env` and set:

- `APIFY_TOKEN`
- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL`
- `TAVILY_API_KEY`
- `EXA_API_KEY`

### One-command run

- Windows PowerShell:
  - `.\run.ps1`
- Bash (Linux/macOS/Git Bash):
  - `./run.sh`

Both runners will:
1. Create `.venv` if missing
2. Install `requirements.txt`
3. Validate required `.env` keys with a clear error message
4. Execute `pipeline.py`

## Clean-room validation

To verify from a temp clone:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/clean_room_test.ps1
```

This script clones into a temp folder, copies your `.env`, runs one command (`run.ps1` by default), and then removes the temp folder.