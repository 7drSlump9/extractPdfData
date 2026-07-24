#!/usr/bin/env python3
"""
Estrattore dati ordini cliente da PDF o immagini - basato su template.

Ogni formato di documento (un cliente/generatore) e' descritto da un template
JSON in templates/ (vedi template_engine.py per lo schema). Quando un documento
non corrisponde a nessun template noto, viene interpellata un'AI (Claude) che
deduce un nuovo template dal layout; il template viene salvato, cosi' i
documenti futuri dello stesso formato non richiedono piu' l'AI.

Uso:
    python main.py -eo <path_pdf>
    python main.py -eoi <img1> [img2 ...]
"""

import json
import sys
from pathlib import Path

import pdfplumber

from template_engine import get_lines, line_text, load_templates, match_template, apply_template

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _collect_all_pages(pdf):
    """Lines + full_text da tutte le pagine (Y offset cumulativo)."""
    all_lines = []
    y_offset = 0.0
    text_parts = []
    for page in pdf.pages:
        # dedupe_chars: alcuni PDF (report legacy) disegnano due volte lo stesso
        # carattere con un micro-offset per simulare il grassetto (bold-by-double-strike).
        # Senza deduplica, ogni cifra/lettera interessata viene letta due volte
        # (es. "1468451" letto come "11446688445511").
        page = page.dedupe_chars(tolerance=1)
        page_lines = get_lines(page)
        for top, row in page_lines:
            all_lines.append((top + y_offset, row))
        text_parts.append("\n".join(line_text(row) for _, row in page_lines))
        y_offset += float(page.height or 0) + 10.0
    return all_lines, "\n".join(text_parts)


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
        return apply_template(template, lines, full_text), False
    return None


def _bootstrap_ai(lines, full_text):
    print("Formato non riconosciuto da nessun template esistente.")
    print("Interpello l'AI per dedurre un nuovo template...")
    from ai_bootstrap import bootstrap_new_template, save_template

    dati_ai, nuovo_template = bootstrap_new_template(lines, full_text)
    return dati_ai, nuovo_template, save_template


def _estrai_da_layout_pdf(lines, full_text):
    """Path -eo: comportamento originale. Template AI SEMPRE salvato."""
    matched = _match_or_none(lines, full_text)
    if matched is not None:
        return matched

    dati_ai, nuovo_template, save_template = _bootstrap_ai(lines, full_text)
    saved_path = save_template(nuovo_template, TEMPLATES_DIR)
    print(f"Nuovo template salvato in: {saved_path}")

    # IMPORTANTE: non ci fidiamo dei 'dati' restituiti direttamente dall'AI
    # (sono una lettura una tantum del modello, non provano che il template
    # generato funzioni). Riapplichiamo subito il template appena salvato
    # con lo stesso motore deterministico usato per tutti i documenti futuri,
    # cosi' un template rotto viene scoperto immediatamente, non alla prossima
    # fattura dello stesso cliente.
    dati = apply_template(nuovo_template, lines, full_text)

    righe_ai = len(dati_ai.get("righe", []))
    righe_motore = len(dati.get("righe", []))
    if righe_motore == 0 or righe_motore != righe_ai:
        print(
            f"ATTENZIONE: il template generato non e' affidabile "
            f"(l'AI aveva estratto {righe_ai} righe, il motore deterministico "
            f"ne estrae {righe_motore} riapplicando lo stesso template salvato). "
            f"Controlla e correggi manualmente {saved_path} prima di usarlo in produzione."
        )
    return dati, True


def _estrai_da_layout_image(lines, full_text):
    """Path -eoi: quality-gate. Non salva template se motore non riproduce le righe."""
    matched = _match_or_none(lines, full_text)
    if matched is not None:
        return matched

    dati_ai, nuovo_template, save_template = _bootstrap_ai(lines, full_text)
    dati = apply_template(nuovo_template, lines, full_text)

    righe_ai = dati_ai.get("righe", []) or []
    righe_motore = dati.get("righe", []) or []
    q_ai = _righe_quality(righe_ai)
    q_motore = _righe_quality(righe_motore)

    template_ok = q_motore > 0 and q_motore >= max(1, int(0.6 * max(q_ai, 1)))

    if not template_ok:
        print(
            f"ATTENZIONE: template AI NON salvato "
            f"(qualita' motore={q_motore}/{len(righe_motore)} righe utili, "
            f"AI={q_ai}/{len(righe_ai)}). "
            f"Uso i dati grezzi dell'AI solo per QUESTO documento."
        )
        if q_ai >= q_motore and q_ai > 0:
            return _pack_ai_dati(dati_ai, nuovo_template), True
        if q_motore > 0:
            return dati, True
        return _pack_ai_dati(dati_ai, nuovo_template), True

    saved_path = save_template(nuovo_template, TEMPLATES_DIR)
    print(f"Nuovo template salvato in: {saved_path}")

    if q_motore != q_ai or len(righe_motore) != len(righe_ai):
        print(
            f"ATTENZIONE: template parzialmente affidabile "
            f"(motore {q_motore}/{len(righe_motore)} utili, "
            f"AI {q_ai}/{len(righe_ai)}). "
            f"Controlla {saved_path} prima della produzione."
        )
    return dati, True


def estrai_ordine(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        lines, full_text = _collect_all_pages(pdf)
        return _estrai_da_layout_pdf(lines, full_text)


def estrai_ordine_immagini(image_paths):
    """OCR multipagina (N immagini = N pagine), poi path -eoi con quality-gate."""
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
    return _estrai_da_layout_image(lines, full_text)


def stampa_risultati(dati):
    print("\n" + "=" * 80)
    print(f"ORDINE ESTRATTO - Formato: {dati.get('formato', 'N/A')}")
    print("=" * 80)
    for key, value in dati.items():
        if key in ("formato", "righe"):
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
    print("  python main.py -eo <path_pdf>")
    print("  python main.py -eoi <img1> [img2 ...]")
    print()
    print("Comandi:")
    print("  -eo     Estrai dati ordine da PDF")
    print("  -eoi    Estrai dati ordine da una o piu' immagini (OCR, ordine = pagine)")


def _save_json(dati, stem_source, generato_da_ai):
    json_output = Path(stem_source).stem + "_estratto.json"
    with open(json_output, "w", encoding="utf-8") as f:
        json.dump(dati, f, indent=2, ensure_ascii=False)
    print(f"\n\nJSON salvato in: {json_output}")
    if generato_da_ai:
        print(
            "NOTA: questo documento e' stato interpretato con un template appena "
            "generato dall'AI. Verifica i dati estratti prima di usarli in produzione."
        )


if __name__ == "__main__":
    if len(sys.argv) < 3:
        _print_usage()
        sys.exit(1)

    comando = sys.argv[1]

    if comando == "-eo":
        pdf_file = sys.argv[2]
        if not Path(pdf_file).exists():
            print(f"Errore: file '{pdf_file}' non trovato")
            sys.exit(1)

        print(f"Elaborazione: {pdf_file}")
        dati, generato_da_ai = estrai_ordine(pdf_file)
        stampa_risultati(dati)
        _save_json(dati, pdf_file, generato_da_ai)

    elif comando == "-eoi":
        image_files = sys.argv[2:]
        if not image_files:
            print("Errore: specifica almeno un'immagine")
            _print_usage()
            sys.exit(1)

        print(f"Elaborazione immagini: {', '.join(image_files)}")
        try:
            dati, generato_da_ai = estrai_ordine_immagini(image_files)
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            print(f"Errore: {e}")
            sys.exit(1)

        stampa_risultati(dati)
        _save_json(dati, image_files[0], generato_da_ai)

    else:
        print(f"Errore: comando sconosciuto '{comando}'")
        _print_usage()
        sys.exit(1)
