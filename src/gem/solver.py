"""
GUVI CodeKata Production Auto-Solver v3.3
Modular Architecture with Exact Table Layout Parsing & Filtering.
"""

import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import List, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from dotenv import load_dotenv
from loguru import logger
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# =====================================================================
# 1. Config Module
# =====================================================================
class Config:
    def __init__(self):
        load_dotenv()
        self.api_key: str = os.environ.get("GEMINI_API_KEY", "")
        self.model: str = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
        self.default_url: str = "https://www.guvi.in/" + "code-kata/"
        self.max_tries: int = 4
        self.profile_dir: Path = Path("guvi_profile")
        self.logs_dir: Path = Path("logs")
        self.debug_dir: Path = Path("debug")
        self.completed_log: Path = self.logs_dir / "completed.jsonl"
        self.rules: str = (
            "Solve this problem in Python 3. Read all input from stdin, print ONLY what the output "
            "format asks for (no prompts, labels or extra text). Match the output format exactly. "
            "Reply with a single ```python code block and nothing else."
        )

    def setup_directories(self):
        self.logs_dir.mkdir(exist_ok=True)
        self.debug_dir.mkdir(exist_ok=True)
        self.profile_dir.mkdir(exist_ok=True)

    def configure_logger(self):
        logger.remove()
        logger.add(sys.stderr, level="INFO", format="<green>[{time:HH:mm:ss}]</green> <level>{message}</level>")
        logger.add(self.logs_dir / "solver.log", rotation="10 MB", level="DEBUG")


# =====================================================================
# 2. Gemini API Service
# =====================================================================
class GeminiService:
    def __init__(self, config: Config):
        self.config = config

    def build_prompt(self, statement: str, history: List[Tuple[str, str]]) -> str:
        prompt = f"{self.config.rules}\n\nPROBLEM:\n{statement}\n"
        for code, feedback in history[-2:]:
            prompt += (
                f"\nYOUR PREVIOUS ATTEMPT:\n```python\n{code}\n```\n"
                f"JUDGE FEEDBACK:\n{feedback[:2500]}\n"
            )
        if history:
            prompt += "\nFix the solution. Re-read the problem, edge cases, and exact output format."
        return prompt

    def generate_solution(self, prompt: str) -> str:
        # FIXED: Removed the Markdown brackets [ ] and ( ) that broke requests
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.config.model}:generateContent"
        
        body = {"contents": [{"parts": [{"text": prompt}]}]}

        for attempt in range(1, 4):
            logger.info(f"Querying Gemini ({self.config.model}) - attempt {attempt}/3")
            try:
                r = requests.post(
                    url,
                    headers={"x-goog-api-key": self.config.api_key},
                    json=body,
                    timeout=60,
                )
            except requests.RequestException as e:
                logger.warning(f"Network error on Gemini API: {e}")
                time.sleep(5 * attempt)
                continue

            if r.status_code == 200:
                try:
                    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError):
                    logger.error(f"Invalid API response: {r.text[:300]}")
                    return ""
                
                m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S)
                code = (m.group(1) if m else text).strip()
                logger.success(f"Generated {len(code)} chars of Python code.")
                return code

            logger.warning(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
            if r.status_code in (429, 500, 503):
                wait_time = 10 * attempt
                logger.info(f"Rate limited. Retrying in {wait_time}s...")
                time.sleep(wait_time)
                continue
            break

        console.print("[red]Critical: Gemini API failed after multiple attempts.[/red]")
        return ""


# =====================================================================
# 3. Problem Selector (Targeting GUVI CodeKata Table Layout)
# =====================================================================
class ProblemSelector:
    def __init__(self, config: Config):
        self.config = config

    def load_completed_keys(self) -> Set[str]:
        done = set()
        if self.config.completed_log.exists():
            for line in self.config.completed_log.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        row = json.loads(line)
                        if row.get("ok"):
                            done.add(row["key"])
                    except json.JSONDecodeError:
                        pass
        return done

    def get_problems_from_file(self, file_path: str) -> List[str]:
        path = Path(file_path)
        if not path.exists():
            logger.error(f"Problem file not found: {file_path}")
            sys.exit(1)
        urls = [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]
        logger.info(f"Loaded {len(urls)} problem URLs from {file_path}")
        return urls

    def fetch_filtered_dashboard(self, page, status_filter: str, difficulty_filter: str, count: int) -> List[str]:
        status_param = "start" if status_filter.lower() in ["unsolved", "start"] else status_filter
        dashboard_url = "https://www.guvi.in/code-kata/?status=" + status_param
        if difficulty_filter and difficulty_filter.lower() != "all":
            dashboard_url += "&difficulty=" + difficulty_filter.lower()
    
        print(f"[*] Navigating to: {dashboard_url}")
        page.goto(dashboard_url, wait_until="domcontentloaded")
    
        try:
            page.wait_for_selector("table tbody tr", timeout=20000)
        except Exception:
            print("[!] Table never rendered.")
            return []
    
        page.wait_for_timeout(2000)
    
        rows = page.locator("table tbody tr")
        total = rows.count()
        urls = []
    
        for i in range(total):
            if len(urls) >= count:
                break
            try:
                page.locator("table tbody tr").nth(i).click()
                page.wait_for_selector(".codekataQuestion", timeout=15000)
                page.wait_for_timeout(500)  # let it settle
                urls.append(page.url)
                
                page.goto(dashboard_url, wait_until="domcontentloaded")
                page.wait_for_selector("table tbody tr", timeout=15000)
                page.wait_for_timeout(1500)  # let the table repaint fully
            except Exception as e:
                print(f"[!] Row {i} failed: {e}")
                continue

        print(f"[*] Extracted Problem URLs: {urls}")
        return urls

# =====================================================================
# 4. GUVI Browser Automator
# =====================================================================
class GuviAutomator:
    def __init__(self, config: Config):
        self.config = config

    def save_debug(self, page, name: str):
        try:
            page.screenshot(path=str(self.config.debug_dir / f"{name}.png"))
            (self.config.debug_dir / f"{name}.html").write_text(page.content(), encoding="utf-8")
            logger.debug(f"Saved debug artifacts: {name}.png / {name}.html")
        except Exception as e:
            logger.error(f"Failed to save debug artifacts: {e}")

    def dismiss_modals(self, page):
        page.evaluate("""
            document.querySelectorAll('dialog, .modal').forEach(d => {
                try { d.close(); } catch(e) {}
                d.removeAttribute('open');
            });
        """)
        page.keyboard.press("Escape")

    def wait_judge(self, page):
        try:
            page.wait_for_function("() => document.querySelector('#progress_modal')?.open", timeout=5000)
        except PWTimeout:
            pass
        page.wait_for_function("() => !document.querySelector('#progress_modal')?.open", timeout=90000)
        time.sleep(1.0)

    def set_editor_code(self, page, code: str):
        page.evaluate("code => document.querySelector('#editor').env.editor.setValue(code, 1)", code)
        logger.debug("Injected code into editor.")

    def run_code(self, page) -> Tuple[str, str]:
        logger.info("Executing 'Run Code'...")
        page.click(".run-code")
        self.wait_judge(page)
        
        err = page.locator(".compilationError:visible")
        if err.count():
            return "fail", err.first.inner_text()
            
        if page.locator(".test-fail:visible").count():
            loc = page.locator("#code-kata-console")
            return "fail", loc.inner_text() if loc.count() else "Test failed."
            
        if page.locator(".test-pass:visible").count():
            return "pass", ""
            
        return "unknown", ""

    def submit_code(self, page) -> Tuple[str, str]:
        logger.info("Submitting code...")
        page.click(".submit-code")
        self.wait_judge(page)
        
        err = page.locator(".compilationError:visible")
        if err.count():
            return "fail", err.first.inner_text()
            
        m = re.search(r"Test Cases\s+(\d+)\s*/\s*(\d+)", page.locator("body").inner_text())
        if not m:
            return "unknown", ""
            
        ok, total = map(int, m.groups())
        logger.info(f"Submission results: {ok}/{total} passed.")
        if ok == total:
            return "pass", ""
            
        return "fail", f"Passed {ok}/{total} test cases. Hidden test case failed."


# =====================================================================
# 5. Execution Orchestrator
# =====================================================================
class ExecutionOrchestrator:
    def __init__(self, config: Config):
        self.config = config
        self.llm = GeminiService(config)
        self.selector = ProblemSelector(config)
        self.automator = GuviAutomator(config)

    def log_result(self, key: str, ok: bool, tries: int):
        with self.config.completed_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "key": key,
                "ok": ok,
                "tries": tries,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            }) + "\n")

    def solve_single_problem(self, page, no_submit: bool) -> Tuple[bool, int]:
        statement = page.locator(".codekataQuestion").first.inner_text().strip()
        console.print(Panel(statement[:300] + ("..." if len(statement) > 300 else ""), title="[bold cyan]Problem Statement[/bold cyan]"))
        
        history = []
        for attempt in range(1, self.config.max_tries + 1):
            logger.info(f"--- Attempt {attempt}/{self.config.max_tries} ---")
            code = self.llm.generate_solution(self.llm.build_prompt(statement, history))
            time.sleep(2)
            
            if not code:
                history.append(("", "Empty response received from LLM."))
                continue
                
            self.automator.set_editor_code(page, code)
            status, fb = self.automator.run_code(page)
            
            if status == "unknown":
                self.automator.save_debug(page, "run_unknown")
                return False, attempt
            if status == "fail":
                logger.warning(f"Run failed on attempt {attempt}")
                history.append((code, fb))
                continue

            if no_submit:
                console.print("[yellow]--no-submit enabled. Skipping final submission.[/yellow]")
                return True, attempt

            status, fb = self.automator.submit_code(page)
            if status == "unknown":
                self.automator.save_debug(page, "submit_unknown")
                return False, attempt
            if status == "pass":
                return True, attempt
                
            logger.warning(f"Submission failed on attempt {attempt}")
            history.append((code, fb))
            
        return False, self.config.max_tries

    def run(self, args):
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=str(self.config.profile_dir),
                headless=False,
                viewport={"width": 1280, "height": 800}
            )
            page = browser.pages[0] if browser.pages else browser.new_page()

            if args.start:
                problem_urls = [args.start]
            elif args.file:
                problem_urls = self.selector.get_problems_from_file(args.file)
            else:
                logger.info("Fetching filtered problems from GUVI dashboard...")
                problem_urls = self.selector.fetch_filtered_dashboard(page, args.status, args.difficulty, args.count)

            logger.info(f"Found {len(problem_urls)} problems to process.")
            completed_keys = self.selector.load_completed_keys()

            for idx, target_url in enumerate(problem_urls[:args.count], 1):
                parsed_key = urlparse(target_url).path.strip("/").split("/")[-1]
                if parsed_key in completed_keys:
                    logger.info(f"[{idx}/{len(problem_urls)}] Skipping already completed problem: {parsed_key}")
                    continue

                console.print(f"\n[bold green][*] Processing problem {idx}: {target_url}[/bold green]")
                try:
                    page.goto(target_url, wait_until="domcontentloaded")
                    page.wait_for_selector(".codekataQuestion", timeout=45000)
                    time.sleep(2)
                except Exception as e:
                    logger.error(f"Failed to load problem page {target_url}: {e}")
                    continue

                success, tries = self.solve_single_problem(page, args.no_submit)
                self.log_result(parsed_key, success, tries)

                if success:
                    console.print(f"[bold green][+] Problem successfully solved in {tries} tries![/bold green]")
                else:
                    console.print(f"[bold red][-] Failed to solve problem after {tries} tries.[/bold red]")

            logger.info("Batch execution completed.")
            browser.close()


# =====================================================================
# CLI Entry Point
# =====================================================================
def main():
    config = Config()
    parser = argparse.ArgumentParser(description="GUVI CodeKata Production Auto-Solver v3.3")
    
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--start", help="Direct URL of a single question")
    group.add_argument("--file", help="Path to a text file containing target problem URLs")

    parser.add_argument("--status", choices=["solved", "unsolved", "pending", "all"], default="unsolved",
                        help="Filter status query parameter (default: unsolved)")
    parser.add_argument("--difficulty", choices=["all", "easy", "medium", "hard"], default="all",
                        help="Filter problem difficulty from table row (default: all)")
    
    parser.add_argument("--count", type=int, default=3, help="Max number of questions to process in this run")
    parser.add_argument("--no-submit", action="store_true", help="Run sample checks without submitting")

    args = parser.parse_args()

    config.setup_directories()
    config.configure_logger()

    orchestrator = ExecutionOrchestrator(config)
    orchestrator.run(args)


if __name__ == "__main__":
    main()
    