#!/usr/bin/env python3
"""
Estrattore dati ordini cliente da PDF o immagini - basato su template.

Sorgenti layout (policy diverse):
  - native     : PDF con testo digitale (-eo, testo ok)
  - ocr_pdf    : PDF scansionato / sola immagine (-eo fallback OCR)
  - ocr_image  : foto (-eoi)

Uso:
    python main.py -customer "Nome Cliente" -template "NOME" [-outputTemplatePath "PATH"] [-append] -eo <path_pdf>
    python main.py -customer "Nome Cliente" -template "NOME" [-outputTemplatePath "PATH"] [-append] -eoi <img1> [img2 ...]
"""

import json
import sys
from pathlib import Path

import pdfplumber

from template_engine import get_lines, line_text, match_template, apply_template
from db import db

# Carica configurazione da config.json (condiviso con frontend)
CONFIG_PATH = Path(__file__).parent / "web" / "src" / "config.json"
_config = {}
if CONFIG_PATH.exists():
    try:
        _config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass

# Sotto questa soglia di caratteri utili il PDF e' considerato "senza testo"
# (scansione / sola immagine) e si tenta l'OCR.
MIN_NATIVE_TEXT_CHARS = 20

# Risoluzione render pagine PDF per OCR (dpi). Default 200 se non in config.
PDF_OCR_DPI = int(_config.get("ocrDpi", 200))

# Gate OCR: frazione minima righe utili motore vs AI per salvare template
OCR_TEMPLATE_SAVE_RATIO = 0.8


def _collect_all_pages(pdf):
    """Lines + full_text da tutte le pagine (Y offset cumulativo).
    Coordinate normalizzate in 0-1000 (permille) per portabilità."""
    all_lines = []
    y_offset = 0.0
    text_parts = []
    for page in pdf.pages:
        page = page.dedupe_chars(tolerance=1)
        pw = float(page.width or 0) or 1.0
        ph = float(page.height or 0) or 1.0
        page_lines = get_lines(page)
        for top, row in page_lines:
            # Normalizza in 0-1000
            norm_top = (top / ph) * 1000.0 + y_offset
            norm_row = []
            for w in row:
                norm_row.append({
                    **w,
                    'x0': (w['x0'] / pw) * 1000.0,
                    'top': (w['top'] / ph) * 1000.0,
                })
            all_lines.append((norm_top, norm_row))
        text_parts.append("\n".join(line_text(row) for _, row in page_lines))
        y_offset += 1000.0 + 10.0  # offset in unità 0-1000
    return all_lines, "\n".join(text_parts)


def _has_usable_text(full_text):
    return len((full_text or "").strip()) >= MIN_NATIVE_TEXT_CHARS


def _embedded_fullpage_image(page):
    """
    Se la pagina e' essenzialmente una sola immagine full-page (scansione),
    ritorna un PIL.Image dall'embedded ad alta risoluzione; altrimenti None.
    """
    import io
    from PIL import Image

    images = page.images or []
    if len(images) != 1:
        return None
    im = images[0]
    pw = float(page.width or 0) or 1.0
    ph = float(page.height or 0) or 1.0
    if float(im.get("x0") or 0) > 5 or float(im.get("top") or 0) > 5:
        return None
    if float(im.get("width") or 0) < pw * 0.85 or float(im.get("height") or 0) < ph * 0.85:
        return None
    try:
        data = im["stream"].get_data()
        pil = Image.open(io.BytesIO(data))
        pil.load()
        return pil.copy()
    except Exception:
        return None


def _collect_pages_via_ocr(pdf, dpi=PDF_OCR_DPI):
    """
    OCR pagine PDF scansionate. Preferisce l'immagine embedded full-page
    (alta res); altrimenti render a dpi.
    Ritorna (lines, full_text, page_images) dove page_images e' lista di
    (img, page_width, page_height) per scaling coordinate.
    """
    from image_ocr import collect_lines_from_pil_images

    page_images = []
    page_dims = []
    for i, page in enumerate(pdf.pages):
        pw = float(page.width or 0)
        ph = float(page.height or 0)
        page_image = page.to_image(resolution=dpi)
        page_images.append(page_image.original.copy())
        page_dims.append((pw, ph))

    print(f"OCR su {len(page_images)} pagina/e PDF (render {dpi}dpi)...")
    lines, full_text, rotations = collect_lines_from_pil_images(page_images, labels=[f"pagina {i+1}" for i in range(len(page_images))], auto_orient=False)
    return lines, full_text, list(zip(page_images, page_dims, page_images)), rotations


def _valore_utile(v):
    """True se un campo estratto non e' vuoto/N/A."""
    if v is None:
        return False
    s = str(v).strip()
    return bool(s) and s.upper() not in ("N/A", "NA", "-", "")


def _righe_quality(righe):
    """Quante righe hanno almeno 2 campi utili (oltre a rumore OCR)."""
    good = 0
    for r in righe or []:
        if not isinstance(r, dict):
            continue
        useful = sum(1 for v in r.values() if _valore_utile(v))
        if useful >= 2:
            good += 1
    return good


def _riga_has_identity(riga):
    """True se la riga ha codice e/o descrizione utili (non solo qty/prezzo)."""
    if not isinstance(riga, dict):
        return False
    keys = ("codice_articolo", "codice", "articolo", "descrizione", "description")
    for k in keys:
        if k in riga and _valore_utile(riga.get(k)):
            return True
    for k, v in riga.items():
        if not _valore_utile(v):
            continue
        s = str(v).strip()
        if any(ch.isalpha() for ch in s) and len(s) >= 3:
            return True
    return False


def _identity_ratio(righe):
    if not righe:
        return 0.0
    n = sum(1 for r in righe if _riga_has_identity(r))
    return n / max(len(righe), 1)


def _attach_meta(dati, *, source, extraction_mode, template_saved, generato_da_ai,
                 q_ai=None, q_motore=None, customer_name=None):
    out = dict(dati or {})
    out["source"] = source
    out["extraction_mode"] = extraction_mode
    out["template_saved"] = bool(template_saved)
    out["generato_da_ai"] = bool(generato_da_ai)
    out["customer_name"] = customer_name or "UNKNOWN"
    quality = {
        "righe_totali": len(out.get("righe") or []),
        "righe_utili": _righe_quality(out.get("righe")),
    }
    if q_ai is not None:
        quality["righe_utili_ai"] = q_ai
    if q_motore is not None:
        quality["righe_utili_motore"] = q_motore
    out["quality"] = quality
    return out


def _pack_ai_dati(dati_ai, template):
    dati_out = dict(dati_ai or {})
    dati_out.setdefault("formato", (template or {}).get("name", "AI_UNSAVED"))
    if "righe" not in dati_out:
        dati_out["righe"] = []
    return dati_out


def _match_or_none(lines, full_text, customer_name="UNKNOWN", fuzzy=False, page_images=None):
    min_ratio = 0.5 if fuzzy else 1.0
    db_templates = db.get_all_templates(customer_name=customer_name)
    template = match_template(db_templates, full_text, min_match_ratio=min_ratio)
    if template:
        return apply_template(template, lines, full_text, page_images=page_images), template
    if customer_name != "UNKNOWN":
        all_db_templates = db.get_all_templates()
        template = match_template(all_db_templates, full_text, min_match_ratio=min_ratio)
        if template:
            return apply_template(template, lines, full_text, page_images=page_images), template
    return None


def _bootstrap_ai(lines, full_text, mode, customer_name="UNKNOWN", template_name=None):
    if not _has_usable_text(full_text):
        raise RuntimeError(
            "Impossibile chiamare l'AI: nessun testo/layout disponibile dal documento. "
            "Se e' un PDF scansionato, verifica che Tesseract OCR sia installato "
            "(tesseract --version) con lingue ita/eng, oppure usa "
            "python main.py -eoi <immagine>."
        )
    print("Formato non riconosciuto da nessun template esistente.")
    print(f"Interpello l'AI (mode={mode}) per dedurre template/dati...")
    from ai_bootstrap import bootstrap_new_template

    dati_ai, nuovo_template = bootstrap_new_template(lines, full_text, mode=mode)
    if template_name:
        nuovo_template["name"] = template_name
        print(f"Nome template forzato a: {template_name}")
    return dati_ai, nuovo_template


def _estrai_native(lines, full_text, source="native", customer_name="UNKNOWN", template_name=None):
    tpl = db.get_template_by_name(template_name) if template_name else None
    if tpl:
        print(f"Template '{template_name}' trovato nel DB. Applico direttamente.")
        dati = apply_template(tpl, lines, full_text)
        return _attach_meta(
            dati,
            source=source,
            extraction_mode="template",
            template_saved=True,
            generato_da_ai=False,
            customer_name=customer_name,
        )

    print(f"Template '{template_name}' non trovato nel DB. Creo nuovo con AI.")
    dati_ai, nuovo_template = _bootstrap_ai(
        lines, full_text, mode="native", customer_name=customer_name, template_name=template_name
    )
    db.save_template(nuovo_template, customer_name)
    print("Nuovo template (native) salvato nel DB.")

    dati = apply_template(nuovo_template, lines, full_text)
    q_ai = _righe_quality(dati_ai.get("righe", []))
    q_motore = _righe_quality(dati.get("righe", []))
    righe_ai = len(dati_ai.get("righe", []) or [])
    righe_motore = len(dati.get("righe", []) or [])

    if q_motore == 0 or (righe_ai > 0 and righe_motore == 0):
        print(
            f"ATTENZIONE: template native debole "
            f"(AI {q_ai}/{righe_ai} utili, motore {q_motore}/{righe_motore}). "
            f"Uso dati AI per questo documento."
        )
        packed = _pack_ai_dati(dati_ai, nuovo_template)
        return _attach_meta(
            packed,
            source=source,
            extraction_mode="ai_oneshot",
            template_saved=True,
            generato_da_ai=True,
            q_ai=q_ai,
            q_motore=q_motore,
            customer_name=customer_name,
        )

    if q_motore < q_ai or righe_motore != righe_ai:
        print(
            f"ATTENZIONE: template native da verificare "
            f"(motore {q_motore}/{righe_motore}, AI {q_ai}/{righe_ai})."
        )

    return _attach_meta(
        dati,
        source=source,
        extraction_mode="template",
        template_saved=True,
        generato_da_ai=True,
        q_ai=q_ai,
        q_motore=q_motore,
        customer_name=customer_name,
    )


def _estrai_ocr(lines, full_text, source="ocr_image", customer_name="UNKNOWN", template_name=None, page_images=None):
    tpl = db.get_template_by_name(template_name) if template_name else None
    if tpl:
        print(f"Template '{template_name}' trovato nel DB. Applico direttamente.")
        dati = apply_template(tpl, lines, full_text, page_images=page_images)
        return _attach_meta(
            dati,
            source=source,
            extraction_mode="template",
            template_saved=True,
            generato_da_ai=False,
            customer_name=customer_name,
        )

    print(f"Template '{template_name}' non trovato nel DB. Creo nuovo con AI.")
    dati_ai, nuovo_template = _bootstrap_ai(
        lines, full_text, mode="ocr", customer_name=customer_name, template_name=template_name
    )
    dati_motore = apply_template(nuovo_template, lines, full_text, page_images=page_images)

    righe_ai = dati_ai.get("righe", []) or []
    righe_motore = dati_motore.get("righe", []) or []
    q_ai = _righe_quality(righe_ai)
    q_motore = _righe_quality(righe_motore)
    id_ai = _identity_ratio(righe_ai)
    id_motore = _identity_ratio(righe_motore)

    template_ok = (
        q_motore > 0
        and q_ai > 0
        and q_motore >= max(1, int(OCR_TEMPLATE_SAVE_RATIO * max(q_ai, 1)))
        and id_motore >= 0.5
    )

    if not template_ok:
        db.save_template(nuovo_template, customer_name)
        print(
            f"ATTENZIONE: template OCR salvato nel DB come draft "
            f"(motore utili={q_motore}/{len(righe_motore)} id={id_motore:.0%}, "
            f"AI utili={q_ai}/{len(righe_ai)} id={id_ai:.0%}). "
            f"Rivedi le colonne nell'editor."
        )
        if q_ai >= q_motore and q_ai > 0:
            packed = _pack_ai_dati(dati_ai, nuovo_template)
        elif q_motore > 0:
            packed = dati_motore
        else:
            packed = _pack_ai_dati(dati_ai, nuovo_template)
        return _attach_meta(
            packed,
            source=source,
            extraction_mode="ai_oneshot",
            template_saved=True,
            generato_da_ai=True,
            q_ai=q_ai,
            q_motore=q_motore,
            customer_name=customer_name,
        )

    db.save_template(nuovo_template, customer_name)
    print("Template OCR salvato nel DB. Rivedi le colonne nell'editor.")
    if q_motore != q_ai or len(righe_motore) != len(righe_ai):
        print(
            f"ATTENZIONE: template OCR parziale "
            f"(motore {q_motore}/{len(righe_motore)}, AI {q_ai}/{len(righe_ai)})."
        )

    return _attach_meta(
        dati_motore,
        source=source,
        extraction_mode="template",
        template_saved=True,
        generato_da_ai=True,
        q_ai=q_ai,
        q_motore=q_motore,
        customer_name=customer_name,
    )


def estrai_ordine(pdf_path, customer_name="UNKNOWN", template_name=None):
    with pdfplumber.open(pdf_path) as pdf:
        lines, full_text = _collect_all_pages(pdf)

        if _has_usable_text(full_text):
            return _estrai_native(lines, full_text, source="native", customer_name=customer_name, template_name=template_name)

        print(
            "PDF senza testo nativo estraibile (probabile scansione o sola immagine). "
            "Provo OCR (immagine embedded o render pagine)..."
        )
        try:
            lines, full_text, page_images, rotations = _collect_pages_via_ocr(pdf)
        except RuntimeError as e:
            raise RuntimeError(
                f"Fallback OCR sul PDF fallito: {e}\n"
                "Installa Tesseract (https://github.com/UB-Mannheim/tesseract/wiki) "
                "con lingue Italian+English, oppure usa: python main.py -eoi <img>"
            ) from e

        if not _has_usable_text(full_text):
            if page_images:
                print("OCR globale ha prodotto poco testo. Uso comunque OCR zonale sui campi.")
            else:
                raise RuntimeError(
                    "OCR non ha estratto testo utile dal PDF. "
                    "Verifica qualita' della scansione e installazione Tesseract "
                    "(tesseract --version) con lingue ita/eng."
                )

        return _estrai_ocr(lines, full_text, source="ocr_pdf", customer_name=customer_name, template_name=template_name, page_images=page_images)


def estrai_ordine_immagini(image_paths, customer_name="UNKNOWN", template_name=None):
    from image_ocr import collect_lines_from_images, validate_image_paths

    paths = validate_image_paths(image_paths)
    print(f"OCR su {len(paths)} immagine/i...")
    lines, full_text = collect_lines_from_images(paths)
    if not full_text.strip():
        raise RuntimeError(
            "OCR non ha estratto testo dalle immagini. "
            "Verifica qualita' foto e che Tesseract sia installato "
            "(tesseract --version) con lingue ita/eng."
        )
    return _estrai_ocr(lines, full_text, source="ocr_image", customer_name=customer_name, template_name=template_name)


def stampa_risultati(dati):
    print("\n" + "=" * 80)
    print(f"ORDINE ESTRATTO - Formato: {dati.get('formato', 'N/A')}")
    print(
        f"source={dati.get('source', '?')}  "
        f"mode={dati.get('extraction_mode', '?')}  "
        f"template_saved={dati.get('template_saved', '?')}"
    )
    q = dati.get("quality") or {}
    if q:
        print(
            f"quality: righe={q.get('righe_totali')} utili={q.get('righe_utili')} "
            f"ai={q.get('righe_utili_ai')} motore={q.get('righe_utili_motore')}"
        )
    print("=" * 80)
    skip = {"formato", "righe", "source", "extraction_mode", "template_saved",
            "generato_da_ai", "quality"}
    for key, value in dati.items():
        if key in skip:
            continue
        print(f"{key}: {value}")

    righe = dati.get("righe", [])
    print(f"\n{'=' * 80}")
    print(f"RIGHE ({len(righe)} articoli)")
    print("=" * 80)
    for riga in righe:
        print(f"\n{json.dumps(riga, indent=2, ensure_ascii=False)}")


def _print_usage():
    print("Uso:")
    print("  python main.py -customer \"Nome Cliente\" -template \"NOME\" [-outputTemplatePath \"PATH\"] [-append] -eo <path_pdf>")
    print("  python main.py -customer \"Nome Cliente\" -template \"NOME\" [-outputTemplatePath \"PATH\"] [-append] -eoi <img1> [img2 ...]")
    print()
    print("Opzioni (ordine libero):")
    print("  -customer           Nome cliente (obbligatorio)")
    print("  -template (-t)      Nome template (obbligatorio). Se esiste nel DB lo usa, altrimenti lo crea con AI.")
    print("  -outputTemplatePath Path output JSON (opzionale, default: output/<file>_estratto.json)")
    print("  -append             Appende al JSON esistente (default: false)")
    print("  -eo                 PDF: testo nativo oppure OCR se scansione")
    print("  -eoi                Immagini/foto (OCR)")


def _save_json(dati, stem_source, output_path=None, append=False):
    if output_path:
        json_output = Path(output_path)
        json_output.parent.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = Path(__file__).parent / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        json_output = output_dir / (Path(stem_source).stem + "_estratto.json")

    if append and json_output.exists():
        try:
            with open(json_output, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if isinstance(existing, list):
                existing.append(dati)
                dati = existing
            else:
                dati = [existing, dati]
        except (json.JSONDecodeError, Exception):
            dati = [dati]
    elif append:
        dati = [dati]

    with open(json_output, "w", encoding="utf-8") as f:
        json.dump(dati, f, indent=2, ensure_ascii=False)
    print(f"\n\nJSON salvato in: {json_output}")
    if dati.get("generato_da_ai"):
        if dati.get("extraction_mode") == "ai_oneshot":
            print("NOTA: estrazione AI one-shot. Verifica i dati prima della produzione.")
        else:
            print("NOTA: template generato dall'AI. Verifica i dati prima della produzione.")


if __name__ == "__main__":
    if len(sys.argv) < 5:
        _print_usage()
        sys.exit(1)

    args = sys.argv[1:]
    customer_name = None
    template_name = None
    output_path = None
    append_mode = False
    comando = None
    comando_idx = -1

    i = 0
    while i < len(args):
        if args[i] == "-customer":
            if i + 1 >= len(args):
                print("Errore: -customer richiede un valore")
                _print_usage()
                sys.exit(1)
            customer_name = args[i + 1]
            i += 2
        elif args[i] == "-template" or args[i] == "-t":
            if i + 1 >= len(args):
                print("Errore: -template richiede un valore")
                _print_usage()
                sys.exit(1)
            template_name = args[i + 1]
            i += 2
        elif args[i] == "-outputTemplatePath":
            if i + 1 >= len(args):
                print("Errore: -outputTemplatePath richiede un valore")
                _print_usage()
                sys.exit(1)
            output_path = args[i + 1]
            i += 2
        elif args[i] == "-append":
            append_mode = True
            i += 1
        elif args[i] in ("-eo", "-eoi"):
            comando = args[i]
            comando_idx = i
            i += 1
        else:
            i += 1

    comando_args = args[comando_idx + 1:] if comando_idx >= 0 else []

    if not customer_name:
        print("Errore: -customer obbligatorio")
        _print_usage()
        sys.exit(1)

    if not template_name:
        print("Errore: -template obbligatorio")
        _print_usage()
        sys.exit(1)

    if comando == "-eo":
        if not comando_args:
            print("Errore: specifica un file PDF dopo -eo")
            _print_usage()
            sys.exit(1)
        pdf_file = comando_args[0]
        if not Path(pdf_file).exists():
            print(f"Errore: file '{pdf_file}' non trovato")
            sys.exit(1)

        print(f"Elaborazione: {pdf_file}  (customer: {customer_name})")
        try:
            dati = estrai_ordine(pdf_file, customer_name=customer_name, template_name=template_name)
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            print(f"Errore: {e}")
            db.log_event(
                action="extraction",
                document_name=pdf_file,
                template_name="ERROR",
                message=str(e),
                success=False,
                level="ERROR",
            )
            sys.exit(1)

        stampa_risultati(dati)
        _save_json(dati, pdf_file, output_path=output_path, append=append_mode)
        db.log_event(
            action="extraction",
            document_name=pdf_file,
            template_name=dati.get("formato", "UNKNOWN"),
            output_json=dati,
            success=True,
        )

    elif comando == "-eoi":
        if not comando_args:
            print("Errore: specifica almeno un'immagine dopo -eoi")
            _print_usage()
            sys.exit(1)

        print(f"Elaborazione immagini: {', '.join(comando_args)}  (customer: {customer_name})")
        try:
            dati = estrai_ordine_immagini(comando_args, customer_name=customer_name, template_name=template_name)
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            print(f"Errore: {e}")
            db.log_event(
                action="extraction",
                document_name=comando_args[0] if comando_args else "unknown",
                template_name="ERROR",
                message=str(e),
                success=False,
                level="ERROR",
            )
            sys.exit(1)

        stampa_risultati(dati)
        _save_json(dati, comando_args[0], output_path=output_path, append=append_mode)
        db.log_event(
            action="extraction",
            document_name=comando_args[0],
            template_name=dati.get("formato", "UNKNOWN"),
            output_json=dati,
            success=True,
        )

    else:
        print(f"Errore: comando sconosciuto '{comando}'")
        _print_usage()
        sys.exit(1)