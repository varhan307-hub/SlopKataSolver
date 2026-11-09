# GUVI CodeKata Auto-Solver

An automated script to solve GUVI CodeKata problems using Groq's high-speed LLM API. Built with Playwright and Python.

## Features
- **Headless by default:** Runs silently in the background.
- **Fast Scraper:** Bypasses pagination limits by auto-selecting 50 items per page.
- **Multi-Key Rotation:** Automatically rotates Groq API keys on rate limits (429) or server errors (500/503).
- **Smart Caching:** Caches exhausted API keys in logs/key_status.json to skip them on subsequent runs.
- **Local Stats:** Tracks your solved problems via local logs, independent of GUVI's site.

## Prerequisites
- Python 3.10+
- uv installed
- Groq API Key(s)

## Setup

1. **Install dependencies:**
   uv sync

2. **Initialize Environment:**
   Run the setup wizard to create your .env file:
   uv run main.py --init
   This will prompt you for your Groq API key(s) (comma-separated for multiple) and model preference.

3. **Login:**
   The script uses a persistent browser profile. You only need to log in once:
   uv run main.py --login
   A browser window will open. Log into GUVI manually. The script waits up to 2 minutes and saves your session to the guvi_profile/ directory.

## Usage

**Check if you are logged in:**
uv run main.py --check-login

**View how many problems you have solved:**
uv run main.py --stats
This shows both your local solver stats and your total solved count fetched directly from GUVI.

**View API Key Status:**
uv run main.py --key-status
Shows which keys are ready and which are in cooldown.

**Run the solver (Headless):**
uv run main.py --solve --status "yet to start" --difficulty easy --count 50 --max-tries 1

**Run with a visible browser:**
uv run main.py --solve --status "yet to start" --count 5 --visible

**Solve a single URL:**
uv run main.py --url "https://www.guvi.in/code-kata/some-problem/"

## CLI Reference

| Flag | Description |
|------|-------------|
| --init | Create or overwrite the .env file. |
| --login | Open browser to login manually. |
| --check-login | Check login status (headless). |
| --stats | Show local solved count + GUVI total. |
| --key-status | Show API key cooldowns. |
| --solve | Run the solver (requires filters). |
| --url <URL> | Solve a single specific problem. |
| --file <path> | Path to a text file with URLs. |
| --status <str> | Filter: "yet to start", "in progress", "completed", "all". |
| --difficulty <str> | Filter: "easy", "medium", "hard", "all". |
| --count <int> | Max problems to process in this run (default: 3). |
| --max-tries <int> | Max LLM attempts per problem (default: 4). |
| --visible | Show the browser (default is headless). |
| --no-submit | Run sample checks without submitting. |
| --profile <str> | Browser profile dir (default: guvi_profile). |

## Logs
- logs/completed.jsonl: Successfully solved problems (used to skip on next run).
- logs/errors.jsonl: Failed problems for manual review.
- logs/key_status.json: API key cooldown cache.
- logs/solver.log: General debug log.

## Disclaimer
Use this tool responsibly. The author is not responsible for any accounts banned due to automated activity.