"""
OCR di immagini ordine → stesso layout (lines, full_text) usato da template_engine.

Converte una o più immagini (pagine) in parole con coordinate x0/top compatibili
con l'output di pdfplumber, così match_template / apply_template / AI bootstrap
funzionano come su PDF nativo.

Include auto-rotazione (0/90/180/270) per foto di documenti scattate storte.
"""

from __future__ import annotations

import os
import re
import shutil
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageOps

try:
    import pytesseract
except ImportError as e:  # pragma: no cover
    raise RuntimeError(
        "Modulo pytesseract non installato. Esegui: pip install pytesseract Pillow"
    ) from e

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}

# conf OCR minimo (0-100). Sotto soglia = rumore scartato.
MIN_OCR_CONF = 0

# Lato minimo consigliato per OCR leggibile (upscale se più piccolo).
MIN_OCR_SIDE = 1600

# Keyword tipiche di ordini IT: aiutano a scegliere la rotazione giusta.
_ORIENT_KEYWORDS = (
    "ORDINE", "DESCRIZIONE", "QUANTITA", "QUANTITÀ", "ARTICOLO", "CODICE",
    "EAN", "IMPORTO", "CONSEGNA", "PARTITA", "CLIENTE", "RIGA", "TOTALE",
    "PZ", "CT", "COLLI", "SPETTABILE", "FATTURARE",
)

# Path tipici Windows se tesseract non e' nel PATH
_WINDOWS_TESSERACT_CANDIDATES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    r"C:\Users\{user}\AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
)


def _configure_tesseract():
    """Imposta pytesseract.pytesseract.tesseract_cmd se necessario."""
    env_cmd = os.environ.get("TESSERACT_CMD")
    if env_cmd and Path(env_cmd).exists():
        pytesseract.pytesseract.tesseract_cmd = env_cmd
        return

    which = shutil.which("tesseract")
    if which:
        pytesseract.pytesseract.tesseract_cmd = which
        return

    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    for candidate in _WINDOWS_TESSERACT_CANDIDATES:
        path = candidate.format(user=user)
        if Path(path).exists():
            pytesseract.pytesseract.tesseract_cmd = path
            return


def _ensure_tesseract():
    _configure_tesseract()
    try:
        pytesseract.get_tesseract_version()
    except Exception as e:
        raise RuntimeError(
            "Tesseract OCR non trovato.\n"
            "Installa Tesseract per Windows:\n"
            "  https://github.com/UB-Mannheim/tesseract/wiki\n"
            "Durante l'install seleziona le lingue Italian + English.\n"
            "Poi riapri il terminale, oppure imposta TESSERACT_CMD "
            "al path di tesseract.exe."
        ) from e


def is_image_path(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def _prepare_image(image: Image.Image) -> Image.Image:
    """EXIF transpose, RGB, eventuale upscale per OCR."""
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    w, h = image.size
    short = min(w, h)
    if short < MIN_OCR_SIDE and short > 0:
        scale = MIN_OCR_SIDE / short
        # cap per non esplodere memoria su foto già grandi in un lato
        scale = min(scale, 3.0)
        new_size = (int(w * scale), int(h * scale))
        image = image.resize(new_size, Image.Resampling.LANCZOS)
    return image


def _rotate(image: Image.Image, degrees: int) -> Image.Image:
    if degrees % 360 == 0:
        return image
    # expand=True tiene tutto il foglio dopo rotazione 90/270
    return image.rotate(-degrees, expand=True)


def _ocr_raw(image: Image.Image, lang: str = "ita+eng"):
    """image_to_data grezzo (dict tesseract)."""
    _ensure_tesseract()
    try:
        return pytesseract.image_to_data(
            image, lang=lang, output_type=pytesseract.Output.DICT
        )
    except pytesseract.TesseractError:
        if lang != "eng":
            return pytesseract.image_to_data(
                image, lang="eng", output_type=pytesseract.Output.DICT
            )
        raise


def _data_to_words(data):
    words = []
    confs = []
    n = len(data["text"])
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < MIN_OCR_CONF:
            continue
        if conf >= 0:
            confs.append(conf)
        words.append({
            "text": text,
            "x0": float(data["left"][i]),
            "top": float(data["top"][i]),
        })
    return words, confs


def _score_orientation(words, confs):
    """Punteggio: keyword ordine + conf media + densità parole."""
    if not words:
        return -1e9
    text_upper = " ".join(w["text"] for w in words).upper()
    # normalizza accenti grezzi
    text_upper = text_upper.replace("À", "A").replace("È", "E").replace("É", "E")
    kw_hits = sum(1 for kw in _ORIENT_KEYWORDS if kw in text_upper)
    avg_conf = (sum(confs) / len(confs)) if confs else 0.0
    # bonus se compaiono pattern numerici tipici (EAN 13, date, qty)
    ean_hits = len(re.findall(r"\b\d{13}\b", text_upper))
    date_hits = len(re.findall(r"\b\d{2}/\d{2}/\d{2,4}\b", text_upper))
    return (
        kw_hits * 40.0
        + avg_conf
        + min(len(words), 400) * 0.05
        + ean_hits * 8.0
        + date_hits * 5.0
    )


def _best_orientation(image: Image.Image, lang: str = "ita+eng"):
    """
    Prova 0/90/180/270, tiene la rotazione con score migliore.
    Ritorna (image_ruotata, words, degrees).
    """
    best = None  # (score, degrees, rotated, words)
    for degrees in (0, 90, 180, 270):
        rotated = _rotate(image, degrees)
        data = _ocr_raw(rotated, lang=lang)
        words, confs = _data_to_words(data)
        score = _score_orientation(words, confs)
        if best is None or score > best[0]:
            best = (score, degrees, rotated, words)

    assert best is not None
    return best[2], best[3], best[1]


def _ocr_page_words(image: Image.Image, lang: str = "ita+eng"):
    """
    Preprocess + auto-orient + OCR.
    Ritorna (words, page_height, rotation_degrees).
    """
    prepared = _prepare_image(image)
    oriented, words, degrees = _best_orientation(prepared, lang=lang)
    return words, float(oriented.height or 0), degrees


def _words_to_lines(words):
    """Raggruppa parole per Y arrotondato, come template_engine.get_lines."""
    buckets = defaultdict(list)
    for w in words:
        key = round(w["top"], 0)
        buckets[key].append(w)
    result = []
    for top in sorted(buckets.keys()):
        row = sorted(buckets[top], key=lambda w: w["x0"])
        result.append((float(top), row))
    return result


def _line_text(row):
    return " ".join(w["text"] for w in row)


def collect_lines_from_pil_images(images, lang: str = "ita+eng", labels=None):
    """
    OCR multipagina da oggetti PIL.Image (es. pagine PDF renderizzate).
    Ritorna (all_lines, full_text) con Y offset cumulativo tra pagine,
    stesso contratto di main._collect_all_pages sul PDF.
    """
    if not images:
        raise ValueError("Nessuna immagine fornita")

    all_lines = []
    y_offset = 0.0
    text_parts = []

    for i, im in enumerate(images):
        label = None
        if labels and i < len(labels):
            label = labels[i]
        words, page_height, degrees = _ocr_page_words(im, lang=lang)
        if degrees:
            tag = label or f"pagina {i + 1}"
            print(f"  OCR auto-rotate {tag}: {degrees}°")

        page_lines = _words_to_lines(words)
        for top, row in page_lines:
            all_lines.append((top + y_offset, row))
        text_parts.append("\n".join(_line_text(row) for _, row in page_lines))
        y_offset += page_height + 10.0

    return all_lines, "\n".join(text_parts)


def collect_lines_from_images(image_paths, lang: str = "ita+eng"):
    """
    OCR multipagina: N immagini in ordine = N pagine.
    Ritorna (all_lines, full_text) con Y offset cumulativo tra pagine,
    stesso contratto di main._collect_all_pages sul PDF.
    """
    if not image_paths:
        raise ValueError("Nessuna immagine fornita")

    images = []
    labels = []
    opened = []
    try:
        for path in image_paths:
            path = Path(path)
            im = Image.open(path)
            opened.append(im)
            # load() forza la lettura prima di chiudere il file
            im.load()
            images.append(im.copy())
            labels.append(path.name)
        return collect_lines_from_pil_images(images, lang=lang, labels=labels)
    finally:
        for im in opened:
            try:
                im.close()
            except Exception:
                pass


def validate_image_paths(paths):
    """Valida lista path: esistono e sono immagini supportate. Ritorna list[Path]."""

    resolved = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            raise FileNotFoundError(f"file '{path}' non trovato")
        if not path.is_file():
            raise ValueError(f"'{path}' non e' un file")
        if not is_image_path(path):
            raise ValueError(
                f"'{path}' non e' un'immagine supportata "
                f"(usa: {', '.join(sorted(IMAGE_EXTENSIONS))})"
            )
        resolved.append(path)
    return resolved
