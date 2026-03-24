"""Download Playwright browsers into ./playwright-browsers (included in the Vercel bundle)."""

import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent
    browsers = root / "playwright-browsers"
    browsers.mkdir(parents=True, exist_ok=True)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    # Playwright 1.58+ uses chromium-headless-shell for headless launch; install both.
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "playwright",
            "install",
            "chromium",
            "chromium-headless-shell",
        ],
    )


if __name__ == "__main__":
    main()
