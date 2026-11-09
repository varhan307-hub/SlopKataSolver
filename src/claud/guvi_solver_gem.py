"""
GUVI CodeKata auto-solver v2 (Playwright + Gemini) - verbose, fails loudly.

Setup (Windows PowerShell):
    pip install playwright requests
    playwright install chromium
    $env:GEMINI_API_KEY="your-new-key"
    $env:GEMINI_MODEL="gemini-3.8-flash"     # exact model name that works for you

Step 1 - test the pipeline WITHOUT submitting anything:
    uv run python ./src/guvi_solver.py --start "https://www.guvi.in/code-kata/trapped-rain-water/" --count 1 --no-submit
Step 2 - real run:
    uv run python ./src/guvi_solver.py --start "https://www.guvi.in/code-kata/trapped-rain-water/" --count 150
    

Every step prints a timestamped line. On any error it saves debug/error.png
and debug/error.html. Send me the terminal output + those two files.
"""
import argparse
import json
import os
import random
import re
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import urlparse

import requests
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
MAX_TRIES = 4
PROFILE_DIR = Path("guvi_profile")
LOG_FILE = Path("results.jsonl")
DEBUG_DIR = Path("debug")
NO_SUBMIT = False

RULES = (
    "Solve this problem in Python 3. Read all input from stdin, print ONLY what the output "
    "format asks for (no prompts, labels or extra text). Match the output format exactly. "
    "Reply with a single ```python code block and nothing else."
)


def step(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- Gemini
def ask_gemini_original_claude(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    for attempt in range(1, 6):
        step(f"Gemini ({MODEL}): request, attempt {attempt}/5")
        try:
            r = requests.post(url, headers={"x-goog-api-key": API_KEY}, json=body, timeout=90)
        except requests.RequestException as e:
            step(f"Gemini network error: {e}")
            time.sleep(5)
            continue
        if r.status_code == 200:
            try:
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError):
                step(f"Gemini 200 but no text. Raw: {r.text[:300]}")
                return ""
            m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S)
            code = (m.group(1) if m else text).strip()
            step(f"Gemini: got {len(code)} chars of code")
            return code
        step(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
        if r.status_code in (429, 500, 503):
            wait = 10 * attempt
            step(f"Gemini busy/rate-limited, waiting {wait}s")
            time.sleep(wait)
            continue
        sys.exit("Gemini error is not retryable (wrong model name or bad key?). Fix GEMINI_MODEL / GEMINI_API_KEY.")
    sys.exit("Gemini kept failing after 5 attempts.")

def ask_gemini_new_from_gemini(prompt):
    # Try primary model, with automatic fallbacks if 503 / high demand hits
    models_to_try = [MODEL]
    for fallback in ["gemini-3.5-flash-lite", "gemini-3.6-flash"]:
        if fallback not in models_to_try:
            models_to_try.append(fallback)

    for current_model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{current_model}:generateContent"
        body = {"contents": [{"parts": [{"text": prompt}]}]}
        
        for attempt in range(1, 3):  # 2 attempts per model before trying fallback
            step(f"Gemini ({current_model}): request, attempt {attempt}/2")
            try:
                r = requests.post(url, headers={"x-goog-api-key": API_KEY}, json=body, timeout=60)
            except requests.RequestException as e:
                step(f"Gemini network error on {current_model}: {e}")
                time.sleep(3)
                continue
                
            if r.status_code == 200:
                try:
                    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError):
                    step(f"Gemini 200 but no text. Raw: {r.text[:300]}")
                    break
                m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S)
                code = (m.group(1) if m else text).strip()
                step(f"Gemini: got {len(code)} chars of code from {current_model}")
                return code
                
            step(f"Gemini HTTP {r.status_code} on {current_model}: {r.text[:300]}")
            if r.status_code in (429, 500, 503):
                wait = 5 * attempt
                step(f"Model busy/rate-limited, waiting {wait}s")
                time.sleep(wait)
                continue
            break  # Non-retryable error, move to next model fallback
            
    sys.exit("Gemini kept failing across all fallback models due to server-side demand. Try again shortly.")

ask_gemini = ask_gemini_new_from_gemini # **
    
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
def save_debug(page, name):
    DEBUG_DIR.mkdir(exist_ok=True)
    try:
        page.screenshot(path=str(DEBUG_DIR / f"{name}.png"))
    except Exception as e:
        step(f"(screenshot failed: {e})")
    try:
        (DEBUG_DIR / f"{name}.html").write_text(page.content(), encoding="utf-8")
    except Exception as e:
        step(f"(html dump failed: {e})")
    step(f"saved debug/{name}.png and debug/{name}.html")


def wait_judge(page):
    """Wait for the 'Testing' progress dialog to open and close."""
    try:
        page.wait_for_function("() => document.querySelector('#progress_modal')?.open", timeout=5000)
        step("judge: testing dialog opened, waiting for it to close")
    except PWTimeout:
        step("judge: testing dialog never opened within 5s (continuing anyway)")
    page.wait_for_function("() => !document.querySelector('#progress_modal')?.open", timeout=90000)
    time.sleep(1.5)


def set_code(page, code):
    page.evaluate("code => document.querySelector('#editor').env.editor.setValue(code, 1)", code)
    step("editor: code set")


def compile_error(page):
    err = page.locator(".compilationError:visible")
    return err.first.inner_text() if err.count() else None


def console_text(page):
    loc = page.locator("#code-kata-console")
    return loc.inner_text() if loc.count() else ""


def run_code(page):
    step("clicking Run Code")
    page.click(".run-code")
    wait_judge(page)
    err = compile_error(page)
    n_fail = page.locator(".test-fail:visible").count()
    n_pass = page.locator(".test-pass:visible").count()
    step(f"run result: compile_error={bool(err)} fail_marks={n_fail} pass_marks={n_pass}")
    if err:
        return "fail", err
    if n_fail:
        return "fail", console_text(page)
    if n_pass:
        return "pass", ""
    return "unknown", ""


def submit_code(page):
    step("clicking Submit")
    page.click(".submit-code")
    wait_judge(page)
    err = compile_error(page)
    if err:
        step("submit result: compile/runtime error")
        return "fail", err
    m = re.search(r"Test Cases\s+(\d+)\s*/\s*(\d+)", page.locator("body").inner_text())
    if not m:
        step("submit result: could not find 'Test Cases x/y' text")
        return "unknown", ""
    ok, total = map(int, m.groups())
    step(f"submit result: {ok}/{total}")
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
    step(f"statement read ({len(statement)} chars): {statement[:80]!r}...")
    history = []
    for attempt in range(1, MAX_TRIES + 1):
        step(f"--- attempt {attempt}/{MAX_TRIES}")
        code = ask_gemini(build_prompt(statement, history))
        time.sleep(4)  # stay under free-tier rate limits
        if not code:
            history.append(("", "Empty response. Reply with a python code block."))
            continue
        set_code(page, code)

        status, fb = run_code(page)
        if status == "unknown":
            save_debug(page, "run_unknown")
            sys.exit("Could not read the Run result. Send me debug/run_unknown.png and the terminal output.")
        if status == "fail":
            history.append((code, fb))
            continue

        if NO_SUBMIT:
            step("Run passed. --no-submit is on, so not submitting.")
            return True, attempt

        status, fb = submit_code(page)
        if status == "unknown":
            save_debug(page, "submit_unknown")
            sys.exit("Could not read the Submit result. Send me debug/submit_unknown.png and the terminal output.")
        if status == "pass":
            return True, attempt
        history.append((code, fb))
    return False, MAX_TRIES


def go_next_original_claude(page):
    old = page.url
    page.keyboard.press("Escape")  # closes the 'Geekoins earned' dialog if open
    time.sleep(0.5)
    step("clicking Next Question")
    page.click("#next-questions")
    try:
        page.wait_for_url(lambda u: u != old, timeout=15000)
    except PWTimeout:
        return False
    page.wait_for_selector(".codekataQuestion", timeout=30000)
    delay = random.uniform(3, 7)
    step(f"next question loaded, pausing {delay:.1f}s")
    time.sleep(delay)
    return True

def go_next_new_gemini(page):
    old = page.url
    
    # Force close any blocking modals/dialogs (like the points modal) via JS
    page.evaluate("""
        document.querySelectorAll('dialog').forEach(d => {
            try { d.close(); } catch(e) {}
            d.removeAttribute('open');
        });
    """)
    
    page.keyboard.press("Escape")  
    time.sleep(0.5)
    
    step("clicking Next Question")
    page.click("#next-questions")
    try:
        page.wait_for_url(lambda u: u != old, timeout=15000)
    except PWTimeout:
        return False
    page.wait_for_selector(".codekataQuestion", timeout=30000)
    delay = random.uniform(3, 7)
    step(f"next question loaded, pausing {delay:.1f}s")
    time.sleep(delay)
    return True

go_next = go_next_new_gemini

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


def open_start(page, url):
    step(f"opening {url}")
    page.goto(url, wait_until="domcontentloaded")
    step(f"loaded: title={page.title()!r} url={page.url}")
    try:
        page.wait_for_selector(".codekataQuestion", timeout=45000)
    except PWTimeout:
        save_debug(page, "error")
        sys.exit(
            f"Question text never appeared. Title was {page.title()!r}. "
            "Look at debug/error.png: bot check, login wall or wrong URL?"
        )
    step("question text found")


def main():
    global NO_SUBMIT
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="URL of the first question")
    ap.add_argument("--count", type=int, default=1, help="how many questions to go through")
    ap.add_argument("--no-submit", action="store_true", help="Run only, never Submit (for testing)")
    args = ap.parse_args()
    NO_SUBMIT = args.no_submit
    if not API_KEY:
        sys.exit("Set GEMINI_API_KEY first.")
    step(f"model={MODEL} count={args.count} no_submit={NO_SUBMIT}")

    done = load_done()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, viewport={"width": 1440, "height": 900}
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(20000)
        try:
            open_start(page, args.start)

            if page.locator("text=to run or submit the code").first.is_visible():
                input("Not logged in. Log in in the browser window, then press Enter here... ")
                open_start(page, args.start)

            lang = page.locator(".ss-single").first.inner_text().strip()
            step(f"editor language: {lang!r}")
            if "PYTHON" not in lang.upper():
                sys.exit(f"Editor language is '{lang}'. Switch it to PYTHON 3 in the browser and re-run.")

            for i in range(args.count):
                key = urlparse(page.url).path
                if key in done and not NO_SUBMIT:
                    step(f"[{i + 1}/{args.count}] {key} already done, skipping")
                else:
                    step(f"[{i + 1}/{args.count}] {key}")
                    ok, tries = solve_current(page)
                    if not NO_SUBMIT:
                        log(key, ok, tries)
                    step(f"  -> {'SOLVED' if ok else 'FAILED'} after {tries} tries")
                if i < args.count - 1 and not go_next(page):
                    step("No next question (URL did not change). Stopping.")
                    break
        except SystemExit:
            raise
        except Exception:
            traceback.print_exc()
            save_debug(page, "error")
            sys.exit("Crashed. Send me the traceback above plus debug/error.png.")
        finally:
            ctx.close()


if __name__ == "__main__":
    main()
    