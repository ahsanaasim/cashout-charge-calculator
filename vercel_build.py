"""Download Playwright Chromium into the deployment bundle (Linux on Vercel)."""

import os
import subprocess
import sys


def main() -> None:
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
    subprocess.check_call(
        [sys.executable, "-m", "playwright", "install", "chromium"],
    )


if __name__ == "__main__":
    main()
