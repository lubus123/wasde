"""Second independent reader for the scan era: Claude vision.

Tesseract and a VLM have differently-shaped failure modes: tesseract produces
visible garbage ('i,id6i'), a VLM can hallucinate plausible clean digits. So
neither is trusted alone — a cell is accepted when BOTH readers agree AND the
balance identities hold; disagreements go to the manual worklist
(scripts/06_vlm_reconcile.py). Two independent error processes almost never
produce the same wrong digit.

Responses are JSON-schema-constrained (structured outputs) and cached to
data/raw/vlm_json/{release_id}-p{NN}.json so the pass is resumable and
re-runs are free.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import anthropic
import fitz
from dotenv import load_dotenv
from PIL import Image

from wasde_data.config import PROJECT_ROOT

MODEL = "claude-opus-4-8"
MAX_EDGE = 2576  # native high-res vision limit on Opus 4.7+

_SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "commodity_heading": {
                        "type": "string",
                        "description": "The section heading exactly as printed, "
                                       "e.g. 'FEED GRAINS', 'CORN', 'SOYBEANS', "
                                       "'SOYBEAN OIL', 'SOYBEAN MEAL'",
                    },
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string",
                                          "description": "row label as printed"},
                                "values": {
                                    "type": "array",
                                    "description": "numeric cells left to right, "
                                                   "excluding any +/- tolerance "
                                                   "column; null where illegible",
                                    "items": {"type": ["number", "null"]},
                                },
                            },
                            "required": ["label", "values"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["commodity_heading", "rows"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["sections"],
    "additionalProperties": False,
}

_PROMPT = """\
This is a scanned page from a USDA WASDE report (1970s-90s fax-quality scan) \
containing commodity balance sheets. Transcribe every supply/use table on the \
page into the JSON schema.

Rules:
- Transcribe ONLY what is legibly printed. If a digit or number is too degraded \
to read with confidence, use null for that cell. Do NOT infer or reconstruct \
values from context, totals, or your knowledge of agricultural statistics — \
this output is cross-checked against an independent reader, and a guessed \
value is worse than a null.
- values: the numeric columns left to right. Skip '+/-NN' reliability/tolerance \
columns entirely. Price ranges like '2.50-2.70' -> transcribe as null (ranges \
are handled separately); single prices like '3.25' -> 3.25.
- Numbers may have thousands separators ('1,747' -> 1747).
- Include unit/heading rows' info only via section headings; skip rows with no \
numbers.
- Keep row labels exactly as printed (e.g. 'Ending stocks, total', \
'Outstdg. loans 3/').
"""


def _client() -> anthropic.Anthropic:
    load_dotenv(PROJECT_ROOT / ".env")
    return anthropic.Anthropic()


def render_page_png(pdf_path: Path, page_no: int) -> bytes:
    doc = fitz.open(pdf_path)
    pix = doc[page_no].get_pixmap(dpi=350, colorspace=fitz.csGRAY)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    if max(img.size) > MAX_EDGE:
        scale = MAX_EDGE / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)),
                         Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def read_page(client: anthropic.Anthropic, png: bytes) -> dict:
    """One vision call -> schema-validated table JSON."""
    import base64
    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/png",
                            "data": base64.standard_b64encode(png).decode()}},
                {"type": "text", "text": _PROMPT},
            ],
        }],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model refused the page")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def read_page_cached(client: anthropic.Anthropic, pdf_path: Path, page_no: int,
                     cache_dir: Path, release_id: str) -> dict:
    cache = cache_dir / f"{release_id}-p{page_no:02d}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    png = render_page_png(pdf_path, page_no)
    data = read_page(client, png)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, indent=1))
    return data
