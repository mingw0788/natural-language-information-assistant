from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader as PypdfReader

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 200


class PdfReadError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ExtractedPdf:
    text: str
    page_count: int


class PdfReader:
    def extract(
        self,
        data: bytes,
        max_bytes: int = MAX_PDF_BYTES,
        max_pages: int = MAX_PDF_PAGES,
    ) -> ExtractedPdf:
        if len(data) > max_bytes:
            raise PdfReadError("pdf_too_large", "PDF 超过 20 MiB 大小上限")
        try:
            reader = PypdfReader(BytesIO(data), strict=True)
            page_count = len(reader.pages)
            if page_count > max_pages:
                raise PdfReadError("pdf_too_many_pages", "PDF 超过 200 页上限")
            text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
        except PdfReadError:
            raise
        except Exception:
            raise PdfReadError("invalid_pdf", "PDF 无法读取或文件格式无效") from None
        if not text:
            raise PdfReadError("scanned_pdf", "此 PDF 没有可提取的文字，首版不支持 OCR")
        return ExtractedPdf(text=text, page_count=page_count)
