"""Code128 scanline decoder. No OCR, native barcode library or neural model.

Pillow and NumPy contain native code, but no *additional* native dependency
is required. Checksum detection reduces errors; it cannot guarantee correctness.
"""
import base64
import io
import json
import os
from pathlib import Path
import re

import numpy as np
from PIL import Image

# ISO/IEC 15417 symbol widths, indices 0..106. Stop has seven runs.
PATTERNS = (
    "212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 "
    "221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 "
    "221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 "
    "212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 "
    "231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 "
    "231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
    "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 "
    "112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
    "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 "
    "214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 "
    "114131 311141 411131 211412 211214 211232 2331112"
).split()
_WIDTHS = np.array([[int(c) for c in p] for p in PATTERNS[:106]], dtype=float)


def _symbols_to_text(symbols):
    code_set = {103: "A", 104: "B", 105: "C"}[symbols[0]]
    result = []
    shift = False
    upper = False
    upper_once = False
    data = symbols[1:-1]
    i = 0
    while i < len(data):
        value = data[i]
        active = ({"A": "B", "B": "A"}[code_set] if shift else code_set)
        shift = False
        if active == "C" and value < 100:
            result.append(f"{value:02d}")
        elif value == 102:  # GS1 leading FNC1 is metadata, later FNC1 separates fields.
            if i:
                result.append("\x1d")
        elif active != "C" and value < 96:
            char = value + 32 if active == "B" or value < 64 else value - 64
            result.append(chr(char + (128 if upper != upper_once else 0)))
            upper_once = False
        elif value == 99:
            code_set = "C"
        elif value == 100 and active != "B":
            code_set = "B"
        elif value == 101 and active != "A":
            code_set = "A"
        elif value == 98 and active != "C":
            shift = True
        elif (value == 100 and active == "B") or (value == 101 and active == "A"):
            if i + 1 < len(data) and data[i + 1] == value:
                upper = not upper
                i += 1
            else:
                upper_once = True
        else:
            # FNC2/FNC3 and malformed control sequences are not guessed.
            return None
        i += 1
    return None if shift or upper_once else "".join(result)


def _decode_runs(runs):
    for start in range(0, len(runs) - 18, 2):
        if start and runs[start - 1] < sum(runs[start:start + 6]) / 11 * 3:
            continue
        widths = np.asarray(runs[start:start + 6], dtype=float)
        unit = widths.sum() / 11
        errors = np.mean((widths / unit - _WIDTHS[103:106]) ** 2, axis=1)
        if errors.min() > 0.20:
            continue
        symbols = [103 + int(errors.argmin())]
        pos = start + 6
        while pos + 6 < len(runs):
            stop = np.asarray(runs[pos:pos + 7], dtype=float)
            if len(symbols) >= 2 and len(stop) == 7:
                stop_unit = stop.sum() / 13
                if (0.65 < stop_unit / unit < 1.5
                        and np.mean((stop / stop_unit - np.array([2, 3, 3, 1, 1, 1, 2])) ** 2) < 0.20):
                    checksum = (symbols[0] + sum(i * v for i, v in enumerate(symbols[1:-1], 1))) % 103
                    if checksum == symbols[-1]:
                        text = _symbols_to_text(symbols)
                        if text:
                            return text
            widths = np.asarray(runs[pos:pos + 6], dtype=float)
            local_unit = widths.sum() / 11
            if not 0.65 < local_unit / unit < 1.5:
                break
            errors = np.mean((widths / local_unit - _WIDTHS[:103]) ** 2, axis=1)
            if errors.min() > 0.20:
                break
            symbols.append(int(errors.argmin()))
            pos += 6
    return None


def _scan(image):
    array = np.asarray(image, dtype=np.uint8)
    # Multiple heights cover a barcode anywhere inside the search rectangle.
    rows = np.unique(np.linspace(0, array.shape[0] - 1, min(81, array.shape[0])).astype(int))
    for y in rows:
        row = array[y]
        low, high = int(row.min()), int(row.max())
        if high - low < 40:
            continue
        for fraction in (0.5, 0.35, 0.65):
            dark = row < low + fraction * (high - low)
            for line in (dark, dark[::-1]):
                edges = np.flatnonzero(line[1:] != line[:-1]) + 1
                boundaries = np.r_[0, edges, len(line)]
                runs = np.diff(boundaries)
                if not line[0]:
                    runs = runs[1:]
                text = _decode_runs(runs)
                if text:
                    return text
    return None


def decode_code128(image, try_rotations=True):
    gray = image.convert("L")
    # Start with the unchanged crop; then deskew at bounded angles.
    angles = [0]
    if try_rotations:
        angles += [90, 180, 270]
        angles += [base + sign * angle for angle in range(2, 21, 2)
                   for base in (0, 90) for sign in (-1, 1)]
    for angle in angles:
        candidate = gray if not angle else gray.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)
        text = _scan(candidate)
        if text:
            return text
    return None


def _ai_decode(crop, settings):
    import requests
    from ai_bootstrap import _load_dotenv, OPENROUTER_URL
    _load_dotenv(Path(__file__).resolve().parent / ".env")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("Fallback barcode AI abilitato ma OPENROUTER_API_KEY assente")
    buffer = io.BytesIO()
    crop.convert("RGB").save(buffer, format="PNG")
    url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    response = requests.post(OPENROUTER_URL, headers={"Authorization": f"Bearer {key}"}, json={
        "model": settings["model"], "messages": [{"role": "user", "content": [
            {"type": "text", "text": 'Read the Code128 barcode in this crop. Do not guess. Return only JSON {"value": "decoded payload"} or {"value": null} if unreadable.'},
            {"type": "image_url", "image_url": {"url": url}},
        ]}],
    }, timeout=settings["timeout"])
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    value = json.loads(content).get("value")
    return value if isinstance(value, str) and value else None


def barcode_zone(image, x, y, w, h, config=None, ai_fallback=None):
    from template_store import load_config
    settings = config if config is not None else load_config()["barcode"]
    margin = float(settings.get("searchMargin", 0.10))
    if w <= 0 or h <= 0 or not 0 <= margin <= 1:
        raise ValueError("Riquadro barcode o margine non valido")
    iw, ih = image.size
    box = (max(0, int(x - w * margin)), max(0, int(y - h * margin)),
           min(iw, int(x + w * (1 + margin))), min(ih, int(y + h * (1 + margin))))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("Zona barcode fuori pagina")
    crop = image.crop(box)
    value = decode_code128(crop, settings.get("tryRotations", True))
    if value:
        return {"value": value, "method": "local", "verified": True}
    ai = settings.get("ai", {})
    # Global false is a hard privacy gate, never overridden by a zone.
    if ai.get("enabled", False) and ai_fallback is not False:
        try:
            value = _ai_decode(crop, ai)
        except Exception as exc:
            raise RuntimeError(f"Fallback AI barcode fallito: {type(exc).__name__}") from exc
        if value:
            return {"value": value, "method": "ai", "verified": False}
    return {"value": "N/A", "method": "none", "verified": False}