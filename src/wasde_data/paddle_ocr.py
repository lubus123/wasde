"""Second independent reader for the scan era: PaddleOCR (local, free).

Chosen over GOT-OCR 2.0 after a head-to-head on the hardest sample page
(June 1985 corn): inside Docker on Apple Silicon (no GPU passthrough) GOT's
autoregressive decoder needs >12 min/page even int8-quantized, while
PaddleOCR's CNN det+rec pipeline reads the same page in minutes-to-seconds —
and matched the verified ground truth on every digit tesseract misread
(docs/DECISIONS.md).

Output is rebuilt into layout-ordered text lines and flows through the SAME
parse_page machinery as tesseract, so the two readers differ only in engine.
Page texts cache under data/raw/paddle_text/.
"""

from __future__ import annotations

import io
from pathlib import Path

_state: dict = {}

# det/rec model choice and render dpi are tuned for CPU-in-VM throughput;
# verified against the 1985 ground-truth page before adoption
DET_MODEL = "PP-OCRv5_mobile_det"
REC_MODEL = "PP-OCRv5_mobile_rec"
RENDER_DPI = 250
_LINE_Y_TOL = 14  # px at 250dpi: spans within this y-distance share a line


def _load():
    if "ocr" in _state:
        return _state["ocr"]
    from paddleocr import PaddleOCR
    _state["ocr"] = PaddleOCR(
        text_detection_model_name=DET_MODEL,
        text_recognition_model_name=REC_MODEL,
        use_doc_orientation_classify=False, use_doc_unwarping=False,
        use_textline_orientation=False, lang="en")
    return _state["ocr"]


def _render(pdf_path: Path, page_no: int):
    import fitz
    import numpy as np
    from PIL import Image
    doc = fitz.open(pdf_path)
    pix = doc[page_no].get_pixmap(dpi=RENDER_DPI, colorspace=fitz.csGRAY)
    return np.array(Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB"))


def rebuild_lines(boxes, texts, y_tol: int = _LINE_Y_TOL) -> str:
    """Group recognized spans into reading-order lines (y-cluster, x-sort)."""
    items = sorted(zip(boxes, texts, strict=False),
                   key=lambda bt: (bt[0][1] + bt[0][3]) / 2)
    lines, cur, cury = [], [], None
    for box, txt in items:
        yc = (box[1] + box[3]) / 2
        if cury is None or abs(yc - cury) < y_tol:
            cur.append((box[0], txt))
            cury = yc if cury is None else (cury + yc) / 2
        else:
            lines.append(" ".join(t for _, t in sorted(cur)))
            cur, cury = [(box[0], txt)], yc
    if cur:
        lines.append(" ".join(t for _, t in sorted(cur)))
    return "\n".join(lines)


def ocr_page(pdf_path: Path, page_no: int) -> str:
    result = _load().predict(_render(pdf_path, page_no))[0]
    return rebuild_lines(result["rec_boxes"], result["rec_texts"])


def ocr_page_cached(pdf_path: Path, page_no: int, cache_dir: Path,
                    release_id: str) -> str:
    cache = cache_dir / f"{release_id}-p{page_no:02d}.txt"
    if cache.exists():
        return cache.read_text()
    text = ocr_page(pdf_path, page_no)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text)
    return text
