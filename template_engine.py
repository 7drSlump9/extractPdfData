"""
Motore generico di estrazione basato su template JSON.

Un template descrive UN layout di documento (un cliente/generatore) tramite:
- signature: stringhe che devono comparire nel testo per riconoscere il template
- header_fields: regole per estrarre numero ordine / data / partita IVA ecc.
- table: regole per riconoscere l'inizio/fine tabella e spezzare le righe in colonne

Vedi templates/*.json per esempi completi.
"""

import re
import json
from pathlib import Path
from collections import defaultdict

NUMERIC_RE = re.compile(r'^[\d\.,]+$')


# ---------------------------------------------------------------------------
# Utility di basso livello sul layout del PDF
# ---------------------------------------------------------------------------
def get_lines(page):
    """Raggruppa le parole della pagina in righe per coordinata Y (arrotondata)."""
    words = page.extract_words()
    lines = defaultdict(list)
    for w in words:
        key = round(w['top'], 0)
        lines[key].append(w)
    result = []
    for top in sorted(lines.keys()):
        row = sorted(lines[top], key=lambda w: w['x0'])
        result.append((top, row))
    return result


def line_text(row):
    return " ".join(w['text'] for w in row)


def words_in_range(row, x_min, x_max):
    # Le coordinate PDF sono float e possono avere rumore sub-pixel (es. una
    # parola disegnata a x=292.999999... invece di 293.0 esatto): confrontare
    # i bound di colonna (scelti guardando coordinate arrotondate) contro il
    # float grezzo puo' escludere per un pelo la parola giusta. Arrotondiamo.
    return [w['text'] for w in row if x_min <= round(w['x0']) < x_max]


# ---------------------------------------------------------------------------
# Template store: caricamento e matching per firma testuale
# ---------------------------------------------------------------------------
def load_templates(templates_dir):
    templates = []
    for path in sorted(Path(templates_dir).glob("*.json")):
        with open(path, encoding='utf-8') as f:
            templates.append(json.load(f))
    return templates


def match_template(templates, full_text):
    """Ritorna il template la cui 'signature' e' interamente contenuta nel testo.
    Se piu' template combaciano, vince quello con la firma piu' specifica."""
    text_upper = full_text.upper()
    candidates = []
    for tpl in templates:
        sig = tpl.get('signature', [])
        if sig and all(s.upper() in text_upper for s in sig):
            candidates.append(tpl)
    if not candidates:
        return None
    candidates.sort(
        key=lambda t: (len(t.get('signature', [])), sum(len(s) for s in t.get('signature', []))),
        reverse=True,
    )
    return candidates[0]


# ---------------------------------------------------------------------------
# Estrazione campi di intestazione
# ---------------------------------------------------------------------------
def extract_header_field(config, full_text, lines):
    ftype = config.get('type', 'regex_full_text')
    group = config.get('group', 1)

    if ftype == 'regex_full_text':
        m = re.search(config['pattern'], full_text)
        return m.group(group) if m else "N/A"

    if ftype == 'regex_column_filtered':
        x_min = config.get('x_min', 0)
        x_max = config.get('x_max', 9999)
        for _, row in lines:
            text = " ".join(w['text'] for w in row if x_min <= round(w['x0']) < x_max)
            m = re.search(config['pattern'], text)
            if m:
                return m.group(group)
        return "N/A"

    if ftype == 'label_then_value_below':
        label_re = re.compile(config['label_pattern'])
        value_re = re.compile(config['value_pattern'])
        lookahead = config.get('lookahead_lines', 5)
        for i, (_, row) in enumerate(lines):
            text = line_text(row).strip()
            if label_re.search(text):
                for _, next_row in lines[i + 1:i + 1 + lookahead]:
                    next_text = line_text(next_row).strip()
                    m = value_re.match(next_text)
                    if m:
                        return m.group(group)
                break
        return "N/A"

    return "N/A"


# ---------------------------------------------------------------------------
# Estrazione tabella righe
# ---------------------------------------------------------------------------
def extract_column_value(tokens, value_type):
    if value_type == "first_word":
        return tokens[0] if tokens else "N/A"
    if value_type == "joined_text":
        return " ".join(tokens) if tokens else "N/A"
    if value_type == "numeric":
        for t in tokens:
            if NUMERIC_RE.match(t):
                return t
        return "N/A"
    if value_type == "unit_prefix":
        for t in tokens:
            if not NUMERIC_RE.match(t):
                return t
        return ""
    return " ".join(tokens) if tokens else "N/A"


def extract_table(table_config, lines):
    if not table_config:
        return []

    columns = table_config['columns']
    row_pattern = re.compile(table_config['row_detect_pattern'])
    skip_pattern = re.compile(table_config['skip_line_if_matches']) if table_config.get('skip_line_if_matches') else None
    start_markers = table_config.get('start_after_contains', [])
    end_markers = [m.upper() for m in table_config.get('end_markers', [])]

    rows = []
    current_tokens = None
    current_continuation = []
    in_table = False

    def flush():
        nonlocal current_tokens, current_continuation
        if current_tokens is None:
            return
        row = {}
        for col in columns:
            toks = current_tokens.get(col['name'], [])
            row[col['name']] = extract_column_value(toks, col['value'])

        # continuation_join_field prima, continuation_field_extract dopo: se un
        # template (es. generato dall'AI) punta entrambi allo stesso nome campo
        # per errore, l'estrazione mirata deve vincere sul semplice testo grezzo.
        cjf = table_config.get('continuation_join_field')
        if cjf:
            row[cjf] = " | ".join(current_continuation)

        cfe = table_config.get('continuation_field_extract')
        if cfe:
            joined_cont = " ".join(current_continuation)
            m = re.search(cfe['pattern'], joined_cont)
            row[cfe['name']] = m.group(1) if m else "N/A"

        rows.append(row)
        current_tokens = None
        current_continuation = []

    for _, row_words in lines:
        text = line_text(row_words)
        text_upper = text.upper()

        if not in_table:
            if start_markers and all(marker.upper() in text_upper for marker in start_markers):
                in_table = True
            continue

        if end_markers and any(marker in text_upper for marker in end_markers):
            break
        if skip_pattern and skip_pattern.match(text.strip()):
            continue

        if row_pattern.match(text):
            flush()
            current_tokens = {
                col['name']: words_in_range(row_words, col['x_min'], col['x_max'])
                for col in columns
            }
        else:
            if current_tokens is not None:
                current_continuation.append(text)

    flush()
    return rows


# ---------------------------------------------------------------------------
# Applicazione completa di un template a un documento
# ---------------------------------------------------------------------------
def apply_template(template, lines, full_text):
    result = {"formato": template.get('name', 'UNKNOWN')}
    for field_name, cfg in template.get('header_fields', {}).items():
        result[field_name] = extract_header_field(cfg, full_text, lines)
    result['righe'] = extract_table(template.get('table'), lines)
    return result
