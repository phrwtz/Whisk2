#!/usr/bin/env python3
"""Tiny frontend harness for bot-mode UI regression checks.

Usage:
  python scripts/frontend_bot_harness.py --bot-seed 123 --port 8765

Requires:
  pip install playwright
  playwright install chromium
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def wait_http_ready(url: str, timeout_s: float = 15.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if 200 <= resp.status < 500:
                    return
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"Server did not become ready at {url}")


def run_harness(base_url: str, bot_seed: int) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError(
            "Playwright is required. Install with: pip install playwright && playwright install chromium"
        ) from exc

    url = f"{base_url}/?bot_seed={bot_seed}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")

        page.fill("#nameInput", "HarnessUser")
        page.click("#botBtn")
        assert page.locator("#joinBtn").is_enabled(), "Join button should be enabled after name+mode"

        page.click("#joinBtn")

        # Transition 1: joined message
        page.wait_for_function(
            "document.querySelector('#messages') && document.querySelector('#messages').innerText.includes('Joined. Waiting for game state')",
            timeout=10_000,
        )

        # Transition 2: bot mode prompt
        page.wait_for_function(
            "document.querySelector('#messages') && document.querySelector('#messages').innerText.includes('your move against WhiskBot')",
            timeout=10_000,
        )

        # Make one move; server should commit human + bot move.
        page.click(".cell[data-row='0'][data-col='0']")

        # Transition 3: post-commit text (transient variants allowed)
        page.wait_for_function(
            """
            (() => {
              const txt = document.querySelector('#messages')?.innerText || '';
              return txt.includes('your move against WhiskBot')
                || txt.includes('Waiting for you to make your next move');
            })()
            """,
            timeout=10_000,
        )

        # Analysis panel should include a bot explanation entry.
        page.wait_for_function(
            "document.querySelector('#analysisPanel') && document.querySelector('#analysisPanel').innerText.includes('WhiskBot played')",
            timeout=10_000,
        )

        # Board should now contain at least one O and one X after first committed turn.
        page.wait_for_function(
            """
            (() => {
              const cells = [...document.querySelectorAll('.cell')];
              let hasO = false;
              let hasX = false;
              for (const c of cells) {
                if (c.textContent === 'O') hasO = true;
                if (c.textContent === 'X') hasX = true;
              }
              return hasO && hasX;
            })()
            """,
            timeout=10_000,
        )

        browser.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Frontend bot-mode UI harness")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--bot-seed", type=int, default=123)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    base_url = f"http://{args.host}:{args.port}"

    server_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.app.main:app",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    server = subprocess.Popen(server_cmd, cwd=str(repo_root))
    try:
        wait_http_ready(base_url)
        run_harness(base_url=base_url, bot_seed=args.bot_seed)
        print("Frontend bot harness passed.")
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    main()
