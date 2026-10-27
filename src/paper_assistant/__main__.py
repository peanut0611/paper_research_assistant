"""One application entry point: python -m paper_assistant [web|research]."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="Literature discovery and evidence analysis agent")
    parser.add_argument("command", nargs="?", choices=("web", "research"), default="web",
                        help="web: local research page (default); research: run a research task")
    args = sys.argv[1:]
    command = parser.parse_args(args[:1]).command
    original = sys.argv
    sys.argv = [f"{original[0]} {command}", *args[1:]]
    try:
        if command == "web":
            from paper_assistant.web.cli import main as run
        else:
            from paper_assistant.research.cli import main as run
        run()
    finally:
        sys.argv = original


if __name__ == "__main__":
    main()
