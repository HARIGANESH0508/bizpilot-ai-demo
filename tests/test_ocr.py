"""OCR tests run only when Tesseract is installed. Everything else in the suite runs without it."""
import shutil

import fitz
import pytest

from app.services import ocr
from app.services.extraction import extract_invoice
from app.services.seed import text_to_pdf_bytes

requires_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None and ocr.settings.tesseract_cmd is None,
                                        reason="Tesseract not installed")


def _sample_text() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "data/samples/invoices/INV-2026-001_clean.txt").read_text(encoding="utf-8")


@requires_tesseract
def test_image_ocr_recovers_invoice_fields(tmp_path):
    doc = fitz.open(stream=text_to_pdf_bytes(_sample_text()), filetype="pdf")
    image = tmp_path / "scan.png"
    image.write_bytes(doc[0].get_pixmap(dpi=300).tobytes("png"))
    text, method = ocr.extract_text(str(image))
    assert method == "tesseract-image"
    extracted = extract_invoice(text, method)
    assert extracted.invoice_number == "INV-2026-001"
    assert extracted.grand_total == 13965.00
    assert len(extracted.items) == 2


@requires_tesseract
def test_image_only_pdf_uses_scanned_path(tmp_path):
    src = fitz.open(stream=text_to_pdf_bytes(_sample_text()), filetype="pdf")
    png = src[0].get_pixmap(dpi=300).tobytes("png")
    scanned = fitz.open()
    page = scanned.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=png)
    pdf_path = tmp_path / "scanned.pdf"
    pdf_path.write_bytes(scanned.tobytes())
    text, method = ocr.extract_text(str(pdf_path))
    assert method == "tesseract-scanned-pdf"
    assert "INV-2026-001" in text
