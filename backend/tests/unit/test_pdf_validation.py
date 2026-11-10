"""Unit tests for PDF structural integrity verification and metric measurement."""

import io
import pytest
import pypdf
from app.core.exceptions import ValidationError
from app.services.report_renderer import ReportRendererService


def test_pdf_validation_empty_bytes():
    with pytest.raises(ValidationError, match="artifact is empty"):
        ReportRendererService.verify_and_measure_pdf(b"")


def test_pdf_validation_exceeds_size_cap():
    fake_huge_pdf = b"%PDF-1.4 " + (b"0" * 1024)
    with pytest.raises(ValidationError, match="exceeds limit"):
        ReportRendererService.verify_and_measure_pdf(fake_huge_pdf, max_bytes=500)


def test_pdf_validation_corrupted_structure():
    corrupt_bytes = b"This is not a real PDF document at all"
    with pytest.raises(ValidationError, match="structural integrity verification"):
        ReportRendererService.verify_and_measure_pdf(corrupt_bytes)


def test_pdf_validation_valid_pdf():
    # Build a minimal valid PDF in memory
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    stream = io.BytesIO()
    writer.write(stream)
    valid_pdf_bytes = stream.getvalue()

    pages, checksum, size = ReportRendererService.verify_and_measure_pdf(valid_pdf_bytes)
    assert pages == 2
    assert len(checksum) == 64
    assert size == len(valid_pdf_bytes)

