"""One application entry point: python -m paper_assistant [web|research]."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="Literature discovery and evidence analysis agent")
    parser.add_argument("command", nargs="?", choices=("web", "research", "chat"), default="web",
                        help="web: local page; research: new research; chat: execute a prepared discussion turn")
    args = sys.argv[1:]
    command = parser.parse_args(args[:1]).command
    original = sys.argv
    sys.argv = [f"{original[0]} {command}", *args[1:]]
    try:
        if command == "web":
            from paper_assistant.web.cli import main as run
        elif command == "research":
            from paper_assistant.research.cli import main as run
        else:
            from paper_assistant.research.conversation import main as run
        run()
    finally:
        sys.argv = original


if __name__ == "__main__":
    main()
