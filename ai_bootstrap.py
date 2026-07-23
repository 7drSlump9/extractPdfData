"""
Bootstrap di un nuovo template tramite AI (OpenRouter).

Quando un PDF non corrisponde a nessun template salvato, questo modulo manda
il layout del documento (testo + posizione x/y di ogni parola) a un modello
via OpenRouter, chiedendogli di dedurre lo schema del template (vedi
template_engine.py per il formato) e di estrarre subito i dati per quel
documento.

Il template restituito viene salvato in templates/, cosi' i documenti futuri
dello stesso formato vengono riconosciuti senza bisogno di richiamare l'AI.

Configurazione (file .env nella cartella del progetto, oppure variabili
d'ambiente):
    OPENROUTER_API_KEY   (obbligatoria)
    OPENROUTER_MODEL     (opzionale, default: anthropic/claude-sonnet-4.5)
"""

import json
import os
import re
from pathlib import Path

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-sonnet-5"

SCHEMA_EXPLANATION = """
Devi produrre un TEMPLATE in JSON con questa struttura esatta:

{
  "name": "NOME_BREVE_MAIUSCOLO",
  "description": "descrizione libera del layout",
  "signature": ["stringa univoca 1", "stringa univoca 2"],
  "header_fields": {
    "numero_ordine": { <regola di estrazione> },
    "data": { <regola di estrazione> },
    "partita_iva_cliente": { <regola di estrazione> }
  },
  "table": {
    "start_after_contains": ["parole", "che", "compaiono", "nella", "riga", "di", "intestazione", "della", "tabella"],
    "end_markers": ["stringhe che segnano la fine della tabella (es. TOTALE)"],
    "row_detect_pattern": "regex Python che riconosce l'inizio di una nuova riga articolo",
    "skip_line_if_matches": "(opzionale) regex: se una riga la soddisfa, interrompi la lettura tabella",
    "columns": [
      {"name": "nome_campo", "x_min": 0, "x_max": 100, "value": "first_word|joined_text|numeric|unit_prefix"}
    ],
    "continuation_join_field": "(opzionale) nome campo dove accumulare le righe extra sotto la riga principale come testo grezzo",
    "continuation_field_extract": {"name": "campo", "pattern": "regex con un gruppo di cattura da cercare nelle righe extra"}
  }
}

Le "signature" devono essere stringhe che identificano IN MODO UNIVOCO questo layout
(nome del cliente/mittente, diciture fisse specifiche di questo modulo) - NON usare
parole generiche come "Ordine" o "Descrizione" che potrebbero comparire in altri layout.

Regole di estrazione per header_fields (scegli il tipo adatto):
- {"type": "regex_full_text", "pattern": "...(gruppo)...", "group": 1}
  cerca nel testo intero del documento.
- {"type": "regex_column_filtered", "pattern": "...", "x_min": 0, "x_max": 300, "group": 1}
  cerca riga per riga, ma considerando solo le parole con coordinata x0 nel range indicato
  (utile quando lo stesso testo, es. "Partita IVA", compare piu' volte in punti diversi
  della pagina e serve isolare la colonna giusta).
- {"type": "label_then_value_below", "label_pattern": "...", "value_pattern": "...(gruppi)...", "group": 1, "lookahead_lines": 5}
  usa quando l'etichetta (es. "NUMERO DATA") e il valore effettivo sono su righe diverse.

I "value" delle colonne della tabella:
- "first_word": prende la prima parola trovata nel range di colonna
- "joined_text": unisce tutte le parole trovate nel range con uno spazio
- "numeric": prende la prima parola nel range che sia un numero (cifre, punti, virgole)
- "unit_prefix": prende la prima parola nel range che NON sia un numero (es. unita' di misura CT/KG/NU)

IMPORTANTE su x_min/x_max delle colonne: nei documenti generati da sistemi legacy i valori
numerici sono spesso allineati a destra nella colonna, quindi la loro x0 (inizio parola)
puo' cadere PRIMA della x dell'etichetta di intestazione della colonna nel testo. Guarda
sempre le coordinate REALI delle parole nella tabella fornita sotto, non solo la posizione
delle etichette di intestazione, per calibrare i confini.

Rispondi SOLO con un oggetto JSON valido (nessun testo prima o dopo), con questa forma:
{
  "template": { ...come sopra... },
  "dati": {
    "numero_ordine": "...", "data": "...", "partita_iva_cliente": "...",
    "righe": [ {..un oggetto per ogni riga articolo, con gli stessi nomi di campo delle colonne del template..} ]
  }
}
"""


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


def bootstrap_new_template(lines, full_text):
    """Chiama il modello via OpenRouter per dedurre un nuovo template dal
    layout del documento. Ritorna (dati_estratti, template_dict)."""
    _load_dotenv(Path(__file__).parent / ".env")

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Variabile d'ambiente OPENROUTER_API_KEY non impostata (e nessun file .env "
            "trovato accanto allo script).\n"
            "Imposta la tua API key OpenRouter per abilitare il bootstrap automatico di nuovi "
            "formati, oppure crea manualmente un template in templates/ seguendo lo schema "
            "descritto in ai_bootstrap.py::SCHEMA_EXPLANATION."
        )

    model = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
    layout_dump = _build_layout_dump(lines, full_text)

    user_prompt = (
        "Il documento seguente e' un ordine cliente in PDF il cui layout non corrisponde a "
        "nessun template gia' noto. Analizza testo e coordinate e genera il template JSON "
        "richiesto, insieme ai dati estratti per QUESTO documento.\n\n"
        + SCHEMA_EXPLANATION
        + "\n\n"
        + layout_dump
    )

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

    with open(path, "w", encoding="utf-8") as f:
        json.dump(template, f, indent=2, ensure_ascii=False)

    return path
