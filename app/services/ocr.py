"""OCR: native PDF text first, Tesseract for scanned PDFs and images. Fully offline."""
import io
from pathlib import Path

import fitz  # PyMuPDF

from app.config import settings
from app.services.errors import ServiceError

MIN_NATIVE_TEXT_CHARS = 40


class OcrError(ServiceError):
    code = "ocr_failed"
    status_code = 422


def _pytesseract():
    import pytesseract

    if settings.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
    return pytesseract


def ocr_image_bytes(data: bytes) -> str:
    from PIL import Image

    pt = _pytesseract()
    try:
        return pt.image_to_string(Image.open(io.BytesIO(data)))
    except pt.TesseractNotFoundError as exc:
        raise OcrError("Tesseract OCR is not installed or not on PATH (set TESSERACT_CMD).") from exc


def extract_text(path: str) -> tuple[str, str]:
    """Returns (text, method). Method describes which engine produced the text."""
    file_path = Path(path)
    data = file_path.read_bytes()
    if file_path.suffix.lower() == ".pdf":
        with fitz.open(stream=data, filetype="pdf") as doc:
            native = "\n".join(page.get_text() for page in doc)
            if len(native.strip()) >= MIN_NATIVE_TEXT_CHARS:
                return native, "pdf-text"
            pages = [ocr_image_bytes(page.get_pixmap(dpi=300).tobytes("png")) for page in doc]
        return "\n".join(pages), "tesseract-scanned-pdf"
    return ocr_image_bytes(data), "tesseract-image"
