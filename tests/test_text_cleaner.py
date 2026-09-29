from paper_assistant.text_cleaner import clean_page_text, remove_page_boilerplate


def test_clean_page_text_normalizes_layout_line_breaks() -> None:
    raw = "Retrieval -\nAugmented   Generation\r\nsystems"

    assert clean_page_text(raw) == "Retrieval-Augmented Generation systems"


def test_clean_page_text_normalizes_unicode_hyphens() -> None:
    raw = "A state\u2011of\u2011the\u2011art model"

    assert clean_page_text(raw) == "A state-of-the-art model"


def test_clean_page_text_preserves_paragraph_boundaries() -> None:
    raw = "First line\ncontinues here.\n\nSecond paragraph."

    assert clean_page_text(raw) == (
        "First line continues here.\n\nSecond paragraph."
    )


def test_clean_page_text_does_not_guess_ambiguous_word_boundaries() -> None:
    raw = "strict latency bud gets"

    assert clean_page_text(raw) == "strict latency bud gets"


def test_remove_page_boilerplate_removes_page_number_and_access_notice() -> None:
    raw = (
        "The final sentence of the paper body.\n"
        "1158\n"
        "Authorized licensed use limited to: Example University. "
        "Downloaded on September 29, 2026 from IEEE Xplore. "
        "Restrictions apply."
    )

    assert remove_page_boilerplate(raw) == "The final sentence of the paper body."


def test_remove_page_boilerplate_removes_duplicated_ieee_footer() -> None:
    raw = (
        "Body text.\n"
        "Example International Conference\n"
        "Example International Conference | $31.00 ©2025 IEEE | DOI: 10.1/test\n"
        "1157"
    )

    assert remove_page_boilerplate(raw) == "Body text."


def test_remove_page_boilerplate_keeps_body_year_and_doi() -> None:
    raw = "2025\nResults remain available at DOI: 10.1/body\nConclusion text."

    assert remove_page_boilerplate(raw) == raw
