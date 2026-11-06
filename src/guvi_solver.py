"""
GUVI CodeKata auto-solver (Playwright + Gemini).

Setup (Windows):
    pip install playwright requests
    playwright install chromium
    PowerShell:  $env:GEMINI_API_KEY="your-new-key"
                 $env:GEMINI_MODEL="exact-model-name-from-AI-Studio"
    cmd:         set GEMINI_API_KEY=your-new-key

Run (test with ONE question first):
    python guvi_solver.py --start "https://www.guvi.in/codekata/<first-question>/?status=start" --count 1
Then the real thing:j
    python guvi_solver.py --start "<same url>" --count 150

First run opens a browser: log in manually, press Enter in the terminal.
The login is kept in ./guvi_profile, so later runs skip it.
Results go to results.jsonl; re-running skips questions already solved.
"""
import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")  # set GEMINI_MODEL to a model you actually have
MAX_TRIES = 4
PROFILE_DIR = Path("guvi_profile")
LOG_FILE = Path("results.jsonl")
DEBUG_DIR = Path("debug")

RULES = (
    "Solve this problem in Python 3. Read all input from stdin, print ONLY what the output "
    "format asks for (no prompts, labels or extra text). Match the output format exactly. "
    "Reply with a single ```python code block and nothing else."
)


# ---------------------------------------------------------------- Gemini
def ask_gemini(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    for attempt in range(5):
        r = requests.post(url, headers={"x-goog-api-key": API_KEY}, json=body, timeout=120)
        if r.status_code == 200:
            try:
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError):
                return ""
            m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S)
            return (m.group(1) if m else text).strip()
        if r.status_code in (429, 500, 503):
            time.sleep(10 * (attempt + 1))
            continue
        sys.exit(f"Gemini error {r.status_code}: {r.text[:300]}")
    sys.exit("Gemini kept failing (rate limit?). Try again in a few minutes.")


def build_prompt(statement, history):
    p = f"{RULES}\n\nPROBLEM:\n{statement}\n"
    for code, feedback in history[-2:]:
        p += (
            f"\nYOUR PREVIOUS ATTEMPT:\n```python\n{code}\n```\n"
            f"JUDGE FEEDBACK (raw text from the result panel; any 'Output' shown may be the "
            f"expected output, not yours):\n{feedback[:2500]}\n"
        )
    if history:
        p += "\nFix the solution. Re-read the problem, the edge cases and the exact output format."
    return p


# ---------------------------------------------------------------- browser helpers
def wait_judge(page):
    """Wait for the 'Testing' progress dialog to open and close."""
    try:
        page.wait_for_function("() => document.querySelector('#progress_modal')?.open", timeout=5000)
    except PWTimeout:
        pass
    page.wait_for_function("() => !document.querySelector('#progress_modal')?.open", timeout=90000)
    time.sleep(1.5)


def dump(page, name):
    DEBUG_DIR.mkdir(exist_ok=True)
    path = DEBUG_DIR / f"{name}.html"
    path.write_text(page.content(), encoding="utf-8")
    return path


def set_code(page, code):
    page.evaluate("code => document.querySelector('#editor').env.editor.setValue(code, 1)", code)


def compile_error(page):
    err = page.locator(".compilationError:visible")
    return err.first.inner_text() if err.count() else None


def console_text(page):
    loc = page.locator("#code-kata-console")
    return loc.inner_text() if loc.count() else ""


def run_code(page):
    """Returns (status, feedback). status: pass / fail / unknown."""
    page.click(".run-code")
    wait_judge(page)
    err = compile_error(page)
    if err:
        return "fail", err
    if page.locator(".test-fail:visible").count():
        return "fail", console_text(page)
    if page.locator(".test-pass:visible").count():
        return "pass", ""
    return "unknown", ""


def submit_code(page):
    page.click(".submit-code")
    wait_judge(page)
    err = compile_error(page)
    if err:
        return "fail", err
    m = re.search(r"Test Cases\s+(\d+)\s*/\s*(\d+)", page.locator("body").inner_text())
    if not m:
        return "unknown", ""
    ok, total = map(int, m.groups())
    if ok == total:
        return "pass", ""
    return "fail", (
        f"Submission passed {ok}/{total} test cases. The sample cases pass, so a hidden case fails. "
        "No details are given. Think about edge cases: minimum/maximum values, zero, negatives, "
        "duplicates, empty or single-element input, whitespace, and the exact output format."
    )


# ---------------------------------------------------------------- per-question logic
def solve_current(page):
    statement = page.locator(".codekataQuestion").first.inner_text().strip()
    history = []
    for attempt in range(1, MAX_TRIES + 1):
        code = ask_gemini(build_prompt(statement, history))
        time.sleep(4)  # keep under free-tier rate limits
        if not code:
            history.append(("", "Empty response. Reply with a python code block."))
            continue
        set_code(page, code)

        status, fb = run_code(page)
        if status == "unknown":
            p = dump(page, "run_unknown")
            sys.exit(f"Could not read the Run result. Saved page to {p}. Send me that file's console section.")
        if status == "fail":
            print(f"  try {attempt}: Run failed")
            history.append((code, fb))
            continue

        status, fb = submit_code(page)
        if status == "unknown":
            p = dump(page, "submit_unknown")
            sys.exit(f"Could not read the Submit result. Saved page to {p}.")
        if status == "pass":
            return True, attempt
        print(f"  try {attempt}: Submit failed")
        history.append((code, fb))
    return False, MAX_TRIES


def go_next(page):
    old = page.url
    page.keyboard.press("Escape")  # closes the 'Geekoins earned' dialog if open
    time.sleep(0.5)
    page.click("#next-questions")
    try:
        page.wait_for_url(lambda u: u != old, timeout=15000)
    except PWTimeout:
        return False
    page.wait_for_selector(".codekataQuestion", timeout=30000)
    time.sleep(random.uniform(3, 7))
    return True


def load_done():
    done = set()
    if LOG_FILE.exists():
        for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("ok"):
                    done.add(row["key"])
    return done


def log(key, ok, tries):
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"key": key, "ok": ok, "tries": tries}) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="URL of the first question")
    ap.add_argument("--count", type=int, default=1, help="how many questions to go through")
    args = ap.parse_args()
    if not API_KEY:
        sys.exit("Set GEMINI_API_KEY first.")

    done = load_done()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, viewport={"width": 1440, "height": 900}
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(args.start)
        page.wait_for_selector(".codekataQuestion", timeout=60000)

        if page.locator("text=to run or submit the code").first.is_visible():
            input("Log in in the browser window, then press Enter here... ")
            page.goto(args.start)
            page.wait_for_selector(".codekataQuestion", timeout=60000)

        # ** old, claude
        # lang = page.eval_on_selector("#editorModeSelectDesktop", "e => e.value")
        # if lang != "PYTHON 3":
        #    sys.exit(f"Editor language is '{lang}'. Switch it to PYTHON 3 in the browser and re-run.")

        # ** new, from gemini
        # Read the text inside the visible dropdown component
        lang = page.locator(".ss-single").first.inner_text().strip()
        if "PYTHON" not in lang.upper():
            sys.exit(f"Editor language is '{lang}'. Switch it to PYTHON 3 in the browser and re-run.")
        
        for i in range(args.count):
            key = urlparse(page.url).path
            if key in done:
                print(f"[{i + 1}/{args.count}] {key} already done, skipping")
            else:
                print(f"[{i + 1}/{args.count}] {key}")
                ok, tries = solve_current(page)
                log(key, ok, tries)
                print(f"  -> {'SOLVED' if ok else 'FAILED'} after {tries} tries")
            if i < args.count - 1 and not go_next(page):
                print("No next question (URL did not change). Stopping.")
                break
        ctx.close()


if __name__ == "__main__":
    main()
