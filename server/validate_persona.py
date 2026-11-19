"""Validate local persona without printing its contents."""
import argparse
from pathlib import Path

from .config import Settings
from .persona import load_persona


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path)
    args = parser.parse_args()
    try:
        persona = load_persona(args.path or Settings.from_env().persona_path)
    except ValueError as exc:
        parser.exit(1, str(exc) + "\n")
    print(f"Valid persona: {len(persona.public_documents())} public document(s).")


if __name__ == "__main__":
    main()
