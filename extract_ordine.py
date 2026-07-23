#!/usr/bin/env python3
"""
Estrattore dati ordini cliente da PDF - basato su template.

Ogni formato di documento (un cliente/generatore) e' descritto da un template
JSON in templates/ (vedi template_engine.py per lo schema). Quando un PDF non
corrisponde a nessun template noto, viene interpellata un'AI (Claude) che
deduce un nuovo template dal layout del documento; il template viene salvato,
cosi' i documenti futuri dello stesso formato non richiedono piu' l'AI.

Uso:
    python extract_ordine.py <path_pdf>
"""

import json
import sys
from pathlib import Path

import pdfplumber

from template_engine import get_lines, line_text, load_templates, match_template, apply_template

TEMPLATES_DIR = Path(__file__).parent / "templates"


def estrai_ordine(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        # dedupe_chars: alcuni PDF (report legacy) disegnano due volte lo stesso
        # carattere con un micro-offset per simulare il grassetto (bold-by-double-strike).
        # Senza deduplica, ogni cifra/lettera interessata viene letta due volte
        # (es. "1468451" letto come "11446688445511").
        page = pdf.pages[0].dedupe_chars(tolerance=1)
        lines = get_lines(page)
        full_text = "\n".join(line_text(row) for _, row in lines)

        templates = load_templates(TEMPLATES_DIR)
        template = match_template(templates, full_text)

        if template:
            return apply_template(template, lines, full_text), False

        print("Formato non riconosciuto da nessun template esistente.")
        print("Interpello l'AI per dedurre un nuovo template...")
        from ai_bootstrap import bootstrap_new_template, save_template

        dati_ai, nuovo_template = bootstrap_new_template(lines, full_text)
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


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python extract_ordine.py <path_pdf>")
        sys.exit(1)

    pdf_file = sys.argv[1]
    if not Path(pdf_file).exists():
        print(f"Errore: file '{pdf_file}' non trovato")
        sys.exit(1)

    print(f"Elaborazione: {pdf_file}")
    dati, generato_da_ai = estrai_ordine(pdf_file)
    stampa_risultati(dati)

    json_output = Path(pdf_file).stem + "_estratto.json"
    with open(json_output, 'w', encoding='utf-8') as f:
        json.dump(dati, f, indent=2, ensure_ascii=False)
    print(f"\n\nJSON salvato in: {json_output}")
    if generato_da_ai:
        print("NOTA: questo documento e' stato interpretato con un template appena "
              "generato dall'AI. Verifica i dati estratti prima di usarli in produzione.")
