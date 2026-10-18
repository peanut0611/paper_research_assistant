from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Stable fixture independent of the user's replaceable paper directory."""
    writer = PdfWriter()
    for number in range(2):
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({
            NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject('/Helvetica'),
        })
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/Font'): DictionaryObject({NameObject('/F1'): font}),
        })
        stream = DecodedStreamObject()
        stream.set_data(
            f'BT /F1 12 Tf 50 700 Td (Hybrid Retrieval page {number + 1}) Tj '
            '0 -20 Td (Retrieval-Augmented research.) Tj '
            '0 -20 Td (Downloaded on today from IEEE Xplore) Tj ET'.encode('ascii')
        )
        page[NameObject('/Contents')] = writer._add_object(stream)
    destination = tmp_path / 'sample.pdf'
    writer.write(destination)
    return destination
