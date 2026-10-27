"""Compatibility entry point; prefer python -m paper_assistant web."""

from paper_assistant.web.cli import main

if __name__ == "__main__":
    main()
