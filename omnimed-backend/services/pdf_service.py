# PDF text extraction using PyMuPDF (fitz).

import fitz  # PyMuPDF


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """
    Extract all text from PDF bytes. Raises ValueError on parse failure.
    """
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        pages = [page.get_text() for page in doc]
        text = "\n".join(pages).strip()
        if not text:
            raise ValueError("PDF contained no extractable text")
        return text
    except Exception as e:
        raise ValueError(f"PDF_PARSE_FAILED: {e}") from e
