# Estrazione generale e zone barcode Code128

[README](../README.md) · [Manuale split soPDF](MANUALE_soPDF.md)

## Indice

- [Scopo](#scopo)
- [Avvio rapido e verifica](#avvio-rapido-e-verifica)
- [Template: file prima, database dopo](#template-file-prima-database-dopo)
- [Creare il riquadro barcode](#creare-il-riquadro-barcode)
- [Configurazione generale e permesso AI](#configurazione-generale-e-permesso-ai)
- [Uso da linea di comando](#uso-da-linea-di-comando)
- [Percorso PDF e compatibilità](#percorso-pdf-e-compatibilità)
- [Limiti del decoder attuale](#limiti-del-decoder-attuale)
- [Problemi comuni](#problemi-comuni)
- [Checklist prima di integrare](#checklist-prima-di-integrare)
- [Verifica tecnica](#verifica-tecnica)

> **Due programmi, due risultati:** `main.py` estrae dati in JSON; `soPDF.py`
> usa quel lettore per dividere e rinominare PDF. I rispettivi codici di uscita
> e file di configurazione non sono intercambiabili.

## Scopo

Il programma resta un estrattore generale basato su template: **header, table,
footer**. Non esiste una nuova utility separata. Una zona barcode è un tipo
aggiuntivo di campo nell'header o nel footer; può convivere con regex, OCR, HTR
e tabella. Un template può anche avere soltanto un header con un barcode.

Il tuo programma esterno passa i documenti a `main.py`. L'editor web serve a
preparare e verificare i template, non deve essere aperto durante l'estrazione CLI.

## Avvio rapido e verifica

1. Preparare l'ambiente seguendo i [prerequisiti del README](../README.md#prerequisiti).
   Usare copie dei documenti per la prima prova e conservare un backup dei template.
2. Aprire l'editor su una rete fidata. L'avvio attuale usa `admin/admin` e Flask
   in debug: non esporlo a Internet. Vedere l'[avviso editor](../README.md#sviluppo).
3. Creare o importare un template, impostare cliente, nome e un campo barcode.
   Controllare pagina e coordinate con un PDF rappresentativo.
4. Per una prova senza AI disabilitare `barcode.ai.enabled` e
   `aiBootstrap.enabled` in `web/src/config.json`, mantenendo le altre sezioni.
5. Premere **Test**, confrontare il valore con il documento, poi **Scrivi template
   su file** per rendere esplicita la versione usata dalla CLI. **Salva** da solo
   modifica soltanto il DB.
6. Eseguire la [CLI](#uso-da-linea-di-comando) con gli stessi cliente e nome template,
   indicando un output JSON dedicato. Controllare codice di uscita e file prodotto.

Per una lettura locale attesa verificare valore, `_barcode.NOME_CAMPO.method`
uguale a `local` e `verified` uguale a `true`. Confrontare comunque il risultato
con il documento: il checksum non garantisce l'identità del codice cercato.
Ripetere su scansioni e orientamenti diversi prima dell'integrazione.

Se il chiamante sarà soPDF, allineare `extractor.customer`, `extractor.template`
e `barcode.jsonField` nel suo config. Per le pagine singole prodotte dallo split
il campo deve riferirsi alla pagina **1**, anche se il barcode era su una pagina
successiva del PDF originale.

## Template: file prima, database dopo

Per un template di nome `BARCODE_ALTO_DX` il motore cerca:

1. `templates/decode/BARCODE_ALTO_DX.json`, relativo alla cartella del progetto;
2. se il file manca, il template attivo nel database SQLite `extraction.db`;
3. se manca anche nel database, il comportamento storico è creare un template
   con AI, **soltanto se il bootstrap AI è consentito**.

Il nome interno `name` deve corrispondere al nome del file senza `.json`.
Il cliente, se specificato, deve corrispondere a `customer_file` sul file o al
cliente del template nel DB. Un file presente ma errato non viene ignorato:
si segnala l'errore, evitando di usare silenziosamente una versione diversa.

La cartella `templates/decode` può contenere anche template generali, non solo
barcode. Le cartelle storiche `TEMPLATE_APPROVED` e `draft_ocr` non vengono
scansionate automaticamente. La priorità riguarda la definizione del template;
la CLI continua a usare il DB per i log. Non è una modalità completamente senza DB.

### Salvataggio dall'editor

- **Salva**: salva soltanto nel DB, come prima. Non crea né aggiorna il file.
- **Scrivi template su file**: scrive esplicitamente la versione attualmente
  aperta nell'editor in `templates/decode/NOME.json`, sul computer che esegue il
  backend (sul NAS, se il backend gira sul NAS). Non salva automaticamente nel DB.
- Se il file esiste già, viene chiesta conferma. Prima della sostituzione viene
  conservata una copia `NOME.json.bak`. È un solo backup, non uno storico completo.
- L'elenco dell'editor include template da file e da DB; quando apri un template
  vedi l'origine `file` o `db`. La versione su file ha sempre la precedenza.

**Conseguenza importante:** dopo aver scritto il file, salvare soltanto nel DB
non cambia la versione usata dall'estrazione. Per aggiornarla premi nuovamente
**Scrivi template su file**. Per tornare al DB rimuovi il file `.json`.
Il pulsante di cancellazione storico disattiva il DB: non elimina il file.

Il pulsante **Template JSON** importa un file del tuo PC nell'editor; non lo
installa da solo nella cartella del backend.

## Creare il riquadro barcode

**Nuovo template** crea le tre sezioni vuote. **Nuovo template barcode** crea
soltanto l'header con un campo Code128. Header, table e footer hanno tutti un
pulsante **Rimuovi sezione**: se ci sono campi/colonne viene chiesta conferma.
La sezione rimossa scompare dall'overlay e non viene scritta nel JSON, né nel DB.
Puoi ricrearla con **Aggiungi header / tabella / footer**; i campi eliminati non
vengono ripristinati. Il caricamento non ricrea più sezioni assenti.
Puoi rimuovere tutte le sezioni, ma per Test, Salva e Scrivi template su file
serve almeno un campo o una colonna con nome. Per salvare serve anche il nome
del template. Le sezioni presenti ma vuote restano nel file finché non le rimuovi.

1. Carica un PDF e un template nell'editor (anche il template di esempio).
2. Aggiungi un campo nell'header o nel footer.
3. Dai un nome al campo, ad esempio `codice_documento`.
4. Spunta **Barcode**. Barcode e HTR sono alternativi.
5. Posiziona e ridimensiona il rettangolo nell'intorno in cui compare il barcode.
6. Imposta **Pagina**: numerazione da 1. Il numero si specifica nel campo;
   cambiare pagina nel visualizzatore non cambia automaticamente questo valore.
7. Lascia `Pattern` vuoto oppure `.+` per restituire il valore completo.
8. Premi **Test**, controlla il risultato, poi **Salva** e/o **Scrivi template su file**.

Il riquadro è un'area di ricerca, non deve coincidere perfettamente con le barre.
Lascia spazio bianco attorno al codice. Il motore aggiunge un margine configurabile
e cerca su più righe del ritaglio, in entrambi i versi e con varie rotazioni.
Non cerca automaticamente nel resto del documento se il codice è fuori dall'area.
Se ci sono più barcode nel riquadro viene restituita la prima lettura valida:
per evitarne uno sbagliato, restringi il riquadro a un solo codice.

### Esempio completo di template su file

```json
{
  "name": "BARCODE_ALTO_DX",
  "customer_file": "CLIENTE",
  "description": "Code128 in alto a destra, pagina 1",
  "header": {
    "fields": {
      "codice_documento": {
        "type": "barcode",
        "barcode": true,
        "pattern": ".+",
        "group": 1,
        "page": 1,
        "x_min": 500,
        "x_max": 1000,
        "y_min": 0,
        "y_max": 250,
        "ai_fallback": false
      }
    }
  }
}
```

Le coordinate sono in **permille**, da 0 a 1000: X da sinistra a destra, Y
dall'alto verso il basso, rispetto alla singola pagina indicata. `500` è metà
pagina. `x_min < x_max` e `y_min < y_max` sono obbligatori. `table` e `footer`
possono mancare; in quel caso `righe` è una lista vuota.

È incluso `templates/decode/_esempio_barcode.json`, cliente `ESEMPIO`, da copiare
o importare e adattare ai documenti reali.

## Configurazione generale e permesso AI

Il file delle impostazioni resta **`web/src/config.json`**. È distinto dai
file dei template. Le sezioni esistenti per colori e HTR restano disponibili.
Le nuove sezioni sono:

```json
{
  "aiBootstrap": { "enabled": true },
  "barcode": {
    "renderDpi": 300,
    "searchMargin": 0.10,
    "tryRotations": true,
    "ai": {
      "enabled": false,
      "model": "google/gemini-2.5-flash",
      "timeout": 60
    }
  }
}
```

Questo frammento va integrato nel file, senza eliminare le sezioni esistenti.

| Impostazione | Significato |
|---|---|
| `renderDpi` | Risoluzione di render delle pagine con barcode, tra 72 e 1200 |
| `searchMargin` | Margine aggiunto su ogni lato, come frazione della dimensione della zona; 0.10 = 10% |
| `tryRotations` | Abilita tentativi di rotazione e inclinazione |
| `barcode.ai.enabled` | Permesso globale di inviare il ritaglio all'AI, default false |
| `model` | Modello OpenRouter con supporto immagini; disponibilità da verificare con il provider |
| `timeout` | Timeout HTTP in secondi |
| `aiBootstrap.enabled` | Permesso separato per creare template mancanti con il flusso AI storico |

### Regole di fallback barcode

1. Si tenta sempre il decoder locale.
2. Se legge un Code128 con checksum coerente, l'AI non viene chiamata.
3. Se fallisce e `barcode.ai.enabled` è `false`, niente rete per il barcode.
4. Se è `true`, il campo può comunque vietarlo con `ai_fallback: false`.
5. `ai_fallback: true`, `null` o assente **non supera mai** il divieto globale.

La chiave `OPENROUTER_API_KEY` viene letta dall'ambiente oppure dal `.env`
accanto al programma. All'AI viene inviato soltanto il ritaglio con il margine:
può contenere anche dati sensibili vicini al barcode. La chiamata può avere costi.
Il valore AI non ha una verifica del checksum sulle barre e può essere sbagliato:
il risultato è marcato `verified: false`. La rete AI è coperta dai test con mock;
non sono state effettuate chiamate reali a pagamento.

**Per impedire entrambe le forme di AI**, imposta sia
`barcode.ai.enabled: false` sia `aiBootstrap.enabled: false`.
Il flag CLI `-no-ai-bootstrap` blocca solo la creazione dei template, non il
fallback immagine barcode eventualmente abilitato dalla configurazione.

### File mancante e modifiche al volo

Se manca `web/src/config.json`, il backend lo crea con valori standard, senza
template. I template restano file separati e/o record nel DB: creare questa config
non maschera il DB. JSON corrotto o flag di permesso non booleani producono errore,
senza sovrascrivere il file esistente.

Le impostazioni barcode e il resolver dei template rileggono i file nelle nuove
operazioni. La CLI li rilegge a ogni invocazione. Non cambiare un file mentre lo
si sta scrivendo parzialmente: salva con sostituzione atomica, oppure tra due chiamate.
I colori del frontend sono importati durante la build; non sono hot reload in
produzione. L'impostazione OCR storica `ocrDpi` viene caricata all'avvio del processo.

## Uso da linea di comando

Esempio PowerShell con percorsi assoluti:

```powershell
python '<cartella-progetto>\main.py' `
  -customer 'CLIENTE' -template 'BARCODE_ALTO_DX' -no-ai-bootstrap `
  -outputTemplatePath 'C:\dati\risultato.json' -eo 'C:\dati\documento.pdf'
```

Le opzioni vanno preferibilmente **prima di `-eo` o `-eoi`**, specialmente per
immagini multiple. `-eo` elabora un PDF per invocazione; `-eoi` accetta immagini.
`-outputTemplatePath`, nonostante il nome storico, indica il **JSON dei risultati**,
non il file del template. Senza questa opzione viene scritto
`output/NOME_DOCUMENTO_estratto.json` nella cartella del progetto.

Il programma stampa anche messaggi di log e un riepilogo: stdout **non è solo JSON**.
Il programma chiamante deve leggere il file di risultato e il codice di uscita.

```python
import json
import subprocess
from pathlib import Path

root = Path(r"<cartella-progetto>")
result_path = Path(r"C:\dati\risultato.json")
completed = subprocess.run([
    "python", str(root / "main.py"),
    "-customer", "CLIENTE", "-template", "BARCODE_ALTO_DX",
    "-no-ai-bootstrap", "-outputTemplatePath", str(result_path),
    "-eo", r"C:\dati\documento.pdf",
], capture_output=True, text=True)
if completed.returncode != 0:
    raise RuntimeError(completed.stdout + completed.stderr)
data = json.loads(result_path.read_text(encoding="utf-8"))
print(data["codice_documento"])
```

Usa un percorso di output diverso per ogni documento se il chiamante lavora in
parallelo. Un vecchio file di risultato può restare in caso di errore prima della
scrittura: non usarlo se il codice di uscita segnala fallimento.

### Risultato e uscita

Il campo rimane una stringa, compatibile con il risultato generale. I dettagli
del barcode sono aggiunti nella chiave `_barcode`:

```json
{
  "formato": "BARCODE_ALTO_DX",
  "codice_documento": "ABC123",
  "_barcode": {
    "codice_documento": {
      "value": "ABC123",
      "method": "local",
      "verified": true
    }
  },
  "righe": []
}
```

Il programma aggiunge anche i metadati storici, come cliente, modalità e qualità.
`method` è `local`, `ai` oppure `none`. Se non legge, il valore è `N/A`.
`verified: true` significa checksum Code128 controllato, **non certezza assoluta**.
Il checksum modulo 103 non è una firma crittografica e non elimina ogni falso positivo.
Se imposti una regex, può filtrare/estrarre una parte del payload; il checksum
verifica il payload originale, non il valore trasformato dalla regex.

Codice di uscita 0: elaborazione completata senza zone barcode `N/A`.
Codice 1: errore oppure almeno una zona barcode non letta. In quest'ultimo caso
il JSON viene comunque scritto, per consentire di ispezionare i risultati parziali.
Una lettura AI non verificata è un risultato, quindi non comporta da sola uscita 1:
il chiamante deve verificare `method`/`verified` se vuole accettare solo letture locali.

## Percorso PDF e compatibilità

Le pagine con barcode vengono renderizzate anche nei PDF con testo nativo.
Per template di **sole zone barcode**, non si esegue OCR globale e Tesseract non
serve. Nei template misti con scansioni il flusso OCR generale rimane necessario.

Il decoder è scritto in Python e usa NumPy/Pillow, già presenti nel progetto.
Non richiede nuove librerie barcode, Tesseract, torch o modelli AI per il decode
locale. NumPy e Pillow, però, hanno componenti compilati; anche il rendering PDF
usa dipendenze native. Quindi non si può promettere che l'intera applicazione sia
"Python puro che gira ovunque".

Su QNAP va verificata l'architettura e la disponibilità delle dipendenze di
rendering: x86_64 e ARM64 sono generalmente più semplici di ARMv7. L'applicazione
non è stata provata su un QNAP reale. Nessun Dockerfile o QPKG è stato aggiunto.
Il requirements attuale comprende ancora i motori OCR/HTR: non è stato ridotto.

## Limiti del decoder attuale

- Codici Code128 A/B/C, cambi di set, SHIFT e gestione FNC1/GS1; FNC2/FNC3 non
  vengono interpretati e la lettura viene scartata. Il separatore GS1 interno è `\u001d`.
- Cerca su un massimo di 81 righe per angolo. Prova soglie multiple, entrambi i
  versi, orientamenti ortogonali e inclinazioni fino a circa 20 gradi.
- Non è un localizzatore geometrico avanzato: non corregge prospettiva forte,
  deformazioni o barre tagliate; non esegue una stima continua dell'angolo.
- Un barcode molto piccolo dentro un riquadro enorme può essere saltato. Conviene
  definire un'area abbastanza stretta e scegliere un DPI adeguato.
- Sfocatura, rumore e bassa risoluzione possono causare mancata lettura.
- Nessuna integrazione opzionale zxing-cpp è stata aggiunta: il percorso locale
  attuale usa solo il decoder del progetto.

## Problemi comuni

| Sintomo | Controllo e intervento |
|---|---|
| Campo `N/A` | Verificare pagina, coordinate in permille, margine bianco e risoluzione. Il decoder non cerca fuori dalla zona. |
| Letto un codice diverso | Restringere il riquadro a un solo barcode e verificare pattern e pagina del campo. |
| Modifiche salvate ma CLI invariata | Un file in `templates/decode` precede il DB: riscrivere il file dall'editor oppure rimuoverlo per tornare al DB, dopo backup. |
| Template non trovato o cliente non valido | Controllare nome file, proprietà `name`, `customer_file` e argomenti CLI. Un file presente ma errato non provoca fallback silenzioso al DB. |
| Template importato ma assente sul backend | **Template JSON** carica nell'editor; usare **Scrivi template su file** per installarlo sul backend. |
| Valore con `method: ai`, `verified: false` | Lettura AI non verificata sulle barre. Il chiamante deve applicare la propria politica di accettazione. |
| AI chiamata con `-no-ai-bootstrap` | Il flag riguarda solo la creazione template. Disabilitare anche `barcode.ai.enabled` per impedire il fallback barcode. |
| JSON esistente dopo un errore | Potrebbe essere un risultato precedente. Usare output distinti e non accettarlo come nuovo risultato senza verificarne l'esito. |
| Lettura CLI OK, pagina soPDF KO | Verificare nome del campo di primo livello e regex nel config soPDF; sono controlli aggiuntivi rispetto al decoder. |

Per diagnosticare conservare comando senza segreti, codice di uscita, log e JSON
prodotto. Prima di condividerli rimuovere dati personali; non allegare `.env`.

## Checklist prima di integrare

- [ ] Template e cliente coerenti fra file, editor e CLI.
- [ ] Origine del template nota (`file` o `db`) e copia di backup disponibile.
- [ ] Campo, pagina, coordinate e pattern provati su documenti rappresentativi.
- [ ] Politica AI esplicita: permessi globali, invio dei ritagli e costi valutati.
- [ ] Chiamante legge il file JSON, non tenta di interpretare tutto stdout come JSON.
- [ ] Output distinto per documento e gestione esplicita di exit 1 e risultati parziali.
- [ ] Se richiesto, rifiuto delle letture non locali o non verificate nel chiamante.
- [ ] Backup di `extraction.db` oltre ai file template: la CLI usa ancora il DB per i log.

Il backup `.json.bak` conserva una sola versione precedente e non sostituisce un
backup periodico. La cancellazione dall'editor non elimina il template su file.

## Verifica tecnica

```powershell
python -m unittest discover -s '<cartella-progetto>\tests' -v
npm.cmd --prefix '<cartella-progetto>\web' run build
```

Test inclusi: pattern Code128 di riferimento indipendente dalla tabella decoder,
rotazioni 0/7/-12/90/180/270, checksum errato, immagine vuota, cambi di set/GS1,
divieto AI globale e per zona, priorità file/DB e backup, configurazione corrotta,
render PDF e API editor. Resta necessaria una prova con i tuoi PDF barcode reali.