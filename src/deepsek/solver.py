"""
GUVI CodeKata Production Auto-Solver v5.0
Headless | Multi-Key Cache | Action-Based CLI | Rich Summaries
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from loguru import logger
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

console = Console()

# MINOR CHANGES:
# Use of regular expression alias `re.S` help: Replace with `re.DOTALL` (Ruff FURB167)


# =====================================================================
# 1. Config & Init Module
# =====================================================================
class Config:
    def __init__(self):
        load_dotenv()
        raw_keys = os.environ.get("GROQ_API_KEYS", "")
        self.api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        self.model = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

        self.logs_dir = Path("logs")
        self.debug_dir = Path("debug")
        self.completed_log = self.logs_dir / "completed.jsonl"
        self.error_log = self.logs_dir / "errors.jsonl"
        self.key_status_log = self.logs_dir / "key_status.json"

        self.rules = (
            "Solve this problem in Python 3. Read all input from stdin, print ONLY what the output "
            "format asks for (no prompts, labels or extra text). Match the output format exactly. "
            "Reply with a single ```python code block and nothing else."
        )

    def setup_dirs(self):
        self.logs_dir.mkdir(exist_ok=True)
        self.debug_dir.mkdir(exist_ok=True)

    def configure_logger(self):
        logger.remove()
        logger.add(
            sys.stderr,
            level="INFO",
            format="<green>[{time:HH:mm:ss}]</green> <level>{message}</level>",
        )
        logger.add(self.logs_dir / "solver.log", rotation="10 MB", level="DEBUG")

    @staticmethod
    def run_init_wizard():
        console.print(
            Panel("[bold cyan]Environment Setup Wizard[/bold cyan]", expand=False)
        )
        if Path(".env").exists() and not Confirm.ask(
            "[yellow].env file already exists. Overwrite?[/yellow]", default=False
        ):
            console.print("[red]Aborted.[/red]")
            return

        keys = Prompt.ask("Enter your Groq API Key(s) (comma separated)")
        keys = ",".join([k.strip() for k in keys.split(",") if k.strip()])
        model = Prompt.ask("Enter Groq Model", default="openai/gpt-oss-120b")

        with open(".env", "w") as f:
            f.write(f"GROQ_API_KEYS={keys}\n")
            f.write(f"GROQ_MODEL={model}\n")
        console.print("[bold green]✔ .env created successfully![/bold green]")


# =====================================================================
# 2. Key Manager (Cache & Rotation)
# =====================================================================
class KeyManager:
    def __init__(self, config: Config):
        self.config = config
        self.status = self._load_status()
        self.current_idx = 0

    def _load_status(self) -> dict:
        if self.config.key_status_log.exists():
            try:
                return json.loads(self.config.key_status_log.read_text())
            except json.JSONDecodeError:
                pass
        return {}

    def _save_status(self):
        self.config.key_status_log.write_text(json.dumps(self.status, indent=2))

    def get_ready_key(self) -> str:
        if not self.config.api_keys:
            return ""

        now = time.time()
        for _ in range(len(self.config.api_keys)):
            key = self.config.api_keys[self.current_idx]
            # Mask key for logging
            _masked = key[:8] + "..." + key[-4:]

            status = self.status.get(key, {})
            cooldown_until = status.get("cooldown_until", 0)

            if now >= cooldown_until:
                return key

            # Key is in cooldown, rotate
            self.current_idx = (self.current_idx + 1) % len(self.config.api_keys)

        return ""  # All keys exhausted

    def mark_cooldown(self, key: str, seconds: int, reason: str):
        self.status[key] = {
            "cooldown_until": time.time() + seconds,
            "reason": reason,
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._save_status()
        self.current_idx = (self.current_idx + 1) % len(self.config.api_keys)

    def print_status(self):
        if not self.config.api_keys:
            console.print("[red]No API keys configured. Run `--init`[/red]")
            return

        table = Table(title="API Key Status")
        table.add_column("Index", justify="right")
        table.add_column("Key", style="cyan")
        table.add_column("Status")
        table.add_column("Reason")

        now = time.time()
        for i, key in enumerate(self.config.api_keys):
            masked = key[:8] + "..." + key[-4:]
            status = self.status.get(key, {})
            cooldown = status.get("cooldown_until", 0)

            if now >= cooldown:
                table.add_row(str(i + 1), masked, "[green]Ready[/green]", "-")
            else:
                remaining = int(cooldown - now)
                mins = remaining // 60
                secs = remaining % 60
                table.add_row(
                    str(i + 1),
                    masked,
                    "[red]Cooldown[/red]",
                    f"{mins}m {secs}s left ({status.get('reason', '')})",
                )

        console.print(table)


# =====================================================================
# 3. LLM Service
# =====================================================================
class GroqService:
    def __init__(self, config: Config, key_mgr: KeyManager):
        self.config = config
        self.key_mgr = key_mgr

    def build_prompt(self, statement: str, history: list[tuple[str, str]]) -> str:
        prompt = f"{self.config.rules}\n\nPROBLEM:\n{statement}\n"
        for code, feedback in history[-2:]:
            prompt += f"\nPREVIOUS ATTEMPT:\n```python\n{code}\n```\nFEEDBACK:\n{feedback[:1500]}\n"
        if history:
            prompt += "\nFix the solution. Exact output format."
        return prompt

    def generate_solution(self, prompt: str) -> str:
        url = "https://api.groq.com/openai/v1/chat/completions"
        body = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }

        max_attempts = len(self.config.api_keys) * 2 if self.config.api_keys else 1

        for attempt in range(max_attempts):
            key = self.key_mgr.get_ready_key()
            if not key:
                logger.error("All API keys are in cooldown. Sleeping 1 hour...")
                try:
                    time.sleep(3600)
                except KeyboardInterrupt:
                    logger.warning("Sleep interrupted by user. Exiting.")
                    sys.exit(1)
                return self.generate_solution(prompt)

            try:
                r = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {key}"},
                    json=body,
                    timeout=60,
                )
            except requests.RequestException as e:
                logger.warning(f"Network error: {e}")
                time.sleep(5)
                continue

            if r.status_code == 200:
                try:
                    text = r.json()["choices"][0]["message"]["content"]
                except KeyError, IndexError:
                    return ""
                m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
                return (m.group(1) if m else text).strip()

            if r.status_code == 429:
                logger.warning(f"Rate limit hit on key {key[:8]}...")
                self.key_mgr.mark_cooldown(key, 24 * 3600, "Rate Limit (429)")
                continue

            if r.status_code in (500, 503):
                self.key_mgr.mark_cooldown(
                    key, 5 * 60, f"Server Error ({r.status_code})"
                )
                continue
            break

        return ""


# =====================================================================
# 4. GUVI Automator & Orchestrator
# =====================================================================
class GuviAutomator:
    def __init__(self, config: Config):
        self.config = config

    def save_debug(self, page, name: str):
        try:
            page.screenshot(path=str(self.config.debug_dir / f"{name}.png"))
            (self.config.debug_dir / f"{name}.html").write_text(
                page.content(), encoding="utf-8"
            )
        # TODO: Do not catch blind exception: `Exception` (Ruff BLE001)
        # TODO: `try`-`except`-`pass` detected, consider logging the exception (Ruff S110)
        except Exception:
            pass

    def wait_judge(self, page):
        try:
            page.wait_for_function(
                "() => document.querySelector('#progress_modal')?.open", timeout=5000
            )
        except PWTimeout:
            pass
        page.wait_for_function(
            "() => !document.querySelector('#progress_modal')?.open", timeout=90000
        )
        time.sleep(1.0)

    def set_editor_code(self, page, code: str):
        page.evaluate(
            "code => document.querySelector('#editor').env.editor.setValue(code, 1)",
            code,
        )

    def run_code(self, page) -> tuple[str, str]:
        page.click(".run-code")
        self.wait_judge(page)
        if page.locator(".compilationError:visible").count():
            return "fail", page.locator(".compilationError:visible").first.inner_text()
        if page.locator(".test-fail:visible").count():
            loc = page.locator("#code-kata-console")
            return "fail", loc.inner_text() if loc.count() else "Test failed."
        if page.locator(".test-pass:visible").count():
            return "pass", ""
        return "unknown", ""

    def submit_code(self, page) -> tuple[str, str]:
        page.click(".submit-code")
        self.wait_judge(page)
        if page.locator(".compilationError:visible").count():
            return "fail", page.locator(".compilationError:visible").first.inner_text()
        m = re.search(
            r"Test Cases\s+(\d+)\s*/\s*(\d+)", page.locator("body").inner_text()
        )
        if not m:
            return "unknown", ""
        ok, total = map(int, m.groups())
        return (
            ("pass", "")
            if ok == total
            else ("fail", f"Passed {ok}/{total} test cases.")
        )


class ExecutionOrchestrator:
    def __init__(self, config: Config):
        self.config = config
        self.key_mgr = KeyManager(config)
        self.llm = GroqService(config, self.key_mgr)
        self.automator = GuviAutomator(config)

    def get_completed_keys(self) -> set[str]:
        done = set()
        if self.config.completed_log.exists():
            for line in self.config.completed_log.read_text().splitlines():
                if line.strip():
                    try:
                        row = json.loads(line)
                        if row.get("ok"):
                            done.add(row["key"])
                    except json.JSONDecodeError:
                        pass
        return done

    def log_result(self, key: str, ok: bool, tries: int, error: str = ""):
        log_file = self.config.completed_log if ok else self.config.error_log
        with log_file.open("a") as f:
            f.write(
                json.dumps(
                    {
                        "key": key,
                        "ok": ok,
                        "tries": tries,
                        "error": error,
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
                + "\n"
            )

    def fetch_filtered_dashboard(
        self, page, status_filter: str, difficulty_filter: str, count: int
    ) -> list[str]:
        status_map = {
            "yet to start": "start",
            "in progress": "pending",
            "completed": "solved",
        }
        status_param = status_map.get(status_filter.lower(), "start")

        url = f"https://www.guvi.in/code-kata/?status={status_param}"
        if difficulty_filter.lower() != "all":
            url += f"&difficulty={difficulty_filter.lower()}"

        logger.info(f"Scraping: {url}")
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.wait_for_selector("table tbody tr", timeout=20000)
            # TODO: Do not catch blind exception: `Exception` (Ruff BLE001)
        except Exception:
            return []

        page.wait_for_timeout(2000)

        urls = []
        rows = page.locator("table tbody tr")
        for i in range(rows.count()):
            if len(urls) >= count:
                break
            try:
                rows.nth(i).click()
                page.wait_for_selector(".codekataQuestion", timeout=15000)
                urls.append(page.url)
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_selector("table tbody tr", timeout=15000)
            # TODO: `try`-`except`-`continue` detected, consider logging the exception (Ruff S112)
            except Exception:
                continue
        return urls

    def solve_single_problem(
        self, page, no_submit: bool, max_tries: int
    ) -> tuple[bool, int, str]:
        statement = page.locator(".codekataQuestion").first.inner_text().strip()
        history = []
        for attempt in range(1, max_tries + 1):
            logger.info(f"Attempt {attempt}/{max_tries}")
            code = self.llm.generate_solution(self.llm.build_prompt(statement, history))
            if not code:
                continue

            self.automator.set_editor_code(page, code)
            status, fb = self.automator.run_code(page)

            if status == "fail":
                history.append((code, fb))
                continue
            if status == "unknown":
                return False, attempt, "Unknown run"
            if no_submit:
                return True, attempt, ""

            status, fb = self.automator.submit_code(page)
            if status == "pass":
                return True, attempt, ""
            if status == "unknown":
                return False, attempt, "Unknown submit"
            history.append((code, fb))
        return False, max_tries, "Max tries"

    def cmd_stats(self, args):
        completed = self.get_completed_keys()
        errors = 0
        if self.config.error_log.exists():
            errors = sum(
                1
                for line in self.config.error_log.read_text().splitlines()
                if line.strip()
            )

        guvi_total = 0
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch_persistent_context(
                    user_data_dir=args.profile,
                    headless=True,
                    viewport={"width": 1280, "height": 800},
                )
                page = browser.pages[0] if browser.pages else browser.new_page()
                guvi_total = self.get_guvi_total(page, "completed")
                browser.close()
        # TODO: Do not catch blind exception: `Exception` (Ruff BLE001)
        except Exception:
            guvi_total = -1

        table = Table(title="Stats")
        table.add_column("Metric")
        table.add_column("Value", justify="right")
        table.add_row(
            "Total Solved on GUVI",
            str(guvi_total) if guvi_total >= 0 else "[red]could not fetch[/red]",
        )
        table.add_row("Solved by This Script", f"[green]{len(completed)}[/green]")
        table.add_row("Failed by This Script", f"[red]{errors}[/red]")
        console.print(table)

    def get_guvi_total(self, page, status_filter: str) -> int:
        status_map = {
            "yet to start": "start",
            "in progress": "pending",
            "completed": "solved",
        }
        status_param = status_map.get(status_filter.lower(), "start")
        try:
            page.goto(
                f"https://www.guvi.in/code-kata/?status={status_param}",
                wait_until="domcontentloaded",
            )
            page.wait_for_selector("table tbody tr", timeout=15000)
            body = page.locator("body").inner_text()
            m = re.search(r"Showing \d+ to \d+ of (\d+) results", body)
            return int(m.group(1)) if m else 0
        # TODO: Do not catch blind exception: `Exception` (Ruff BLE001)
        except Exception:
            return 0

    def run(self, args):
        with sync_playwright() as p:
            profile_dir = Path(args.profile)
            browser = p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=not args.visible and not args.login,
                viewport={"width": 1280, "height": 800},
            )
            page = browser.pages[0] if browser.pages else browser.new_page()

            if args.login:
                page.goto("https://www.guvi.in/login/", wait_until="domcontentloaded")
                console.print(
                    "[bold yellow]Please log in manually in the browser window.[/bold yellow]"
                )
                console.print("[bold yellow]Waiting up to 3 minutes...[/bold yellow]")
                try:
                    page.wait_for_function(
                        "() => localStorage.getItem('authToken') || sessionStorage.getItem('authToken')",
                        timeout=180000,
                    )
                    console.print(
                        "[bold green]✔ Login successful. Session saved.[/bold green]"
                    )
                except PWTimeout:
                    console.print("[bold red]✖ Login timed out.[/bold red]")
                browser.close()
                return

            if args.check_login:
                page.goto("https://www.guvi.in/", wait_until="domcontentloaded")
                time.sleep(2)
                token = page.evaluate(
                    "() => localStorage.getItem('authToken') || sessionStorage.getItem('authToken')"
                )
                if token:
                    console.print("[bold green]✔ Logged in.[/bold green]")
                else:
                    console.print(
                        "[bold red]✖ Not logged in. Run `--login`.[/bold red]"
                    )
                browser.close()
                return

            # Scrape
            hidden = browser.new_page()
            if args.url:
                urls = [args.url]
            elif args.file:
                urls = [
                    l.strip()
                    for l in Path(args.file).read_text().splitlines()
                    if l.strip() and not l.startswith("#")
                ]
            else:
                urls = self.fetch_filtered_dashboard(
                    hidden, args.status, args.difficulty, args.count
                )
            hidden.close()

            if not urls:
                logger.error("No URLs to process.")
                browser.close()
                return

            completed = self.get_completed_keys()
            summary = {"attempted": 0, "solved": 0, "failed": 0, "tries": 0}

            for idx, url in enumerate(urls[: args.count], 1):
                key = urlparse(url).path.strip("/").split("/")[-1]
                if key in completed:
                    continue
                summary["attempted"] += 1

                logger.info(f"[{idx}] Processing: {url}")
                try:
                    page.goto(url, wait_until="domcontentloaded")
                    page.wait_for_selector(".codekataQuestion", timeout=45000)
                # TODO: Do not catch blind exception: `Exception` (Ruff BLE001)
                except Exception as e:
                    logger.error(f"Load failed: {e}")
                    summary["failed"] += 1
                    continue

                success, tries, err = self.solve_single_problem(
                    page, args.no_submit, args.max_tries
                )
                summary["tries"] += tries
                self.log_result(key, success, tries, err)

                if success:
                    summary["solved"] += 1
                    console.print(f"[green]✔ Solved in {tries}[/green]")
                else:
                    summary["failed"] += 1
                    console.print("[red]✖ Failed[/red]")

            # Final Summary
            guvi_total = self.get_guvi_total(page, "completed")

            table = Table(title="Execution Summary")
            table.add_column("Metric")
            table.add_column("Value", justify="right")
            table.add_row("Attempted", str(summary["attempted"]))
            table.add_row("Solved", f"[green]{summary['solved']}[/green]")
            table.add_row("Failed", f"[red]{summary['failed']}[/red]")
            table.add_row("Total Tries", str(summary["tries"]))
            table.add_row(
                "GUVI Total Solved", str(guvi_total) if guvi_total >= 0 else "n/a"
            )
            table.add_row("Solver Total Solved", str(len(self.get_completed_keys())))
            console.print(table)

            browser.close()


# =====================================================================
# 5. CLI
# =====================================================================
def main():
    parser = argparse.ArgumentParser(
        description="GUVI CodeKata Auto-Solver v5.0",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    # Actions
    actions = parser.add_argument_group("Actions (pick one)")
    actions.add_argument(
        "--solve", action="store_true", help="Run the solver (use with filters)"
    )
    actions.add_argument("--url", type=str, help="Solve a single specific problem URL")
    actions.add_argument("--file", type=str, help="Path to text file with URLs")
    actions.add_argument(
        "--login", action="store_true", help="Open browser to login manually"
    )
    actions.add_argument(
        "--init", action="store_true", help="Create/overwrite .env file"
    )
    actions.add_argument(
        "--key-status", action="store_true", help="Show API key cooldowns"
    )
    actions.add_argument("--stats", action="store_true", help="Show local solved count")
    actions.add_argument(
        "--check-login", action="store_true", help="Check login status (headless)"
    )

    # Filters
    filters = parser.add_argument_group("Filters (with --solve)")
    filters.add_argument(
        "--status",
        type=str.lower,
        choices=["yet to start", "in progress", "completed", "all"],
        default="yet to start",
    )
    filters.add_argument(
        "--difficulty",
        type=str.lower,
        choices=["easy", "medium", "hard", "all"],
        default="all",
    )
    filters.add_argument("--count", type=int, default=3)
    filters.add_argument("--max-tries", type=int, default=4)

    # Options
    parser.add_argument(
        "--visible", action="store_true", help="Show browser (default headless)"
    )
    parser.add_argument("--no-submit", action="store_true")
    parser.add_argument(
        "--profile", type=str, default="guvi_profile", help="Profile dir name"
    )

    args = parser.parse_args()

    # Handle special actions first
    if args.init:
        Config.run_init_wizard()
        return

    config = Config()
    config.setup_dirs()
    config.configure_logger()

    if args.key_status:
        KeyManager(config).print_status()
        return

    orchestrator = ExecutionOrchestrator(config)

    if args.stats:
        orchestrator.cmd_stats(args)
        return

    if args.login or args.check_login:
        orchestrator.run(args)
        return

    # Requires action
    if not (args.solve or args.url or args.file):
        console.print("[yellow]No action specified. Did you mean `--solve`?[/yellow]")
        console.print("[yellow]Run with `-h` for help.[/yellow]")
        return

    orchestrator.run(args)


if __name__ == "__main__":
    main()
