import re
from io import BytesIO

import fitz  # PyMuPDF
from docx import Document as DocxDocument

from src.utils.logging import get_logger

logger = get_logger("text_extraction")


def extract_text_from_pdf(file_content: bytes) -> str:
    """Extract text from a PDF file using PyMuPDF.

    Handles multi-column layouts by sorting text blocks by position.
    Falls back to OCR for scanned PDFs with no extractable text.
    """
    doc = fitz.open(stream=file_content, filetype="pdf")
    all_text: list[str] = []

    for page_num, page in enumerate(doc):
        # Extract text blocks with position info
        blocks = page.get_text("blocks")

        if not blocks:
            # Page has no extractable text — try OCR fallback
            ocr_text = _ocr_page(page)
            if ocr_text:
                all_text.append(ocr_text)
            continue

        # Sort blocks: top-to-bottom, then left-to-right
        # This handles multi-column layouts
        sorted_blocks = sorted(blocks, key=lambda b: (round(b[1] / 20) * 20, b[0]))

        page_text_parts: list[str] = []
        for block in sorted_blocks:
            # block = (x0, y0, x1, y1, text, block_no, block_type)
            if block[6] == 0:  # text block (not image)
                text = block[4].strip()
                if text:
                    page_text_parts.append(text)

        if page_text_parts:
            all_text.append("\n".join(page_text_parts))

    doc.close()

    raw_text = "\n\n".join(all_text)
    return _normalize_whitespace(raw_text)


def _ocr_page(page: fitz.Page) -> str:
    """Attempt OCR on a page using Tesseract (if available)."""
    try:
        import pytesseract
        from PIL import Image

        # Render page as image at 300 DPI
        pix = page.get_pixmap(dpi=300)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        text = pytesseract.image_to_string(img)
        return text.strip()
    except ImportError:
        logger.warning("ocr_unavailable", detail="pytesseract or PIL not installed")
        return ""
    except Exception as e:
        logger.warning("ocr_failed", error=str(e))
        return ""


def extract_text_from_docx(file_content: bytes) -> str:
    """Extract text from a DOCX file preserving section structure.

    Preserves headings, bullet points, and extracts tables as structured text.
    """
    doc = DocxDocument(BytesIO(file_content))
    parts: list[str] = []

    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue

        style_name = paragraph.style.name if paragraph.style else ""

        # Preserve heading hierarchy
        if "Heading" in style_name:
            level = _extract_heading_level(style_name)
            prefix = "#" * level + " " if level else ""
            parts.append(f"\n{prefix}{text}")
        elif "List" in style_name or "Bullet" in style_name:
            parts.append(f"  - {text}")
        else:
            parts.append(text)

    # Extract tables
    for table in doc.tables:
        table_text = _extract_table(table)
        if table_text:
            parts.append(f"\n[Table]\n{table_text}")

    raw_text = "\n".join(parts)
    return _normalize_whitespace(raw_text)


def _extract_heading_level(style_name: str) -> int:
    """Extract heading level from style name like 'Heading 1', 'Heading 2'."""
    match = re.search(r"Heading\s*(\d)", style_name)
    if match:
        return int(match.group(1))
    return 1


def _extract_table(table) -> str:
    """Extract a DOCX table as pipe-separated text."""
    rows: list[str] = []
    for row in table.rows:
        cells = [cell.text.strip() for cell in row.cells]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _normalize_whitespace(text: str) -> str:
    """Clean up whitespace: collapse multiple blank lines, trim lines."""
    # Replace tabs with spaces
    text = text.replace("\t", " ")
    # Collapse multiple spaces (but preserve newlines)
    text = re.sub(r"[^\S\n]+", " ", text)
    # Collapse 3+ newlines into 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Trim each line
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def detect_file_type(file_content: bytes) -> str:
    """Detect file type from magic bytes."""
    if file_content[:4] == b"%PDF":
        return "pdf"
    # DOCX files are ZIP archives starting with PK
    if file_content[:2] == b"PK":
        return "docx"
    return "unknown"
