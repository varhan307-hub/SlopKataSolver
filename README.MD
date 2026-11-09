# SlopKhathaSolver

An automated script to solve GUVI CodeKata problems using Groq's high-speed LLM API. Built with Playwright and Python.

## Features
- **Headless by default:** Runs silently in the background.
- **Multi-Key Rotation:** Automatically rotates Groq API keys on rate limits (429) or server errors (500/503).
- **Smart Caching:** Caches exhausted API keys in logs/key_status.json to skip them on subsequent runs.
- **Local Stats:** Tracks your solved problems via local logs, independent of GUVI's site.
- **Persistent Login:** Log in once, session saved to guvi_profile/.
- **Limitation:** The scraper currently pulls a max of 15 problems per run. Use the batch runner script to solve more.

## Prerequisites

You need uv installed. If you don't have it, install it (do not use raw pip, uv handles everything):

    # macOS / Linux
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # Windows (PowerShell)
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

You also need:
- Python 3.10+
- Groq API Key(s)

## Setup

1. Install dependencies:
    
    uv sync

2. Initialize Environment:
    
    uv run main.py --init
    
    Prompts for Groq API key(s) (comma-separated for multiple) and model.

3. Login (once):
    
    uv run main.py --login
    
    Opens a browser. Log into GUVI manually. Waits up to 3 minutes. Saves session to guvi_profile/.

## Usage

Check login:
    
    uv run main.py --check-login

View stats:
    
    uv run main.py --stats
    
    Shows solved count from local logs and total from GUVI.

View API key status:
    
    uv run main.py --key-status

Solve problems:
    
    uv run main.py --solve --status "yet to start" --difficulty easy --count 15 --max-tries 1

Solve with visible browser:
    
    uv run main.py --solve --status "yet to start" --count 5 --visible

Solve a single URL:
    
    uv run main.py --url "https://www.guvi.in/code-kata/some-problem/"

### Solving More Than 15 Problems
The main script only attempts 15 problems per run (GUVI's default page limit). To solve more, use the batch runner script. It will run the solver repeatedly until it hits your target.

    uv run python temp_batch_runner.py

(Note: This runner only attempts the problems; some may fail if the LLM's code doesn't pass GUVI's hidden tests.)

## CLI Reference

| Flag | Description |
|------|-------------|
| --init | Create or overwrite .env file |
| --login | Open browser to login manually |
| --check-login | Check login status (headless) |
| --stats | Show local solved count + GUVI total |
| --key-status | Show API key cooldowns |
| --solve | Run the solver (use with filters) |
| --url <URL> | Solve a single specific problem |
| --file <path> | Path to text file with URLs |
| --status <str> | yet to start / in progress / completed / all |
| --difficulty <str> | easy / medium / hard / all |
| --count <int> | Max problems this run (default: 3) |
| --max-tries <int> | Max LLM attempts per problem (default: 4) |
| --visible | Show browser (default headless) |
| --no-submit | Run sample checks without submitting |
| --profile <str> | Browser profile dir (default: guvi_profile) |

## Logs
- logs/completed.jsonl: Successfully solved problems
- logs/errors.jsonl: Failed problems for review
- logs/key_status.json: API key cooldown cache
- logs/solver.log: General debug log

## Disclaimer
Use this tool responsibly. The author is not responsible for any accounts banned due to automated activity.
