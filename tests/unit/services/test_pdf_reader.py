from io import BytesIO

import pytest
from pypdf import PdfWriter

from info_assistant.services.pdf_reader import PdfReader, PdfReadError


def make_pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_reader_rejects_oversize_and_more_than_200_pages() -> None:
    reader = PdfReader()
    with pytest.raises(PdfReadError, match="大小上限"):
        reader.extract(make_pdf(1), max_bytes=10)
    with pytest.raises(PdfReadError, match="200 页"):
        reader.extract(make_pdf(201))


def test_pdf_reader_reports_scan_without_ocr_as_unsupported() -> None:
    with pytest.raises(PdfReadError) as error:
        PdfReader().extract(make_pdf(1))

    assert error.value.code == "scanned_pdf"
    assert "OCR" in error.value.message


def test_pdf_reader_rejects_invalid_pdf_without_leaking_payload() -> None:
    with pytest.raises(PdfReadError) as error:
        PdfReader().extract(b"private malformed payload")

    assert error.value.code == "invalid_pdf"
    assert "private malformed payload" not in str(error.value)
