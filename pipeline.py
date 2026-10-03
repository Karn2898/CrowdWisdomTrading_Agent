#!/usr/bin/env python3
"""Create the Ads -> Scripts -> Video Kanban pipeline."""

import json
import subprocess
import sys
import time
from pathlib import Path


def run_cmd(cmd: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    """Run a Hermes command."""
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if result.returncode != 0 and capture:
        print(f"  ERROR (exit {result.returncode}): {result.stderr.strip()}")
    return result


def kanban_init() -> bool:
    """Initialize kanban database."""
    result = run_cmd(["hermes", "kanban", "init"])
    return result.returncode == 0


def task_exists(idempotency_key: str) -> str | None:
    """Return the task ID for an existing idempotency key."""
    result = run_cmd(["hermes", "kanban", "list", "--json", "--archived"])
    if result.returncode != 0:
        return None
    try:
        tasks = json.loads(result.stdout)
        for task in tasks:
            if task.get("idempotency_key") == idempotency_key:
                return task["id"]
    except json.JSONDecodeError:
        pass
    return None


def create_task(
    title: str,
    body: str,
    assignee: str,
    idempotency_key: str,
    parent_ids: list[str] | None = None,
) -> str | None:
    """Create a kanban task idempotently."""
    existing = task_exists(idempotency_key)
    if existing:
        print(f"  Task already exists: {existing} ({title})")
        return existing

    cmd = [
        "hermes", "kanban", "create",
        title,
        "--body", body,
        "--assignee", assignee,
        "--idempotency-key", idempotency_key,
        "--json"
    ]

    if parent_ids:
        for pid in parent_ids:
            cmd.extend(["--parent", pid])

    result = run_cmd(cmd)
    if result.returncode != 0:
        print(f"  FAILED to create task: {title}")
        return None

    try:
        data = json.loads(result.stdout)
        task_id = data.get("id")
        print(f"  Created task: {task_id} ({title})")
        return task_id
    except json.JSONDecodeError:
        print(f"  FAILED to parse output for: {title}")
        return None


def link_tasks(parent_id: str, child_id: str) -> bool:
    """Add dependency link if not already linked."""
    result = run_cmd(["hermes", "kanban", "link", parent_id, child_id])
    if result.returncode == 0:
        print(f"  Linked: {parent_id} -> {child_id}")
        return True
    if "already" in result.stderr.lower() or "duplicate" in result.stderr.lower():
        print(f"  Link already exists: {parent_id} -> {child_id}")
        return True
    print(f"  Link failed: {result.stderr.strip()}")
    return False


def print_board_state() -> None:
    """Print current board state."""
    print("\n" + "=" * 80)
    print("KANBAN BOARD STATE")
    print("=" * 80)
    run_cmd(["hermes", "kanban", "list", "--json"], capture=False)
    print("=" * 80)


def main() -> int:
    """Create the full pipeline."""
    if not kanban_init():
        print("Failed to initialize kanban")
        return 1

    base_body = {
        "project_root": str(Path.cwd()),
        "scripts_dir": "out/scripts",
        "videos_dir": "out/videos",
        "cache_dir": "out/.cache",
    }

    t1_body = json.dumps({**base_body, "stage": "ads"}, indent=2)
    t1_id = create_task(
        title="T1: Ads Manager - Scrape Meta Ads & Extract Insights",
        body=t1_body,
        assignee="ads_manager",
        idempotency_key="cw-t1-ads-manager",
    )

    if not t1_id:
        print("Failed to create T1")
        return 1

    t2a_body = json.dumps({**base_body, "stage": "script", "script_type": "pain"}, indent=2)
    t2a_id = create_task(
        title="T2a: Script Agent - Pain Script",
        body=t2a_body,
        assignee="script_agent",
        idempotency_key="cw-t2a-script-pain",
        parent_ids=[t1_id],
    )

    t2b_body = json.dumps({**base_body, "stage": "script", "script_type": "data"}, indent=2)
    t2b_id = create_task(
        title="T2b: Script Agent - Data Script",
        body=t2b_body,
        assignee="script_agent",
        idempotency_key="cw-t2b-script-data",
        parent_ids=[t1_id],
    )

    t2c_body = json.dumps({**base_body, "stage": "script", "script_type": "solution"}, indent=2)
    t2c_id = create_task(
        title="T2c: Script Agent - Solution Script",
        body=t2c_body,
        assignee="script_agent",
        idempotency_key="cw-t2c-script-solution",
        parent_ids=[t1_id],
    )

    t3_body = json.dumps({**base_body, "stage": "video"}, indent=2)
    t3_id = create_task(
        title="T3: Video Agent - Render All Videos",
        body=t3_body,
        assignee="video_agent",
        idempotency_key="cw-t3-video-agent",
        parent_ids=[t2a_id, t2b_id, t2c_id] if all([t2a_id, t2b_id, t2c_id]) else None,
    )

    if t1_id and t2a_id:
        link_tasks(t1_id, t2a_id)
    if t1_id and t2b_id:
        link_tasks(t1_id, t2b_id)
    if t1_id and t2c_id:
        link_tasks(t1_id, t2c_id)
    if t3_id and t2a_id:
        link_tasks(t2a_id, t3_id)
    if t3_id and t2b_id:
        link_tasks(t2b_id, t3_id)
    if t3_id and t2c_id:
        link_tasks(t2c_id, t3_id)

    # Print final board state
    print_board_state()

    print("\nPipeline created successfully!")
    print(f"  T1 (ads_manager):     {t1_id}")
    print(f"  T2a (script/pain):    {t2a_id}")
    print(f"  T2b (script/data):    {t2b_id}")
    print(f"  T2c (script/solution): {t2c_id}")
    print(f"  T3 (video_agent):     {t3_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())