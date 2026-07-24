"""
Bootstrap di un nuovo template tramite AI (OpenRouter).

Due modalita' distinte (stesso schema motore, prompt e priorita' diverse):

- mode="native": PDF con testo estraibile (coordinate stabili). Obiettivo =
  template riusabile e deterministico.
- mode="ocr": layout da OCR (foto o PDF scansionato). Obiettivo = dati utili
  per QUESTO documento; template solo se il chiamante decide di salvarlo
  (quality-gate stretto). Tolleranza a rumore OCR e layout side-by-side.

Configurazione (file .env nella cartella del progetto, oppure variabili
d'ambiente):
    OPENROUTER_API_KEY   (obbligatoria)
    OPENROUTER_MODEL     (opzionale, default: anthropic/claude-sonnet-5)
"""

import json
import os
import re
from pathlib import Path

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-sonnet-5"
#DEFAULT_MODEL = "google/gemini-2.5-pro"

# ---------------------------------------------------------------------------
# Schema comune (motore template_engine)
# ---------------------------------------------------------------------------
_SCHEMA_CORE = """
Devi produrre un TEMPLATE in JSON con questa struttura:

{
  "name": "NOME_BREVE_MAIUSCOLO",
  "description": "descrizione libera del layout",
  "signature": ["stringa univoca 1", "stringa univoca 2"],
  "header_fields": {
    "numero_ordine": { <regola> },
    "data": { <regola> },
    "partita_iva_cliente": { <regola> }
  },
  "table": { <FORM A rows OPPURE FORM B side_by_side_items> }
}

FORM A - tabella classica (una riga orizzontale = un articolo), default:
{
  "layout": "rows",
  "start_after_contains": ["parole header tabella"],
  "end_markers": ["TOTALE"],
  "row_detect_pattern": "regex inizio riga articolo (re.match sull'intera riga)",
  "skip_line_if_matches": "(opzionale) regex da saltare",
  "columns": [
    {"name": "nome_campo", "x_min": 0, "x_max": 100, "value": "first_word|joined_text|numeric|unit_prefix"}
  ],
  "continuation_join_field": "(opzionale)",
  "continuation_field_extract": {"name": "campo", "pattern": "regex con un gruppo"}
}

FORM B - articoli AFFIANCATI (N colonne verticali = N prodotti sulla stessa Y):
usa se sulla STESSA Y compaiono N valori a X diverse (es. "NR NR NR", tre quantita').
NON unire le colonne in un solo articolo.
{
  "layout": "side_by_side_items",
  "start_after_contains": [],
  "end_markers": [],
  "item_x_bands": [
    {"x_min": 2300, "x_max": 2365},
    {"x_min": 2365, "x_max": 2415}
  ],
  "fields": [
    {"name": "codice_articolo", "mode": "first_line", "skip_if_match": "^(NR|\\\\d)"},
    {"name": "descrizione", "mode": "join_lines", "until_match": "^(NR|\\\\d)", "max_lines": 4},
    {"name": "quantita", "mode": "nth_regex", "pattern": "^[\\\\d.,]+$", "n": 0},
    {"name": "prezzo_unitario", "mode": "nth_regex", "pattern": "^[\\\\d.,]+$", "n": 1},
    {"name": "totale_riga", "mode": "nth_regex", "pattern": "^[\\\\d.,]+$", "n": 2}
  ]
}

Regole header_fields:
- {"type": "regex_full_text", "pattern": "...(gruppo)...", "group": 1}
- {"type": "regex_column_filtered", "pattern": "...", "x_min": 0, "x_max": 300, "group": 1}
- {"type": "label_then_value_below", "label_pattern": "...", "value_pattern": "...", "group": 1, "lookahead_lines": 5}

value colonne (FORM A): first_word | joined_text | numeric | unit_prefix

modes fields (FORM B): first_line | join_lines (+ until_match/skip_if_match/max_lines)
  | nth_regex (n 0-based sui token che matchano pattern, alto->basso)
  | nth_line_regex
"""

SCHEMA_NATIVE = _SCHEMA_CORE + """
=== CONTESTO: PDF NATIVO (testo digitale, coordinate AFFIDABILI) ===
Obiettivo principale: un TEMPLATE RIUSABILE e preciso per documenti futuri
dello stesso layout. I "dati" servono a validare il template.

Priorita':
1. signature UNIVOCHE (ragione sociale, codici modulo, diciture fisse). NO generiche.
2. Calibra x_min/x_max sulle coordinate REALI @xNNN delle celle articolo, non solo header.
3. Preferisci layout "rows" se ogni articolo e' una riga orizzontale.
4. Usa side_by_side_items SOLO se e' evidente (N valori ripetuti stessa Y).
5. Ogni riga articolo deve avere i campi presenti nel documento: tipicamente
   codice_articolo e/o descrizione, quantita, prezzo se esistono. Non omettere
   colonne utili solo per semplificare.
6. row_detect_pattern deve matchare l'INTERA riga (re.match), es. "^\\\\d{5,}\\\\s".
7. start_after_contains: 1-3 marker stabili PRIMA della prima riga articolo.
8. Nei documenti legacy i numeri sono spesso right-aligned: x0 del valore puo'
   essere PRIMA dell'etichetta colonna — usa le x delle parole valore.

Rispondi SOLO con JSON valido:
{
  "template": { ... },
  "dati": {
    "numero_ordine": "...", "data": "...", "partita_iva_cliente": "...",
    "righe": [ { ... un oggetto per ogni articolo, stessi nomi campi del template ... } ]
  }
}
"""

SCHEMA_OCR = _SCHEMA_CORE + """
=== CONTESTO: LAYOUT DA OCR (foto o PDF scansionato / PDF sola immagine) ===
Il testo e' IMPERFETTO: parole spezzate, Y non allineate, caratteri errati,
marker header su righe diverse. Le coordinate @x/@y restano la guida migliore.

Obiettivo principale: DATI CORRETTI PER QUESTO DOCUMENTO.
- Conservativo sui VALORI: se una cella non e' leggibile usa "N/A" — NON inventare
  (niente zeri o prezzi finti).
- Completo sullo SCHEMA della tabella: vedi sotto (header = contratto campi).

--------------------------------------------------------------------
HEADER TABELLA = CONTRATTO DEI CAMPI (obbligatorio se c'e' una griglia)
--------------------------------------------------------------------
Nella zona tabellare c'e' quasi sempre una RIGA DI INTESTAZIONE COLONNE
(es. etichette tipo No. / Description / Quantity / Unit / Unit Price / Amount,
oppure equivalenti IT/DE spezzate dall'OCR su 1-3 righe Y vicine).

Quell'intestazione NON e' decorativa: DEFINISCE lo schema della tabella
per QUESTO documento (e per altri fogli dello stesso layout):
  N etichette header  =>  N campi su OGNI riga articolo.

--------------------------------------------------------------------
CONTEGGIO COLONNE DINAMICO (OBBLIGATORIO)
--------------------------------------------------------------------
Il numero di colonne NON e' fisso e NON va assunto a priori.
Devi SCOPRIRLO dall'immagine/OCR di QUESTO documento:

1. CONTA le etichette REALI dell'header tabella (sinistra -> destra).
   Quel conteggio e' N. N puo' essere 3, 5, 9, ... qualsiasi.
2. NOME di ogni chiave = nome colonna header, normalizzato in snake_case.
   Usa SOLO le etichette presenti. NON inventare colonne. NON fondere due
   etichette in una. NON togliere colonne "scomode" (testo, date, codici).
3. table.columns (layout "rows") OPPURE table.fields (side_by_side) deve avere
   ESATTAMENTE N elementi, stesso ordine visuale dell'header.
4. Ogni oggetto in dati.righe deve avere ESATTAMENTE quelle N chiavi,
   STESSI nomi di columns[].name / fields[].name, stesso ordine logico.
5. Cella vuota o illeggibile -> valore "N/A". La chiave resta SEMPRE presente.
6. table_header_detected = lista delle N etichette (come lette / normalizzate)
   e deve combaciare 1:1 con columns[].name (o fields[].name) e con le chiavi
   di ogni riga in dati.righe.

Esempio mentale (N=4, nomi inventati solo per illustrare la forma):
  header letto: ["Pos", "Descrizione", "Qta", "Importo"]
  => columns: 4 campi (pos, descrizione, qta, importo)
  => ogni riga: {"pos":"...", "descrizione":"...", "qta":"...", "importo":"..."}
  Se Qta illeggibile: "qta":"N/A" (chiave presente).

Procedura OBBLIGATORIA prima di estrarre le righe:
1. HEADER DISCOVERY: individua il cluster di etichette colonna (anche se OCR
   le spezza su piu' righe Y: ricomponile ordinate per X). CONTA N.
2. SCHEMA LOCK: elenca le N colonne nell'ordine visuale (sinistra->destra).
   Normalizza i nomi in snake_case stabili (es. Description->descrizione,
   Unit Price->prezzo_unitario, Amount->totale_riga, No.->numero_riga).
   Esempio di mapping (USA le etichette REALI del documento, non questo elenco fisso):
   No.|Pos -> numero_riga; Description|Descrizione -> descrizione;
   Performance Date|Data -> data_prestazione; Quantity|Q.ta -> quantita;
   Unit|UM -> unita_misura; Unit Price|Prezzo -> prezzo_unitario;
   Disc.|Sconto -> sconto; VAT|IVA -> iva; Amount|Importo -> totale_riga.
3. GEOMETRY: per ogni colonna header calibra x_min/x_max sulle parole DEI VALORI
   sotto quella colonna (non solo sulla x dell'etichetta; attenzione al right-align).
4. ROW PARSE: ogni riga articolo deve avere LE STESSE N chiavi dello schema header.
   Chiave senza valore leggibile -> "N/A", NON omettere la chiave.
5. EMIT: in table.columns (layout rows) oppure table.fields, ESATTAMENTE N campi
   = N colonne header. Vietato ridurre la tabella a sole colonne numeriche se
   l'header include anche Description/No./Date/ecc. Vietato aggiungere campi
   assenti dall'header.

start_after_contains: 1-3 token STABILI presi dall'header (es. "Description",
"Quantity"). Il motore li accumula anche su righe OCR diverse.

end_markers: SOLO marker SOTTO la griglia (totali documento, pagamento, banca).
MAI usare come end_marker le etichette dell'header (Amount, Quantity, Total come
titolo colonna, ecc.).

--------------------------------------------------------------------
LAYOUT rows vs side_by_side_items
--------------------------------------------------------------------
- Se c'e' un header multi-colonna classico (Description + Quantity + Price + ...
  in sequenza orizzontale) => layout "rows": una riga Y (o blocco riga) = un articolo.
  NON interpretare le colonne di UNA riga tabella come N articoli affiancati.
- side_by_side_items SOLO se sulla stessa Y si ripetono N volte lo STESSO tipo di
  valore a X diverse SENZA un header multi-campo classico (es. tre "NR" / tre qty
  di tre prodotti affiancati verticalmente).

--------------------------------------------------------------------
Altre priorita'
--------------------------------------------------------------------
1. Conta gli ARTICOLI REALI (righe dati sotto l'header), non le colonne header.
2. signature: stringhe ROBUSTE (ragione sociale, IBAN/BIC lunghi, P.IVA). Evita
   pezzi OCR fragili di una sola parola.
3. Calibra columns / item_x_bands sulle @x REALI OCR.
4. In "dati"."righe": un oggetto per articolo, chiavi = schema header completo.
5. Se l'OCR e' confuso sui valori, genera comunque schema header completo + dati
   con "N/A" dove serve. Non sacrificare colonne header per "semplificare".

Rispondi SOLO con JSON valido:
{
  "template": { ... },
  "table_header_detected": ["etichetta1", "etichetta2", "... come lette nell'header"],
  "dati": {
    "numero_ordine": "...", "data": "...", "partita_iva_cliente": "...",
    "righe": [ { ... stesse chiavi dello schema header, una riga per articolo ... } ]
  }
}
(table_header_detected e' obbligatorio se hai trovato un header tabellare;
 deve allinearsi ai name di columns/fields del template.)
"""


# Retrocompatibilita' nome usato in messaggi d'errore
SCHEMA_EXPLANATION = SCHEMA_NATIVE


def _load_dotenv(env_path):
    """Parser minimale di file .env (KEY=VALUE per riga), senza dipendenze extra."""
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def _build_layout_dump(lines, full_text):
    parts = [f"=== TESTO COMPLETO ===\n{full_text}\n\n=== PAROLE CON COORDINATE (x0, top) ===\n"]
    for top, row in lines:
        row_repr = " | ".join(f"[{w['text']}@x{w['x0']:.0f}]" for w in row)
        parts.append(f"y={top:.0f}: {row_repr}")
    return "\n".join(parts)


def _extract_json_object(text):
    """Estrae il primo blocco JSON valido dalla risposta del modello,
    tollerando eventuali fence ```json ... ``` attorno."""
    fence_match = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.DOTALL)
    candidate = fence_match.group(1) if fence_match else text.strip()
    return json.loads(candidate)


def _normalize_mode(mode):
    m = (mode or "native").strip().lower()
    if m in ("native", "pdf", "text"):
        return "native"
    if m in ("ocr", "image", "scan", "ocr_pdf", "ocr_image"):
        return "ocr"
    raise ValueError(f"mode AI sconosciuto: {mode!r} (usa 'native' o 'ocr')")


def bootstrap_new_template(lines, full_text, mode="native"):
    """
    Chiama OpenRouter per dedurre template + dati.

    mode:
      - "native": PDF testo digitale (prompt SCHEMA_NATIVE)
      - "ocr": foto / PDF scansionato (prompt SCHEMA_OCR)

    Ritorna (dati_estratti, template_dict).
    """
    mode = _normalize_mode(mode)
    _load_dotenv(Path(__file__).parent / ".env")

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Variabile d'ambiente OPENROUTER_API_KEY non impostata (e nessun file .env "
            "trovato accanto allo script).\n"
            "Imposta la tua API key OpenRouter per abilitare il bootstrap automatico di nuovi "
            "formati, oppure crea manualmente un template in templates/ seguendo lo schema "
            "descritto in ai_bootstrap.py (SCHEMA_NATIVE / SCHEMA_OCR)."
        )

    model = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
    layout_dump = _build_layout_dump(lines, full_text)
    schema = SCHEMA_NATIVE if mode == "native" else SCHEMA_OCR

    if mode == "native":
        intro = (
            "Il documento seguente e' un ORDINE/DOCUMENTO da PDF NATIVO (testo digitale). "
            "Il layout non corrisponde a nessun template noto. Analizza testo e coordinate "
            "AFFIDABILI e genera un template RIUSABILE piu' i dati di questo documento.\n\n"
        )
    else:
        intro = (
            "Il documento seguente deriva da OCR (foto oppure PDF scansionato/sola immagine). "
            "Il testo puo' essere rumoroso. Prima individua l'INTESTAZIONE della tabella "
            "(etichette colonna): quello e' lo schema campi. Poi estrai i dati di QUESTO "
            "documento allineati a quello schema. Conservativo sui valori (N/A se illeggibili, "
            "non inventare); completo sulle colonne header. Non fondere colonne di una riga "
            "tabella in un unico articolo.\n\n"
        )


    user_prompt = intro + schema + "\n\n" + layout_dump

    response = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": user_prompt}],
        },
        timeout=120,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Chiamata OpenRouter fallita (HTTP {response.status_code}): {response.text[:2000]}"
        )

    payload = response.json()
    try:
        response_text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Risposta OpenRouter in formato inatteso: {payload}") from e

    try:
        obj = _extract_json_object(response_text)
    except (json.JSONDecodeError, AttributeError) as e:
        raise RuntimeError(
            f"La risposta dell'AI non contiene un JSON valido: {e}\n"
            f"Risposta grezza:\n{response_text}"
        )

    template = obj.get("template")
    dati = obj.get("dati")
    if not template or not dati:
        raise RuntimeError(f"Risposta AI incompleta (manca 'template' o 'dati'): {obj}")

    return dati, template


def save_template(template, templates_dir):
    name = template.get("name", "template_sconosciuto")
    slug = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_') or "template_sconosciuto"
    path = Path(templates_dir) / f"{slug}.json"

    # evita di sovrascrivere un template esistente con lo stesso nome
    counter = 1
    original_path = path
    while path.exists():
        counter += 1
        path = Path(templates_dir) / f"{slug}_{counter}.json"
        if counter > 20:
            path = original_path
            break

    Path(templates_dir).mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(template, f, indent=2, ensure_ascii=False)

    return path
