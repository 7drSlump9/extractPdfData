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


def _data_to_words(data, img_width=None, img_height=None):
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
        x0 = float(data["left"][i])
        top = float(data["top"][i])
        # Normalizza coordinate in 0-1000 (permille) per portabilità
        if img_width and img_width > 0:
            x0 = x0 / img_width * 1000.0
        if img_height and img_height > 0:
            top = top / img_height * 1000.0
        words.append({
            "text": text,
            "x0": x0,
            "top": top,
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
        rw, rh = rotated.size
        words, confs = _data_to_words(data, img_width=rw, img_height=rh)
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


_easyocr_reader = None

def _get_easyocr_reader():
    global _easyocr_reader
    if _easyocr_reader is None:
        import numpy as np
        try:
            import easyocr
        except ImportError:
            raise RuntimeError(
                "Modulo easyocr non installato. Esegui: pip install easyocr"
            )
        _easyocr_reader = easyocr.Reader(['it', 'en'], gpu=False)
    return _easyocr_reader


def htr_zone_easyocr(image, x, y, w, h, config=None):
    """HTR con easyocr (testo stampato, ~100MB)."""
    import numpy as np
    cfg = config or {}
    margin = cfg.get('margin', 0.20)
    upscale_min = cfg.get('upscaleMin', 300)
    text_threshold = cfg.get('textThreshold', 0.3)
    contrast_ths = cfg.get('contrastThs', 0.1)
    adjust_contrast = cfg.get('adjustContrast', 0.5)
    lang = cfg.get('language', 'it')
    # Margine configurabile
    mx = max(3, int(w * margin))
    my = max(3, int(h * margin))
    x1 = max(0, x - mx)
    y1 = max(0, y - my)
    x2 = min(image.size[0], x + w + mx)
    y2 = min(image.size[1], y + h + my)
    cropped = image.crop((x1, y1, x2, y2))
    # Upscale se zona troppo piccola
    cw, ch = cropped.size
    if min(cw, ch) < upscale_min and min(cw, ch) > 0:
        scale = upscale_min / min(cw, ch)
        scale = min(scale, 4.0)
        cropped = cropped.resize((int(cw * scale), int(ch * scale)), Image.Resampling.LANCZOS)
    # easyocr vuole numpy array RGB
    if cropped.mode != 'RGB':
        cropped = cropped.convert('RGB')
    arr = np.array(cropped)
    try:
        reader = _get_easyocr_reader()
        results = reader.readtext(
            arr,
            detail=0,
            text_threshold=text_threshold,
            contrast_ths=contrast_ths,
            adjust_contrast=adjust_contrast,
        )
        text = " ".join(results).strip()
    except Exception:
        text = ""
    return text


_trocr_cache = {}

def _get_trocr(model_name="microsoft/trocr-base-handwritten"):
    """Carica (e cache per nome) processor+model TrOCR. model in eval mode."""
    global _trocr_cache
    if model_name not in _trocr_cache:
        try:
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel
        except ImportError:
            raise RuntimeError(
                "Moduli transformers/torch non installati. Esegui: pip install transformers torch"
            )
        print(f"[TrOCR] carico modello '{model_name}' (prima volta puo' scaricare ~1GB)...")
        processor = TrOCRProcessor.from_pretrained(model_name)
        model = VisionEncoderDecoderModel.from_pretrained(model_name)
        model.eval()
        _trocr_cache[model_name] = (processor, model)
    return _trocr_cache[model_name]


def _prep_line_for_trocr(line_img, preprocess, target_h=384, min_ratio=1.0,
                         pad_frac=0.12, sharpen=True):
    """
    Prepara una riga per TrOCR mantenendo aspect ratio.

    NOTE: i modelli TrOCR (specie i 'large') vogliono input grande e ben
    contrastato. Passi:
    - grayscale + autocontrast (NO binarizzazione dura, il modello vuole i grigi)
    - unsharp mask leggero (aiuta il tratto a penna sottile)
    - resize altezza fissa target_h (default 384, la risoluzione nativa del ViT)
      mantenendo le proporzioni
    - bordo bianco attorno (pad) cosi' le lettere alte/basse non toccano il bordo
    - pad orizzontale per raggiungere un ratio minimo (evita schiacciamento)
    """
    from PIL import ImageOps, ImageFilter
    img = line_img
    if img.mode != 'L':
        img = img.convert('L')
    if preprocess:
        img = ImageOps.autocontrast(img, cutoff=2)
        if sharpen:
            img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=120, threshold=2))

    w, h = img.size
    if h <= 0 or w <= 0:
        return img.convert('RGB')

    # padding verticale interno (bordo bianco alto/basso)
    pad = max(2, int(target_h * pad_frac))
    inner_h = max(1, target_h - 2 * pad)

    # resize mantenendo ratio all'altezza interna
    scale = inner_h / h
    new_w = max(1, int(w * scale))
    resized = img.resize((new_w, inner_h), Image.Resampling.LANCZOS)

    # tela bianca target_h di altezza, con bordi bianchi
    min_w = max(new_w + 2 * pad, int(target_h * min_ratio))
    canvas = Image.new('L', (min_w, target_h), 255)
    canvas.paste(resized, (pad, pad))
    return canvas.convert('RGB')


def _smooth(arr, k):
    """Media mobile 1D (finestra k). Serve a stabilizzare il projection profile."""
    import numpy as np
    if k <= 1:
        return arr
    kernel = np.ones(k, dtype=np.float32) / float(k)
    return np.convolve(arr, kernel, mode='same')


def _segment_lines(gray_img, row_thresh_ratio=0.02, min_line_h=8,
                   smooth_frac=0.015, merge_gap_frac=0.6, pad_frac=0.25):
    """
    Segmenta un crop in righe di testo via projection profile orizzontale.

    Migliorie rispetto alla versione base:
    - smoothing del profilo (media mobile) per non spezzare lettere con
      ascendenti/discendenti (l, t, g, p) o tratti sottili;
    - merge di segmenti separati da un gap piccolo (< merge_gap_frac * altezza media);
    - padding verticale su ogni riga cosi' le lettere non vengono tagliate.

    Ritorna lista di (top, bottom) in pixel. Se non trova nulla → [].
    """
    import numpy as np
    from PIL import ImageOps
    g = gray_img.convert('L')
    g = ImageOps.autocontrast(g, cutoff=2)
    arr = np.asarray(g).astype(np.float32)
    H = arr.shape[0]
    if H <= 0:
        return []
    # inchiostro scuro = valori bassi. Densità = quanto scuro per riga.
    darkness = 255.0 - arr
    row_energy = darkness.mean(axis=1)
    # smoothing: finestra proporzionale all'altezza del crop
    win = max(1, int(H * smooth_frac))
    row_energy = _smooth(row_energy, win)

    thresh = row_energy.max() * row_thresh_ratio + row_energy.mean() * 0.3
    mask = row_energy > thresh

    # 1) segmenti grezzi
    raw = []
    start = None
    for i, on in enumerate(mask):
        if on and start is None:
            start = i
        elif not on and start is not None:
            raw.append((start, i))
            start = None
    if start is not None:
        raw.append((start, H))
    if not raw:
        return []

    # 2) merge di segmenti separati da gap piccolo
    heights = [b - a for a, b in raw]
    avg_h = float(np.mean(heights)) if heights else 0.0
    merge_gap = max(2, int(avg_h * merge_gap_frac))
    merged = [list(raw[0])]
    for a, b in raw[1:]:
        if a - merged[-1][1] <= merge_gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])

    # 3) filtra righe troppo basse + padding verticale
    pad = max(1, int(avg_h * pad_frac))
    segments = []
    for a, b in merged:
        if b - a < min_line_h:
            continue
        top = max(0, a - pad)
        bottom = min(H, b + pad)
        segments.append((top, bottom))
    return segments


def htr_zone_trocr(image, x, y, w, h, config=None):
    """HTR con TrOCR. Gestisce multi-riga via segmentazione e mantiene aspect ratio."""
    import torch
    cfg = config or {}
    margin = cfg.get('margin', 0.20)
    preprocess = cfg.get('preprocess', True)
    num_beams = cfg.get('num_beams', 5)
    max_length = cfg.get('max_length', 64)
    model_name = cfg.get('model', "microsoft/trocr-base-handwritten")
    target_h = cfg.get('lineHeight', 384)
    multiline = cfg.get('multiline', True)
    length_penalty = cfg.get('lengthPenalty', 1.0)
    no_repeat_ngram = cfg.get('noRepeatNgramSize', 3)


    if w <= 0 or h <= 0:
        print(f"[TrOCR] zona degenere {w}x{h}, skip")
        return ""

    # Margine
    mx = max(3, int(w * margin))
    my = max(3, int(h * margin))
    x1 = max(0, x - mx)
    y1 = max(0, y - my)
    x2 = min(image.size[0], x + w + mx)
    y2 = min(image.size[1], y + h + my)
    cropped = image.crop((x1, y1, x2, y2))
    cw, ch = cropped.size
    if cw <= 0 or ch <= 0:
        print(f"[TrOCR] crop vuoto, skip")
        return ""

    # Segmenta in righe (se multiline). Altrimenti riga unica.
    line_boxes = []
    if multiline:
        try:
            line_boxes = _segment_lines(cropped)
        except Exception as e:
            print(f"[TrOCR] segmentazione fallita ({e}), uso riga unica")
            line_boxes = []
    if not line_boxes:
        line_boxes = [(0, ch)]

    try:
        processor, model = _get_trocr(model_name)
    except Exception as e:
        print(f"[TrOCR] ERRORE caricamento modello: {e}")
        return ""

    texts = []
    for (top, bottom) in line_boxes:
        line_crop = cropped.crop((0, top, cw, bottom))
        prepared = _prep_line_for_trocr(line_crop, preprocess, target_h=target_h)
        try:
            pixel_values = processor(prepared, return_tensors="pt").pixel_values
            with torch.no_grad():
                generated_ids = model.generate(
                    pixel_values,
                    num_beams=num_beams,
                    max_length=max_length,
                    early_stopping=True,
                    length_penalty=length_penalty,
                    no_repeat_ngram_size=no_repeat_ngram,
                )
            line_text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0].strip()
            if line_text:
                texts.append(line_text)
            print(f"[TrOCR] riga y[{top}:{bottom}] -> '{line_text}'")
        except Exception as e:
            import traceback
            print(f"[TrOCR] ERRORE inferenza riga: {e}")
            traceback.print_exc()

    result = "\n".join(texts).strip()
    print(f"[TrOCR] risultato zona ({len(line_boxes)} righe): '{result}'")
    return result



def htr_zone(image, x, y, w, h, config=None):
    """HTR dispatcher: sceglie motore in base a config.engine.
    Poi passa config[engine] (sottosezione) al motore scelto.
    - 'easyocr': testo stampato, veloce, ~100MB
    - 'trocr':   preciso, ~1GB (richiede transformers+torch).
                 Modello configurabile via config[trocr].model:
                   'microsoft/trocr-base-printed'     -> STAMPATELLO
                   'microsoft/trocr-base-handwritten' -> CORSIVO/manoscritto"""

    cfg = config or {}
    engine = cfg.get('engine', 'easyocr')
    engine_cfg = cfg.get(engine, {})
    print(f"[HTR] engine={engine} | zona={w}x{h}px")
    if engine == 'trocr':
        return htr_zone_trocr(image, x, y, w, h, engine_cfg)
    else:
        return htr_zone_easyocr(image, x, y, w, h, engine_cfg)


def ocr_zone(image, x, y, w, h, lang="ita+eng"):
    """OCR su una zona specifica dell'immagine con alta precisione."""
    _ensure_tesseract()
    # Margine 15% per OCR zonale (abbastanza per includere testo, ma non troppo)
    mx = max(5, int(w * 0.15))
    my = max(5, int(h * 0.15))
    x1 = max(0, x - mx)
    y1 = max(0, y - my)
    x2 = min(image.size[0], x + w + mx)
    y2 = min(image.size[1], y + h + my)
    cropped = image.crop((x1, y1, x2, y2))
    # Preprocess: scala x2, PSM 6 (block uniforme) per testo in zona piccola
    w2, h2 = cropped.size
    if w2 < 600 or h2 < 300:
        scale = min(4, max(2, int(600 / min(w2, h2))))
        cropped = cropped.resize((w2 * scale, h2 * scale), Image.Resampling.LANCZOS)
    try:
        text = pytesseract.image_to_string(
            cropped, lang=lang,
            config='--psm 6'
        ).strip()
    except pytesseract.TesseractError:
        text = pytesseract.image_to_string(
            cropped, lang='eng',
            config='--psm 6'
        ).strip()
    # Verifica risultato sensato (non solo rumore)
    if text and len(text) >= 2 and not all(c in '-—_\u2014\u2015\u2013' for c in text):
        return text
    # Retry con PSM 3 (più aggressivo)
    try:
        text = pytesseract.image_to_string(
            cropped, lang=lang,
            config='--psm 3'
        ).strip()
    except pytesseract.TesseractError:
        text = pytesseract.image_to_string(
            cropped, lang='eng',
            config='--psm 3'
        ).strip()
    return text


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


def collect_lines_from_pil_images(images, lang: str = "ita+eng", labels=None, auto_orient=True):
    """
    OCR multipagina da oggetti PIL.Image (es. pagine PDF renderizzate).
    Ritorna (all_lines, full_text, rotations) con Y offset cumulativo tra pagine.
    rotations: lista di gradi di rotazione applicati per ogni pagina (0/90/180/270).
    auto_orient: se True, prova auto-rotazione (foto). False per PDF (orientamento già corretto).
    """
    if not images:
        raise ValueError("Nessuna immagine fornita")

    all_lines = []
    y_offset = 0.0
    text_parts = []
    rotations = []

    for i, im in enumerate(images):
        label = None
        if labels and i < len(labels):
            label = labels[i]
        if auto_orient:
            words, page_height, degrees = _ocr_page_words(im, lang=lang)
        else:
            prepared = _prepare_image(im)
            data = _ocr_raw(prepared, lang=lang)
            rw, rh = prepared.size
            words, _ = _data_to_words(data, img_width=rw, img_height=rh)
            degrees = 0
            page_height = float(rh)
        rotations.append(degrees)
        if degrees:
            tag = label or f"pagina {i + 1}"
            print(f"  OCR auto-rotate {tag}: {degrees}°")

        page_lines = _words_to_lines(words)
        for top, row in page_lines:
            all_lines.append((top + y_offset, row))
        text_parts.append("\n".join(_line_text(row) for _, row in page_lines))
        y_offset += page_height + 10.0

    return all_lines, "\n".join(text_parts), rotations


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
