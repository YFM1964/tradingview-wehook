#!/usr/bin/env python3
"""
Build a portable ZIP package for the RSI spot bot.

This script creates:
  dist/rsi_spot_bot_portable.zip

Package contents:
  - rsi_spot_bot_kucoin_zones_with_volume.py
  - requirements.txt
  - run_windows.bat
  - run_linux.sh
  - README_FA.txt
"""

from __future__ import annotations

import shutil
from pathlib import Path


BOT_FILENAME = "rsi_spot_bot_kucoin_zones_with_volume.py"
PACKAGE_BASENAME = "rsi_spot_bot_portable"


README_FA = """\
بسته قابل‌انتقال ربات RSI Spot
=================================

فایل اصلی ربات:
  rsi_spot_bot_kucoin_zones_with_volume.py

نحوه اجرا در ویندوز:
1) روی run_windows.bat دابل‌کلیک کنید.
2) صبر کنید کتابخانه‌ها نصب شوند.
3) ربات اجرا می‌شود.

نحوه اجرا در لینوکس/مک:
1) در ترمینال:
   chmod +x run_linux.sh
   ./run_linux.sh

ارسال با ایمیل:
1) فایل zip را به ایمیل ضمیمه کنید.
2) در سیستم مقصد unzip کنید.
3) فایل اجرا (bat/sh) را اجرا کنید.
"""


RUN_WINDOWS_BAT = """\
@echo off
setlocal
echo Installing dependencies...
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
echo Running bot...
py rsi_spot_bot_kucoin_zones_with_volume.py
pause
"""


RUN_LINUX_SH = """\
#!/usr/bin/env bash
set -e
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 rsi_spot_bot_kucoin_zones_with_volume.py
"""


REQUIREMENTS_TXT = """\
ccxt
pandas
"""


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def main() -> None:
    root = Path(__file__).resolve().parent
    source_bot = root / BOT_FILENAME
    if not source_bot.exists():
        raise FileNotFoundError(f"Bot file not found: {source_bot}")

    dist_dir = root / "dist"
    package_dir = dist_dir / PACKAGE_BASENAME

    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)

    # Copy main bot script
    shutil.copy2(source_bot, package_dir / BOT_FILENAME)

    # Add helper files for portability
    write_text(package_dir / "requirements.txt", REQUIREMENTS_TXT)
    write_text(package_dir / "run_windows.bat", RUN_WINDOWS_BAT)
    write_text(package_dir / "run_linux.sh", RUN_LINUX_SH)
    write_text(package_dir / "README_FA.txt", README_FA)

    # Make shell script executable
    run_linux = package_dir / "run_linux.sh"
    run_linux.chmod(0o755)

    # Build ZIP archive
    dist_dir.mkdir(parents=True, exist_ok=True)
    archive_base = dist_dir / PACKAGE_BASENAME
    archive_path = shutil.make_archive(str(archive_base), "zip", root_dir=package_dir)

    print(f"Portable package created: {archive_path}")


if __name__ == "__main__":
    main()
