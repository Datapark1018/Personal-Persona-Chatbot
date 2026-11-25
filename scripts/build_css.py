"""Compile with the official Tailwind CSS v4.3.5 standalone executable.

Download the executable for your OS from:
https://github.com/tailwindlabs/tailwindcss/releases/tag/v4.3.5
python scripts/build_css.py --cli data/tools/tailwindcss-v4.3.5.exe
Commit web/style.css along with changes to its HTML/JS/CSS sources.
"""
import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', required=True, type=Path)
    args = parser.parse_args()
    cli = args.cli.resolve()
    if not cli.is_file():
        parser.error('Tailwind standalone executable not found')
    subprocess.run([str(cli), '-i', 'web/tailwind.css', '-o', 'web/style.css', '--minify'], cwd=ROOT, check=True)
