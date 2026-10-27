"""Internal bounded PDF extraction worker; the parent enforces a process timeout."""

import json
import logging
import sys
from dataclasses import asdict

from paper_assistant.tools.knowledge.pdf_parser import PDFLimitError, parse_pdf


class WarningCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()[:1000]
        if message not in self.messages and len(self.messages) < 200:
            self.messages.append(message)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    collector = WarningCollector()
    logger = logging.getLogger("pypdf")
    logger.handlers = [collector]
    logger.propagate = False
    try:
        pages = parse_pdf(sys.argv[1], max_pages=int(sys.argv[2]), max_text_chars=int(sys.argv[3]))
        result = {"status": "success", "pages": [asdict(page) for page in pages], "warnings": collector.messages}
    except PDFLimitError as error:
        result = {"status": "error", "code": error.code, "warnings": collector.messages}
    except Exception:
        # The isolated worker must report parser failures without dumping raw
        # PDF content or a traceback into an Agent observation.
        result = {"status": "error", "code": "parse_failed", "warnings": collector.messages}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
