"""Second independent reader for the scan era: GOT-OCR 2.0 (local, free).

Tesseract and GOT-OCR are independently-trained systems with different error
processes, so per-cell agreement between them is strong evidence the digit is
right. Neither is trusted alone: acceptance requires agreement + balance
identities (scripts/06_reconcile.py); disagreements go to the worklist.

GOT-OCR 2.0 (~580M params) runs on CPU here; page texts are cached under
data/raw/got_text/ so the pass is resumable.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

_MODEL_NAME = "stepfun-ai/GOT-OCR-2.0-hf"
_state: dict = {}


def _load():
    if "model" in _state:
        return _state["processor"], _state["model"]
    import torch
    from transformers import AutoModelForImageTextToText, AutoProcessor
    processor = AutoProcessor.from_pretrained(_MODEL_NAME)
    model = AutoModelForImageTextToText.from_pretrained(
        _MODEL_NAME, dtype=torch.float32, device_map="cpu")
    model.eval()
    _state.update(processor=processor, model=model)
    return processor, model


def ocr_png(png: bytes, max_new_tokens: int = 4096) -> str:
    processor, model = _load()
    img = Image.open(io.BytesIO(png)).convert("RGB")
    inputs = processor(img, return_tensors="pt")
    out = model.generate(**inputs, do_sample=False, tokenizer=processor.tokenizer,
                         stop_strings="<|im_end|>", max_new_tokens=max_new_tokens)
    return processor.decode(out[0, inputs["input_ids"].shape[1]:],
                            skip_special_tokens=True)


def ocr_page_cached(pdf_path: Path, page_no: int, cache_dir: Path,
                    release_id: str) -> str:
    from wasde_data.vlm_ocr import render_page_png
    cache = cache_dir / f"{release_id}-p{page_no:02d}.txt"
    if cache.exists():
        return cache.read_text()
    text = ocr_png(render_page_png(pdf_path, page_no))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text)
    return text
