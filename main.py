#!/usr/bin/env python3
"""
Estrattore dati ordini cliente da PDF o immagini - basato su template.

Sorgenti layout (policy diverse):
  - native     : PDF con testo digitale (-eo, testo ok)
  - ocr_pdf    : PDF scansionato / sola immagine (-eo fallback OCR)
  - ocr_image  : foto (-eoi)

PDF nativo: prompt AI "native", template salvato in templates/ (gate leggero).
OCR: prompt AI "ocr", template salvato solo se quality-gate STRETTO
(altrimenti dati AI one-shot senza inquinare templates/).

Uso:
    python main.py -customer "Nome Cliente" -eo <path_pdf>
    python main.py -customer "Nome Cliente" -eoi <img1> [img2 ...]
"""

import json
import sys
from pathlib import Path

import pdfplumber

from template_engine import get_lines, line_text, load_templates, match_template, apply_template
from db import db

TEMPLATES_DIR = Path(__file__).parent / "templates"
# Template da OCR che passano il gate stretto (non auto-promossi in root)
TEMPLATES_DRAFT_OCR_DIR = TEMPLATES_DIR / "draft_ocr"

# Sotto questa soglia di caratteri utili il PDF e' considerato "senza testo"
# (scansione / sola immagine) e si tenta l'OCR.
MIN_NATIVE_TEXT_CHARS = 20

# Risoluzione render pagine PDF per OCR (dpi). 200 bilancia qualita'/tempo.
PDF_OCR_DPI = 200

# Gate OCR: frazione minima righe utili motore vs AI per salvare template
OCR_TEMPLATE_SAVE_RATIO = 0.8


def _collect_all_pages(pdf):
    """Lines + full_text da tutte le pagine (Y offset cumulativo)."""
    all_lines = []
    y_offset = 0.0
    text_parts = []
    for page in pdf.pages:
        # dedupe_chars: alcuni PDF (report legacy) disegnano due volte lo stesso
        # carattere con un micro-offset per simulare il grassetto (bold-by-double-strike).
        page = page.dedupe_chars(tolerance=1)
        page_lines = get_lines(page)
        for top, row in page_lines:
            all_lines.append((top + y_offset, row))
        text_parts.append("\n".join(line_text(row) for _, row in page_lines))
        y_offset += float(page.height or 0) + 10.0
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
    """
    from image_ocr import collect_lines_from_pil_images

    images = []
    labels = []
    sources = []
    for i, page in enumerate(pdf.pages):
        embedded = _embedded_fullpage_image(page)
        if embedded is not None:
            images.append(embedded)
            labels.append(f"pagina {i + 1} (embedded)")
            sources.append("embedded")
        else:
            page_image = page.to_image(resolution=dpi)
            images.append(page_image.original.copy())
            labels.append(f"pagina {i + 1} (render {dpi}dpi)")
            sources.append("render")

    n_emb = sources.count("embedded")
    n_ren = sources.count("render")
    print(
        f"OCR su {len(images)} pagina/e PDF "
        f"(embedded={n_emb}, render={n_ren})..."
    )
    return collect_lines_from_pil_images(images, labels=labels)


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
    # fallback: qualsiasi campo testuale non numerico lungo
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
                 q_ai=None, q_motore=None, template_path=None, customer_name=None):
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
    if template_path is not None:
        quality["template_path"] = str(template_path)
    out["quality"] = quality
    return out


def _pack_ai_dati(dati_ai, template):
    dati_out = dict(dati_ai or {})
    dati_out.setdefault("formato", (template or {}).get("name", "AI_UNSAVED"))
    if "righe" not in dati_out:
        dati_out["righe"] = []
    return dati_out


def _match_or_none(lines, full_text):
    templates = load_templates(TEMPLATES_DIR)
    template = match_template(templates, full_text)
    if template:
        return apply_template(template, lines, full_text), template
    return None, None


def _bootstrap_ai(lines, full_text, mode, customer_name="UNKNOWN"):
    if not _has_usable_text(full_text):
        raise RuntimeError(
            "Impossibile chiamare l'AI: nessun testo/layout disponibile dal documento. "
            "Se e' un PDF scansionato, verifica che Tesseract OCR sia installato "
            "(tesseract --version) con lingue ita/eng, oppure usa "
            "python main.py -eoi <immagine>."
        )
    print("Formato non riconosciuto da nessun template esistente.")
    print(f"Interpello l'AI (mode={mode}) per dedurre template/dati...")
    from ai_bootstrap import bootstrap_new_template, save_template

    dati_ai, nuovo_template = bootstrap_new_template(lines, full_text, mode=mode)
    return dati_ai, nuovo_template, save_template, customer_name


def _estrai_native(lines, full_text, source="native", customer_name="UNKNOWN"):
    """
    PDF testo digitale: prompt native, template in templates/ (gate leggero).
    Ritorna dati (con meta).
    """
    matched = _match_or_none(lines, full_text)
    if matched is not None:
        dati, template = matched
        db.save_template(template, customer_name)
        return _attach_meta(
            dati,
            source=source,
            extraction_mode="template",
            template_saved=False,
            generato_da_ai=False,
            customer_name=customer_name,
        )

    dati_ai, nuovo_template, save_template, customer_name = _bootstrap_ai(
        lines, full_text, mode="native", customer_name=customer_name
    )
    saved_path = save_template(nuovo_template, TEMPLATES_DIR)
    db.save_template(nuovo_template, customer_name)
    print(f"Nuovo template (native) salvato in: {saved_path}")

    # Riapplica template: i dati AI non provano che il template funzioni.
    dati = apply_template(nuovo_template, lines, full_text)
    q_ai = _righe_quality(dati_ai.get("righe", []))
    q_motore = _righe_quality(dati.get("righe", []))
    righe_ai = len(dati_ai.get("righe", []) or [])
    righe_motore = len(dati.get("righe", []) or [])

    if q_motore == 0 or (righe_ai > 0 and righe_motore == 0):
        print(
            f"ATTENZIONE: template native debole "
            f"(AI {q_ai}/{righe_ai} utili, motore {q_motore}/{righe_motore}). "
            f"Uso dati AI per questo documento; controlla {saved_path}."
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
            template_path=saved_path,
            customer_name=customer_name,
        )

    if q_motore < q_ai or righe_motore != righe_ai:
        print(
            f"ATTENZIONE: template native da verificare "
            f"(motore {q_motore}/{righe_motore}, AI {q_ai}/{righe_ai}). "
            f"Controlla {saved_path}."
        )

    return _attach_meta(
        dati,
        source=source,
        extraction_mode="template",
        template_saved=True,
        generato_da_ai=True,
        q_ai=q_ai,
        q_motore=q_motore,
        template_path=saved_path,
        customer_name=customer_name,
    )


def _estrai_ocr(lines, full_text, source="ocr_image", customer_name="UNKNOWN"):
    """
    OCR (foto o PDF scansionato): prompt ocr.
    Template salvato in draft_ocr/ SOLO se gate stretto; altrimenti ai_oneshot.
    """
    matched = _match_or_none(lines, full_text)
    if matched is not None:
        dati, template = matched
        db.save_template(template, customer_name)
        return _attach_meta(
            dati,
            source=source,
            extraction_mode="template",
            template_saved=False,
            generato_da_ai=False,
            customer_name=customer_name,
        )

    dati_ai, nuovo_template, save_template, customer_name = _bootstrap_ai(
        lines, full_text, mode="ocr", customer_name=customer_name
    )
    dati_motore = apply_template(nuovo_template, lines, full_text)

    righe_ai = dati_ai.get("righe", []) or []
    righe_motore = dati_motore.get("righe", []) or []
    q_ai = _righe_quality(righe_ai)
    q_motore = _righe_quality(righe_motore)
    id_ai = _identity_ratio(righe_ai)
    id_motore = _identity_ratio(righe_motore)

    # Gate stretto: motore riproduce bene + almeno parte delle righe ha identita'
    template_ok = (
        q_motore > 0
        and q_ai > 0
        and q_motore >= max(1, int(OCR_TEMPLATE_SAVE_RATIO * max(q_ai, 1)))
        and id_motore >= 0.5
    )

    if not template_ok:
        print(
            f"ATTENZIONE: template OCR NON salvato su disco "
            f"(motore utili={q_motore}/{len(righe_motore)} id={id_motore:.0%}, "
            f"AI utili={q_ai}/{len(righe_ai)} id={id_ai:.0%}). "
            f"Uso dati grezzi AI one-shot per QUESTO documento."
        )
        # Template AI salvato comunque nel DB
        db.save_template(nuovo_template, customer_name)
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
            template_saved=False,
            generato_da_ai=True,
            q_ai=q_ai,
            q_motore=q_motore,
            customer_name=customer_name,
        )

    saved_path = save_template(nuovo_template, TEMPLATES_DRAFT_OCR_DIR)
    db.save_template(nuovo_template, customer_name)
    print(
        f"Template OCR (draft) salvato in: {saved_path}\n"
        f"  (non e' in templates/ root: promuovi a mano dopo verifica)"
    )
    if q_motore != q_ai or len(righe_motore) != len(righe_ai):
        print(
            f"ATTENZIONE: draft OCR parziale "
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
        template_path=saved_path,
        customer_name=customer_name,
    )


def estrai_ordine(pdf_path, customer_name="UNKNOWN"):
    """
    -eo: native se c'e' testo; altrimenti OCR PDF (source=ocr_pdf).
    Ritorna solo il dict dati (con meta).
    """
    with pdfplumber.open(pdf_path) as pdf:
        lines, full_text = _collect_all_pages(pdf)

        if _has_usable_text(full_text):
            return _estrai_native(lines, full_text, source="native", customer_name=customer_name)

        print(
            "PDF senza testo nativo estraibile (probabile scansione o sola immagine). "
            "Provo OCR (immagine embedded o render pagine)..."
        )
        try:
            lines, full_text = _collect_pages_via_ocr(pdf)
        except RuntimeError as e:
            raise RuntimeError(
                f"Fallback OCR sul PDF fallito: {e}\n"
                "Installa Tesseract (https://github.com/UB-Mannheim/tesseract/wiki) "
                "con lingue Italian+English, oppure usa: python main.py -eoi <img>"
            ) from e

        if not _has_usable_text(full_text):
            raise RuntimeError(
                "OCR non ha estratto testo utile dal PDF. "
                "Verifica qualita' della scansione e installazione Tesseract "
                "(tesseract --version) con lingue ita/eng."
            )

        return _estrai_ocr(lines, full_text, source="ocr_pdf", customer_name=customer_name)


def estrai_ordine_immagini(image_paths, customer_name="UNKNOWN"):
    """-eoi: OCR multipagina, source=ocr_image."""
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
    return _estrai_ocr(lines, full_text, source="ocr_image", customer_name=customer_name)


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
    print("  python main.py -customer \"Nome Cliente\" -eo <path_pdf>")
    print("  python main.py -customer \"Nome Cliente\" -eoi <img1> [img2 ...]")
    print()
    print("Opzioni:")
    print("  -customer  Nome cliente (obbligatorio, salvato in JSON e DB template)")
    print("  -eo        PDF: testo nativo (template riusabile) oppure OCR se scansione")
    print("  -eoi       Immagini/foto (OCR, prompt dedicato, template solo se gate ok)")


def _save_json(dati, stem_source):
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    json_output = output_dir / (Path(stem_source).stem + "_estratto.json")
    with open(json_output, "w", encoding="utf-8") as f:
        json.dump(dati, f, indent=2, ensure_ascii=False)
    print(f"\n\nJSON salvato in: {json_output}")
    if dati.get("generato_da_ai"):
        if dati.get("extraction_mode") == "ai_oneshot":
            print(
                "NOTA: estrazione AI one-shot (nessun template riusabile salvato in root). "
                "Verifica i dati prima della produzione."
            )
        else:
            print(
                "NOTA: template generato dall'AI. Verifica i dati e il file template "
                "prima della produzione."
            )


if __name__ == "__main__":
    if len(sys.argv) < 5:
        _print_usage()
        sys.exit(1)

    # Parsing: -customer "Nome" -eo <pdf>  oppure  -customer "Nome" -eoi <img1> [img2...]
    args = sys.argv[1:]
    customer_name = None
    comando = None
    comando_args = []

    i = 0
    while i < len(args):
        if args[i] == "-customer":
            if i + 1 >= len(args):
                print("Errore: -customer richiede un valore")
                _print_usage()
                sys.exit(1)
            customer_name = args[i + 1]
            i += 2
        elif args[i] in ("-eo", "-eoi"):
            comando = args[i]
            comando_args = args[i + 1:]
            break
        else:
            print(f"Errore: parametro sconosciuto '{args[i]}'")
            _print_usage()
            sys.exit(1)

    if not customer_name:
        print("Errore: -customer obbligatorio")
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
            dati = estrai_ordine(pdf_file, customer_name=customer_name)
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
        _save_json(dati, pdf_file)
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
            dati = estrai_ordine_immagini(comando_args, customer_name=customer_name)
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
        _save_json(dati, comando_args[0])
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