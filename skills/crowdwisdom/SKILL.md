---
name: crowdwisdom
category: software-development
description: Tools for the CrowdWisdomTrading video production pipeline
version: 0.1.0
author: CrowdWisdomTrading
---

# CrowdWisdomTrading Skill

Tools for the CrowdWisdomTrading video production pipeline.

## Tools

- `run_ads_manager` — Run ads pipeline (apify_meta_ads + insight_extractor)
- `tavily_search` — Research pain points via Tavily/Exa
- `cw_data` — Extract ad-worthy stats from CrowdWisdomTrading data
- `script_generator` — Generate video scripts (pain/data/solution)
- `validate_script` — Validate script against schema + constraints
- `run_video_agent` — Render script to MP4 with TTS + music

## Usage

```bash
# Ads Manager
python tools/run_ads_manager.py [--refresh]

# Research
python tools/tavily_search.py [--refresh]

# Stats
python tools/cw_data.py [--refresh]

# Scripts
python tools/script_generator.py --type pain|data|solution|all [--refresh]

# Validate
python tools/validate_script.py out/scripts/script_pain.json

# Video
python tools/run_video_agent.py --type pain|data|solution|all [--dry-run]
```