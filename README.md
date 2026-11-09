# extractPdfData

Estrattore dati ordini cliente da PDF e immagini basato su template JSON.

## Split PDF per barcode

`python split_pdf.py` elabora una passata di `in_pdf`: divide le pagine, chiama
`main.py` esternamente, rinomina col barcode e comprime le pagine OK in `out_pdf`.
Originali e pagine KO sono separati per job, con ripresa dopo interruzione.
Configurazione commentata inline: `split_config.jsonc`. Nessuna email inviata
in questa versione; log di testo con rotazione configurabile (default 10 MiB).
Vedere il [manuale completo](docs/MANUALE_SPLIT.md) prima dell'uso.

Due programmi, un unico motore condiviso.

---

## Architettura

```
                           ┌─────────────────────────────────────┐
                           │           CORE CONDIVISO            │
                           │  template_engine.py  motore template│
                           │  db.py              SQLite (ORM)    │
                           │  image_ocr.py       OCR / HTR       │
                           │  ai_bootstrap.py    AI bootstrap    │
                           └──────────┬────────────┬─────────────┘
                                      │            │
┌─────────────────────────────────────▼──┐  ┌──────▼─────────────────────────────┐
│           PROGRAMMA 1 (CLI)            │  │       PROGRAMMA 2 (WEB)            │
│  main.py                               │  │  server.py   Flask API             │
│                                        │  │  web/        React + Vite          │
│  Estrazione batch da terminale:        │  │                                    │
│  - PDF nativo (pdfplumber)             │  │  Editor visuale template:          │
│  - PDF scansionato (OCR Tesseract)     │  │  - Carica PDF + overlay zone       │
│  - Foto/immagini (OCR + auto-rotaz.)   │  │  - Definisci colonne X             │
│  - Bootstrap AI per layout sconosciuti │  │  - Testa estrazione in tempo reale │
└────────────────────────────────────────┘  └────────────────────────────────────┘
```

Il core **(template_engine.py)** fa tutto il lavoro pesante:
- matching firma testuale per riconoscere il template
- estrazione campi header/footer via regex
- estrazione tabella articoli per colonne X

Entrambi i programmi usano lo stesso database SQLite per persistenza template e log.

---

## Stack tecnologico

### Backend (Python 3.12+)

| Componente | Libreria | Ruolo |
|---|---|---|
| Estrazione testo PDF | `pdfplumber` | Legge PDF nativi: testo + coordinate |
| OCR base | `pytesseract` | OCR via Tesseract per PDF scansionati e immagini |
| OCR avanzato (HTR) | `easyocr` | Riconoscimento testo su zone specifiche (lazy load) |
| HTR manoscritto | `transformers` + `torch` | TrOCR per testo a mano/stampatello (lazy load) |
| Immagini | `Pillow` | Manipolazione immagini, crop, auto-rotazione |
| Database | `sqlalchemy` | ORM SQLite. Tabelle: template, config, logs |
| API server | `flask` | Backend REST per editor web |
| AI bootstrap | `requests` | Chiamate OpenRouter (Claude/Gemini) |

### Frontend (Node.js 20+)

| Componente | Libreria | Ruolo |
|---|---|---|
| Framework | `react` 18 | UI editor template |
| Bundler | `vite` 6 | Dev server + build produzione |
| PDF viewer | `pdfjs-dist` 4 | Render PDF nel browser |
| HTTP client | `axios` | Chiamate API backend |
| Proxy dev | Vite proxy | `/api` → `localhost:8000` |

### Infrastruttura esterna

| Servizio | Uso |
|---|---|
| **Tesseract OCR** | OCR base. Da installare separatamente |
| **OpenRouter** | AI per generare template da layout sconosciuti |
| **SQLite** | Database locale, zero-config |

---

## Prerequisiti

### Python

- Python 3.12+
- `pip install -r requirements.txt`

### Tesseract OCR

Installa Tesseract per Windows:
1. Scarica da [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki)
2. Durante installazione seleziona lingue **Italiano + English**
3. Aggiungi al PATH o imposta variabile `TESSERACT_CMD`

Verifica: `tesseract --version`

### Node.js (solo per editor web)

- Node.js 20+
- `cd web && npm install`

### OpenRouter (opzionale, solo AI bootstrap)

Crea file `.env` nella root del progetto:
```
OPENROUTER_API_KEY=sk-or-v1-...
```

## Uso CLI — Estrazione dati (main.py)

### PDF con testo nativo (preferito)

```bash
python main.py -customer "NomeCliente" -template "NOME_TEMPLATE" -eo documento.pdf
```

Il motore usa `pdfplumber` per estrarre testo e coordinate. Se il PDF ha <20 caratteri utili, fa automaticamente fallback OCR.

### Immagini / foto

```bash
python main.py -customer "NomeCliente" -template "NOME_TEMPLATE" -eoi foto1.jpg foto2.jpg
```

Auto-rotazione attiva. Supporta: `.png .jpg .jpeg .webp .tif .tiff .bmp`

### Opzioni

| Opzione | Descrizione |
|---|---|
| `-customer` | Nome cliente (obbligatorio) |
| `-template` | Nome template da usare (obbligatorio) |
| `-eo` | Estrai da PDF |
| `-eoi` | Estrai da immagini |
| `-outputTemplatePath` | Percorso output JSON personalizzato |
| `-append` | Appende al file JSON esistente invece di sovrascrivere |

Esempio completo:
```bash
python main.py -customer "MarioRossi" -template "ORDINE_STANDARD" -eo input/A3.pdf -outputTemplatePath output/risultato.json
```

## Uso Web editor — Gestione template (server.py + React)

### Sviluppo

```bash
# Terminale 1: backend Flask
python server.py
# Avvia su http://localhost:8000

# Terminale 2: frontend Vite
cd web
npm run dev
# Avvia su http://localhost:3000 (proxy /api → 8000)
```

Login: `admin / admin`

### Produzione

```bash
cd web
npm run build

# Poi avvia solo Flask
python server.py
# Serve React build + API su http://localhost:8000
```

### Funzionalità editor

1. **Carica PDF** — drag & drop o file picker
2. **Definisci sezioni** — imposta Y min/max per header, tabella, footer
3. **Definisci colonne** — per ogni colonna: nome, X min, X max, tipo valore
4. **Firma** — stringhe univoche per riconoscere il template
## Struttura template JSON

```json
{
  "name": "NOME_TEMPLATE",
  "description": "Descrizione layout",
  "signature": ["STRINGA_UNIVOCA_1", "STRINGA_UNIVOCA_2"],
  "header": {
    "y_min": 0,
    "y_max": 250,
    "fields": {
      "numero_ordine": {
        "type": "regex_full_text",
        "pattern": "Ordine Nr\\.\\s*(\\S+)",
        "group": 1
      },
      "data": {
        "type": "regex_full_text",
        "pattern": "Data\\s*(\\d{2}/\\d{2}/\\d{4})",
        "group": 1
      }
    }
  },
  "table": {
    "y_min": 250,
    "y_max": 700,
    "layout": "rows",
    "start_after_contains": ["CODICE", "DESCRIZIONE"],
    "end_markers": ["TOTALE", "IMPONIBILE"],
    "row_detect_pattern": "^\\d+\\s",
    "columns": [
      {"name": "codice", "x_min": 0, "x_max": 80, "value": "first_word"},
      {"name": "descrizione", "x_min": 80, "x_max": 400, "value": "joined_text"},
      {"name": "quantita", "x_min": 400, "x_max": 480, "value": "numeric"},
      {"name": "prezzo", "x_min": 480, "x_max": 560, "value": "numeric"}
    ]
  },
  "footer": {
    "y_min": 700,
    "y_max": 1000,
    "fields": {
      "totale": {
        "type": "regex_full_text",
        "pattern": "TOTALE\\s*€?\\s*([\\d.,]+)",
        "group": 1
      }
    }
  }
}
```

### Tipi di regole header/footer

| Tipo | Descrizione |
|---|---|
| `regex_full_text` | Cerca pattern su tutto il testo (con eventuali bound X/Y opzionali) |
| `regex_column_filtered` | Cerca pattern filtrando per colonna X |
| `label_then_value_below` | Cerca etichetta, poi valore nelle righe sottostanti |
| `htr` | Usa HTR (TrOCR/EasyOCR) per zone dove il testo digitale non c'è |

### Tipi valore colonne tabella

| Tipo | Comportamento |
|---|---|
| `first_word` | Prima parola nella colonna |
| `joined_text` | Tutte le parole unite con spazio |
| `numeric` | Filtra solo caratteri numerici/punto/virgola |
| `unit_prefix` | Separa prefisso unità (es. "PZ 5" → due colonne) |
5. **Test** — esegue estrazione in tempo reale sul PDF caricato
6. **Salva** — persiste template nel DB (con backup versione precedente)
Output salvato in `output/<nomefile>_estratto.json` e loggato nel DB.

### Zone barcode Code128 e template su file

L'estrattore mantiene header, table e footer. Un campo header/footer può essere
una zona barcode (`type: "barcode"` oppure `barcode: true`), con rettangolo di
ricerca in coordinate 0-1000 e pagina numerata da 1.

- Lettura template: `templates/decode/NOME.json` prima, DB se il file manca.
- **Salva** nell'editor scrive solo il DB; **Scrivi template su file** scrive
  esplicitamente il file sul backend, con conferma e backup `.json.bak`.
- `web/src/config.json`, sezione `barcode.ai.enabled`, controlla il fallback
  immagine AI (default false). `aiBootstrap.enabled` è il permesso separato per
  creare template mancanti; `-no-ai-bootstrap` lo disabilita per la CLI.
- Per template di soli barcode non serve Tesseract. Nei template misti restano
  i requisiti OCR/HTR esistenti.
- Risultati nel JSON storico, dettagli in `_barcode`; exit code 1 se un barcode
  non viene letto. Il checksum riduce gli errori, non garantisce certezza assoluta.

Manuale completo: [docs/MANUALE_DECODE.md](docs/MANUALE_DECODE.md).
---

## Installazione

```bash
# 1. Entra nel progetto
cd extractPdfData

# 2. Python
## Bootstrap AI — Generare template con OpenRouter

Quando arriva un ordine da layout sconosciuto, puoi usare l'AI per generare template + estrarre dati in un colpo solo:

```bash
python main.py -customer "NuovoCliente" -template "_AI_" -eo documento_nuovo.pdf
```

Se non esiste template `_AI_`, il sistema:
1. Estrae testo + coordinate dal documento
2. Invia il dump a OpenRouter con schema del template
3. Riceve template + dati estratti
4. Salva template in `templates/` e DB
5. Se OCR (>20% righe perse), chiede conferma prima di salvare

Configurazione `.env`:
```
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=anthropic/claude-sonnet-5    # opzionale
```
pip install -r requirements.txt

# 3. Tesseract OCR (vedi sezione Prerequisiti)

# 4. Frontend (opzionale)
cd web
## Configurazione

### config.json (`web/src/config.json`)

Condiviso tra backend e frontend:

```json
{
  "ocrDpi": 200,
  "sectionColors": { "header": "#1e3a8a", "table": "#14532d", "footer": "#581c87" },
  "htr": {
    "engine": "trocr",
    "trocr": {
      "model": "microsoft/trocr-base-printed",
      "preprocess": true,
      "multiline": true
    },
    "easyocr": {
      "language": "it",
      "textThreshold": 0.3
    }
  }
}
```

- `ocrDpi`: risoluzione render pagine PDF per OCR
- `htr.engine`: `"trocr"` per stampatello/manoscritto, `"easyocr"` per testo stampato
- `sectionColors`: colori zone nell'editor

## Struttura progetto

```
extractPdfData/
├── main.py                  CLI estrazione ordini
├── server.py                Flask API server
├── db.py                    Database layer (SQLAlchemy + SQLite)
├── template_engine.py       Motore template: firma, header, tabella, footer
├── image_ocr.py             OCR Tesseract + HTR (EasyOCR/TrOCR) + auto-rotazione
├── ai_bootstrap.py          Bootstrap template via AI (OpenRouter)
├── debug_coordinates.py     Debug: confronta colonne template vs coordinate reali
├── debug_trocr.py           Debug: testa TrOCR su zona immagine con crop salvati
├── _check_db.py             Utility: verifica template attivi nel DB
├── requirements.txt         Dipendenze Python
├── .env                     API key OpenRouter (da creare)
├── extraction.db            Database SQLite (auto-creato)
├── templates/               Template JSON (approvati e draft OCR AI)
│   ├── TEMPLATE_APPROVED/   Template validati manualmente
│   └── draft_ocr/           Template generati da AI (da revisionare)
├── input/                   PDF e immagini di input
├── output/                  JSON estratti
└── web/                     Frontend React + Vite
    ├── src/
    │   ├── App.jsx
    │   ├── api.js
    │   ├── config.json
    │   └── components/
    │       ├── Login.jsx
    │       ├── TemplateEditor.jsx
    │       ├── PdfViewer.jsx
    │       ├── ZoneOverlay.jsx
    │       └── ErrorBoundary.jsx
    ├── package.json
    └── vite.config.js
```

## File di debug

| File | Uso |
|---|---|
| `debug_coordinates.py` | Confronta le zone colonna del template con posizioni reali delle parole nel PDF (nativo o OCR). Calibra x_min/x_max. |
| `debug_trocr.py` | Testa TrOCR su zona immagine salvando crop intermedi. Uso: `python debug_trocr.py img.jpg --x 100 --y 200 --w 600 --h 60 --handwritten` |
| `_check_db.py` | Stampa i template attivi nel database con customer_name |

## Flusso tipico

### Scenario 1: Estrazione batch

```
PDF/immagini → main.py → template_engine
                             ├── match_template (firma)
                             ├── extract_header_field (regex/HTR)
                             ├── extract_table (colonne X)
                             └── apply_template
                                   ↓
                              output JSON + log DB
```

### Scenario 2: Nuovo layout

```
PDF sconosciuto → main.py → nessun template match
                               ↓
                          ai_bootstrap.py → OpenRouter
                               ↓
                          template generato → salva in templates/ + DB
                               ↓
                          estrazione con nuovo template
```

### Scenario 3: Editor template

```
Browser → React → carica PDF (pdfjs-dist)
       → definisci zone/colonne
       → test template → POST /api/test-pdf → server.py
              → template_engine.apply_template()
              ← JSON risultato
       → salva template → POST /api/templates → db.py
```