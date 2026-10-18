"""Conservative text cleanup for content extracted from academic PDFs."""

import re
import unicodedata


_DASH_TRANSLATION = str.maketrans(
    {
        "\u2010": "-",  # hyphen
        "\u2011": "-",  # non-breaking hyphen
        "\u2012": "-",  # figure dash
        "\u2013": "-",  # en dash
        "\u2212": "-",  # minus sign used as a hyphen in some PDFs
    }
)

_ACCESS_NOTICE_PATTERN = re.compile(
    r"authorized licensed use limited to:.*restrictions apply\.?$",
    re.IGNORECASE,
)
_DOWNLOAD_NOTICE_PATTERN = re.compile(
    r"downloaded on .* from ieee xplore",
    re.IGNORECASE,
)
_IEEE_COPYRIGHT_PATTERN = re.compile(
    r"(?:©|\(c\))\s*\d{4}\s+ieee",
    re.IGNORECASE,
)
_STANDALONE_PAGE_NUMBER_PATTERN = re.compile(r"\d{1,5}")


def remove_page_boilerplate(text: str, *, tail_line_count: int = 8) -> str:
    """Remove confidently identified publisher artifacts near a page footer.

    Only the final few non-empty lines are inspected. This keeps numbers and
    copyright references inside the paper body intact while removing page
    numbers, IEEE download notices, and duplicated footer/header text.
    """

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines()
    # Some exporters append a dense watermark block with clipped word fragments.
    # Only strip a terminal block made entirely of WATERMARK and its fragments,
    # with enough repetition to avoid removing ordinary mentions in the body.
    watermark_start = len(lines)
    for index in range(len(lines) - 1, -1, -1):
        fragments = lines[index].split()
        if fragments and not all(fragment in "WATERMARK" for fragment in fragments):
            break
        watermark_start = index
    watermark_lines = lines[watermark_start:]
    if len(watermark_lines) >= 3 and sum(
        line.split().count("WATERMARK") for line in watermark_lines
    ) >= 6:
        lines = lines[:watermark_start]
    non_empty_indexes = [index for index, line in enumerate(lines) if line.strip()]
    if not non_empty_indexes:
        return ""

    tail_indexes = non_empty_indexes[-tail_line_count:]
    removable_indexes: set[int] = set()

    for index in tail_indexes:
        line = " ".join(lines[index].split())
        if (
            _ACCESS_NOTICE_PATTERN.search(line)
            or _DOWNLOAD_NOTICE_PATTERN.search(line)
            or _IEEE_COPYRIGHT_PATTERN.search(line)
        ):
            removable_indexes.add(index)

    # Page numbers are normally the final line, or immediately precede an
    # access notice. Restricting the check to two tail positions avoids
    # deleting years and numeric section content from the paper body.
    final_two_indexes = set(non_empty_indexes[-2:])
    for index in tail_indexes:
        followed_by_known_artifact = any(
            artifact_index > index for artifact_index in removable_indexes
        )
        if (
            index in final_two_indexes or followed_by_known_artifact
        ) and _STANDALONE_PAGE_NUMBER_PATTERN.fullmatch(lines[index].strip()):
            removable_indexes.add(index)

    # IEEE PDFs can expose both a short conference footer and a longer footer
    # that contains the same text plus copyright/DOI information. If the long
    # artifact is removed, remove its duplicated tail fragment as well.
    removed_lines = [
        " ".join(lines[index].split()) for index in removable_indexes
    ]
    for index in tail_indexes:
        if index in removable_indexes:
            continue
        line = " ".join(lines[index].split())
        if len(line) >= 20 and any(line in removed for removed in removed_lines):
            removable_indexes.add(index)

    return "\n".join(
        line for index, line in enumerate(lines) if index not in removable_indexes
    ).strip()


def clean_page_text(text: str) -> str:
    """Normalize PDF text without guessing at ambiguous word boundaries.

    The cleaner fixes Unicode variants, layout line breaks, repeated spaces,
    and stray spaces around hyphens. Blank lines remain paragraph boundaries.
    It deliberately does not merge separated words such as ``"bud gets"``
    because doing so safely requires more context than a deterministic rule.
    """

    normalized = unicodedata.normalize("NFKC", text).translate(_DASH_TRANSLATION)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u00a0", " ")

    # A hyphen at a visual line boundary belongs with the surrounding words.
    normalized = re.sub(
        r"(?<=\w)\s*-\s*\n\s*(?=\w)",
        "-",
        normalized,
    )

    paragraphs = re.split(r"\n[ \t]*\n+", normalized)
    cleaned_paragraphs: list[str] = []

    for paragraph in paragraphs:
        # Fix artifacts such as "Retrieval -Augmented" and "up- to-date",
        # while leaving an intentional construction like "precision - recall".
        paragraph = re.sub(r"(?<=\w)\s+-(?=\w)", "-", paragraph)
        paragraph = re.sub(r"(?<=\w)-\s+(?=\w)", "-", paragraph)
        paragraph = re.sub(r"[ \t]*\n[ \t]*", " ", paragraph)
        paragraph = re.sub(r"[ \t]+", " ", paragraph).strip()

        if paragraph:
            cleaned_paragraphs.append(paragraph)

    return "\n\n".join(cleaned_paragraphs)
