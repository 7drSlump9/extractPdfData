# soPDF — Split PDF per barcode

## Scopo e avvio

`soPDF.py`, accanto a `main.py`, divide PDF multipagina in pagine singole.
Legge il barcode chiamando **la CLI esterna** di `main.py`, quindi rinomina e,
se configurato, comprime le pagine riuscite. Non modifica il motore di estrazione.

Da terminale, nella cartella del progetto:

```powershell
python soPDF.py
python soPDF.py -config soPDF_config.jsonc
```

Una passata, poi esce. Nessun servizio o scheduler incorporato. Lo scheduling
resta a carico dell'utente. Sono accettate estensioni `.pdf` anche maiuscole.
File arrivati dopo la scansione della cartella saranno presi alla passata successiva.

Codici di uscita: **0** tutto riuscito (anche cartella vuota), **1** errori di
documento/pagina o di lavorazione, **2** altra istanza/lock non recuperabile,
**3** configurazione non valida. Un file ancora instabile viene saltato, non è KO.

## Installazione e portabilità

L'utilità usa la libreria standard, `pypdfium2` e `Pillow`:

```powershell
python -m pip install pypdfium2 Pillow
```

Non richiede Ghostscript, ImageMagick o altri eseguibili per la compressione.
**Non è però Python puro:** PDFium e Pillow contengono codice nativo. Le wheel
devono essere disponibili per architettura, sistema e versione Python del QNAP;
un Python pulito da solo non garantisce compatibilità. Non è ancora verificata
la compatibilità con un particolare QNAP. Il codice dell'utilità richiede Python 3.9+;
il `main.py` attuale può avere requisiti più alti e richiede le proprie dipendenze.
Per installare l'intero progetto vedere anche `requirements.txt` e il README.

`extractor.python` vuoto usa lo stesso interprete dell'utilità; se necessario
specificare un altro interprete/venv. `extractor.mainPy` vuoto usa `main.py`
accanto all'utilità. La directory di lavoro del comando è quella di `main.py`.
Template, permessi AI e dipendenze del lettore restano gestiti da quel programma.
`-no-ai-bootstrap` non disabilita l'eventuale fallback AI barcode: vedere
[MANUALE_DECODE.md](MANUALE_DECODE.md).

## Configurazione con commenti inline

File predefinito: `soPDF_config.jsonc`, accanto all'utilità. Ogni impostazione
è spiegata sulla stessa riga con `//`. Ammessi anche commenti `/* ... */` e
virgole finali. Stringhe, URL e backslash non vengono trattati come commenti.
In stringhe JSON i backslash Windows devono essere doppi, oppure usare `/`.
VS Code riconosce il formato JSON con commenti.

Le sezioni mancanti ricevono i default. Tipi, intervalli e chiavi sconosciute
non validi bloccano l'avvio prima della presa in carico. I percorsi relativi
partono sempre dalla cartella dell'utilità, anche usando un config esterno.
Le cartelle dati devono essere distinte e non contenute l'una nell'altra.

### Cartelle

| Chiave in `paths` | Default | Contenuto |
|---|---|---|
| `in_pdf` | `in_pdf` | Originali da prendere in carico |
| `out_pdf` | `out_pdf` | `<barcode>.pdf` sovrascritto; con separazione attiva `<outputName>/<barcode>.pdf` + suffissi |
| `err_pdf` | `err_pdf` | `<outputName>/<outputName>_p<N>.pdf`, pagine KO originali |
| `worked_pdf` | `worked_pdf` | `<outputName>/<outputName>.pdf`, sorgente elaborato |
| `tempDir` | `temp_split` | Job, pagine, JSON, stato di ripresa e lock |
| `logDir` | `logs` | Log di testo |

Le cartelle vengono create all'avvio. Non eliminare `temp_split` mentre lavora.
Gli originali vengono archiviati anche se alcune pagine sono KO. Un PDF che non
si può dividere va invece intero in `err_pdf/<outputName>/<outputName>.pdf`.

`outputName` è sempre `<origine senza estensione>_ddmmyy`, con data locale di
presa in carico. Per esempio `A.pdf` il 10/11/26 produce `A_101126`; i successivi
omonimi producono `A_101126_1`, `A_101126_2`, ecc. L'11/11/26 si riparte da
`A_111126`. Nessun carattere casuale. I nomi mantengono il case originale ma
le collisioni sono confrontate senza distinzione maiuscole/minuscole anche su Linux.
Il controllo riguarda tutte e tre le destinazioni e i nomi prenotati dai job.

Il nome è riservato con una cartella in `worked_pdf` prima del pickup e salvato
in `job.json`: una ripresa, anche il giorno dopo, non cambia nome. Una cartella
di prenotazione vuota può restare dopo un errore: non viene riutilizzata né cancellata
automaticamente. Le cartelle OK/KO vengono create solo quando servono.
Gli ingressi sono fotografati prima di riprendere i job: anche i file arrivati
durante la ripresa attendono la passata successiva.

### Separazione degli OK e sovrascrittura

`out_pdf_dir_separation` è un booleano al primo livello, **false per default**
anche se assente dal config. Con `false`, gli OK vanno direttamente in `out_pdf`,
senza creare sottocartelle. Se il nome esiste viene sostituito: **vince l'ultima
pubblicazione**, anche fra pagine dello stesso PDF. Nessun suffisso `_1` sugli OK.
Il confronto ignora maiuscole/minuscole e il nome finale conserva il case del
nuovo barcode. La sovrascrittura è definitiva, senza backup dell'output precedente;
gli originali sorgenti restano archiviati. Ogni sostituzione è segnalata con WARNING.

Con `true` si mantiene il comportamento separato descritto nella tabella:
cartella per sorgente e suffissi `_1`, `_2`, ecc. per i duplicati, senza sovrascrivere.
`err_pdf` e `worked_pdf` mantengono sempre le sottocartelle e i suffissi protettivi;
`onDuplicate` non si applica agli OK nella modalità piatta.

La modalità si salva nel job alla presa in carico: cambiare config non cambia
le destinazioni dei job già iniziati. I job della versione precedente con
`outputName` ma senza modalità conservano le sottocartelle degli OK.
Nella ripresa piatta un output completato con contenuto diverso è accettato
(può essere stato sostituito da un'altra pagina); se manca resta un errore.
Una pubblicazione interrotta può ripubblicare sopra un file più recente.
Le vecchie sottocartelle e i file già presenti non vengono migrati o eliminati.

I job preesistenti senza `outputName` completano tutto nel vecchio schema:
OK nella radice `out_pdf`, KO in `err_pdf/<jobId>`, archivio in `worked_pdf/<jobId>`.
Gli output già presenti non vengono spostati. In modalità separata barcode ripetuti nello stesso PDF
usano `_1`, `_2`, ecc.; lo stesso barcode in PDF diversi rimane senza suffisso
perché appartiene a sottocartelle diverse.

### Lettura e nomi

`extractor` imposta customer, template, argomenti extra e timeout **per pagina**.
Il comando usa una lista di argomenti, senza shell:

```text
python main.py -customer UNKNOWN -template barcode001 -no-ai-bootstrap -outputTemplatePath <job>/pages/1.json -eo <job>/pages/1.pdf
```

KO significa: exit code non zero, timeout, JSON mancante/corrotto, campo assente,
barcode non stringa/vuoto, valore in `invalidValues` o regex non rispettata.
`barcode.jsonField` è un campo di primo livello del JSON (default `barcode_top`).
`validationRegex` si applica all'intero valore e distingue maiuscole/minuscole.
Assente o vuota (anche solo spazi) disabilita il filtro regex: qualunque valore
letto è ammesso, salvo valore mancante/vuoto e quelli in `invalidValues` (es. `N/A`).
Il config distribuito usa `^[sS][0-9]{7}$`: accetta `S0000957` e `s0001087`.
Per accettare solo maiuscole usare `^S[0-9]{7}$`, solo minuscole `^s[0-9]{7}$`.
La regola non è imposta dal codice; il default senza voce è nessun filtro regex.
Nessuna conversione: `s0001087` conserva la minuscola anche nel nome file.
Una mancata corrispondenza attiva il retry ruotato se abilitato, poi l'eventuale KO.
Il valore viene privato degli spazi esterni. I caratteri non validi
nei nomi Windows sono sostituiti da `_`; nomi riservati e lunghezze eccessive
vengono resi sicuri. Questo può produrre collisioni: si applica il suffisso `_n`.

### Parallelismo

`parallel.maxWorkers` mancante, `null`, 0 o 1: sequenziale, niente pool.
Un intero maggiore di 1 abilita un pool di thread, ognuno con un processo
`main.py` separato. Nessun calcolo automatico sul numero di CPU. Default **1**.
Gli originali sono sempre elaborati uno per volta. Le chiamate PDFium interne
sono serializzate; gli estrattori esterni restano indipendenti. Con worker
paralleli l'ordine di assegnazione dei suffissi ai barcode uguali non è garantito.
In modalità piatta, più worker rendono non deterministico il vincitore fra pagine
con barcode uguali: vince chi pubblica per ultimo, non l'ultima pagina numerica.
Il programma avvisa nel log, senza modificare i worker configurati. Per ordine
sequenziale impostare 0 o 1. I PDF sorgenti restano elaborati uno alla volta.

### Rotazione dopo mancata lettura

`barcode.rotationOnFailureReading=true` (anche se la voce manca) abilita un
secondo e ultimo tentativo dopo qualunque errore del lettore, inclusi barcode
assente, pagina bianca e timeout. `false` mantiene il singolo tentativo.
La copia temporanea usa `/Rotate` di 180° rispetto all'orientamento corrente,
senza modificare o rasterizzare il contenuto: la zona del template rimane la
stessa, ma legge la parte opposta del foglio originale.

Se il retry riesce, compressione e pubblicazione usano la pagina **ruotata**,
anche se la compressione è disabilitata, saltata o fallisce. Se anche il retry
fallisce, in `err_pdf` va la pagina non ruotata, con entrambi gli errori nel log.
L'origine archiviata in `worked_pdf` rimane invariata. I log `ROTATE` tracciano
il tentativo e l'eventuale successo. Ogni tentativo ha il proprio timeout.

### Compressione

Solo pagine OK, **dopo la lettura sulla pagina originale o ruotata**. `compression.enabled` attiva
il motore `raster`: render PDFium, codifica PDF/JPEG Pillow. Non è uno ZIP.
`dpi` (default 150), `jpegQuality` (60, intervallo 1–95) e `grayscale` controllano
qualità e peso. Le dimensioni fisiche della pagina sono mantenute entro gli
arrotondamenti del render. Il risultato viene riaperto per verificarne la struttura.

Il raster perde testo selezionabile, vettori, collegamenti, livelli e annotazioni
interattive. `skipIfHasText=true` evita la trasformazione se trova testo nativo.
Non è una garanzia di conservazione di tutte le caratteristiche PDF.
`minGainPercent=10` conserva l'originale se il risparmio è sotto il 10%; con
`keepOriginalIfLarger=true` non accetta mai un aumento di peso. Abbassare la
qualità può rendere il barcode dell'output meno leggibile: tarare su documenti reali.

Un errore di compressione conserva la pagina originale e scrive un WARN: non
trasforma una lettura riuscita in KO. Le pagine KO e l'origine archiviata non
vengono compresse. Nessuna promessa di riduzione: una scansione già ottimizzata
potrebbe rimanere invariata. I PDF vengono pubblicati completi: per gli OK piatti
si usa una copia temporanea e `os.replace` nello stesso filesystem; nessun PDF
parziale sostituisce il precedente. Per il cambio di case del nome possono servire
due rinomine: in caso di interruzione la ripresa ripubblica dal file preparato.
Le pubblicazioni senza sovrascrittura usano hard link sul filesystem di output:
occorre un filesystem che li supporti (verificare anche eventuali mount di rete).

### Log

Solo file di testo e console: l'utilità **non usa SQLite**. `main.py` scrive
invece già autonomamente sul proprio DB; quel comportamento non viene cambiato.
Con più processi SQLite può perdere eventi per contesa: per questa utilità
fare riferimento al log di testo.

`log.fileName=split.log`, `log.maxSizeMB=10` (10 × 1024 × 1024 byte),
`log.backupCount=5`, `log.level=INFO`. La soglia ammette decimali positivi.
Con zero backup il file attivo viene troncato alla rotazione, senza storico.
Un singolo messaggio più grande del limite può comunque superarlo.
Livelli: DEBUG, INFO, WARN/WARNING, ERROR. INFO raccomandato per traccia completa.

Ogni evento contiene data/ora locale, livello, operazione, job e pagina:

```text
2026-11-09 10:15:02 | INFO | PICKUP | job=... | file=a.pdf sha256=...
2026-11-09 10:15:09 | INFO | EXTRACT | job=... p1 | barcode=S0000854
2026-11-09 10:15:10 | INFO | COMPRESS | job=... p1 | bytes=250000->90000
2026-11-09 10:15:15 | ERROR | EXTRACT | job=... p3 | exit=1 stdout=... stderr=...
```

Il riepilogo riporta job, pagine OK/KO e risparmio delle pagine di questa passata.
Non viene registrata la chiave API Brevo. Gli output del lettore possono contenere
dati del documento: proteggere l'accesso ai log.

### Report ed email Brevo

Un lancio con A.pdf, B.pdf e C.pdf produce **un report JSON e una email** per
tutti e tre. Il report si trova in `paths.reportDir` (default `reports`), con
nome `run_<data_ora>_<id>.json`: data e id evitano collisioni. Nessun report né
email nuova quando non ci sono elaborazioni (i soli file instabili sono saltati).
I report non vengono cancellati automaticamente; `reports/` è ignorata da Git.

Il report contiene inizio/fine/durata del lancio e un blocco `pdfs` per sorgente:
nome originale, outputName, tempi, fogli totali/processati, OK/KO, ruotati,
esito e lista `errors` con nome file, pagina e motivo. Su PDF illeggibile il
totale fogli è `null`, non un conteggio inventato. I job vecchi senza data di
inizio hanno tempi non disponibili. Le durate dei job ripresi includono le pause.
`totals` raccoglie i conteggi, `email` stato e tentativi. Il report viene scritto
atomicamente prima e durante la lavorazione e prima della pulizia dei job.
Un arresto brusco lascia l'ultimo checkpoint, non necessariamente l'ultima pagina.

`email.enabled` abilita gli invii; `sendOn` ammette `always` (default), `error`,
`never`. Nel config distribuito l'invio è abilitato. Il blocco SMTP precedente è
stato sostituito: vecchie chiavi SMTP vanno rimosse dal config.
`from` contiene nome e indirizzo verificato, `to` una lista di indirizzi.
`apiKeyEnvVar` indica la variabile dell'ambiente o del `.env` accanto a soPDF.py:
il default è `BREVO_API_KEY`; se hai usato `BREV_API_KEY`, imposta esattamente
quel nome nel config oppure correggi tu il `.env`. L'ambiente prevale sul file.
La chiave non è mai salvata nei report o stampata nei log.

Oggetto, oggetto in caso di errori (`subjectOnError`, facoltativo), testo e HTML
si configurano in `soPDF_email.jsonc`, indicato da `email.templateFile`.
In cima al file trovi i segnaposto disponibili; per esempio
`soPDF - {total_pdfs} PDF, {total_pages} fogli - {status}`.
`fileTemplate` e `errorTemplate` compongono le righe per PDF ed errori.
I dati HTML sono protetti; per scrivere graffe letterali usare `{{` e `}}`.
Segnaposto sconosciuti vengono rifiutati prima dell'elaborazione.
Dentro Python i dati sono disponibili in `Runner.report.data`; la funzione
`soPDF_reporting.render_email` restituisce il messaggio e il dizionario dei
segnaposto. Nessun allegato viene mai aggiunto.

L'invio usa HTTPS `POST https://api.brevo.com/v3/smtp/email`, con timeout
`email.timeoutSec`. Un errore di invio non cambia l'esito dell'elaborazione;
stato `failed` nel report, WARNING nel log. I riepiloghi `pending`/`failed`
entro `retryDays` (default 7) sono ritentati al lancio successivo, anche vuoto,
in un'unica email insieme al riepilogo corrente, se presente. I job ripetuti
nel recupero sono conteggiati una sola volta usando l'osservazione più recente.
I report scaduti restano sul disco, ma non vengono più ritentati.
Brevo 201 significa accettazione, non conferma della consegna. Un timeout o
un arresto dopo l'accettazione può causare un duplicato al retry: non è garantito
l'invio esattamente una volta. I report locali permettono sempre la verifica.

## Omonimie, lock e sicurezza

Ogni sorgente viene spostato subito in un job con timestamp e UUID: due `a.pdf`
successivi non condividono cartelle. Non si può impedire che il mittente
sovrascriva un file già presente in `in_pdf` **prima** della presa in carico.
Per consegna affidabile copiare con estensione `.part` e rinominare in `.pdf`
solo a copia completata. Il controllo dimensione/mtime per `minFileAgeSec`
è una protezione euristica, non certifica la fine di una copia sospesa.
Si raccomanda `in_pdf` e `tempDir` sullo stesso filesystem per una presa in
carico atomica; su filesystem diversi viene usata copia seguita da rimozione.

Un lock esclusivo impedisce due istanze sulla stessa area temporanea.
`lockStaleMin` non autorizza mai a rubare il lock di un PID ancora vivo.
Un lock sufficientemente vecchio viene recuperato solo se il processo locale
risulta terminato. Lock illeggibile o di altro host: verifica manuale necessaria.
Non condividere le stesse cartelle dati tra configurazioni con `tempDir` diversi.

## Ripresa dopo interruzione

Ogni job conserva `job.json` con le pagine completate e i file destinazione.
All'avvio, prima dei nuovi ingressi, vengono ripresi i job incompleti.
Gli esiti già salvati non sono rielaborati; una pubblicazione interrotta viene
riconciliata controllando hash e destinazione. Stato scritto con sostituzione
atomica. La configurazione corrente viene usata per le pagine ancora mancanti.
Non modificare o rimuovere output e stato durante la ripresa.

In caso di errore infrastrutturale (disco pieno, permessi, pubblicazione fallita)
il job e l'origine vengono mantenuti in `temp_split`, con errore nel log.
Ripristinare la causa e rilanciare. Job senza stato valido: controllo manuale,
nessuna cancellazione automatica. `keepTemp=true` conserva i job terminati per
debug, senza rielaborarli. Con false vengono puliti solo i job terminati.
La sola lettura sul sorgente preso in carico viene rimossa su Windows; anche
la pulizia riprova la rimozione di file temporanei in sola lettura. Il file
`job.json` viene eliminato per ultimo. Se la pulizia fallisce, il job resta
completato: WARN `CLEANUP`, nessun errore di estrazione né exit code 1 per questo
solo motivo. Alla prossima passata viene ritentata solo la pulizia.
Una cartella senza `job.json` viene segnalata come orfana con WARN, senza
cancellazione, ripresa o errore di uscita: richiede verifica manuale.
La ripresa limita i duplicati dopo un arresto del processo; non sostituisce un
backup e non garantisce transazioni distribuite contro guasti disco/power loss.

## Verifiche

```powershell
python -m unittest discover -s tests -p test_soPDF.py -v
```

Prima dell'uso reale sul QNAP provare un piccolo lotto e verificare leggibilità,
peso, dipendenze native, permessi, hard link e timeout. Impostare inizialmente
`maxWorkers=1`. Non abbassare troppo DPI/qualità senza verifica visiva.

### Prova locale effettuata

Sul campione `docs/PTV/merged.pdf` (4 pagine), con copie isolate del lettore e
del suo DB, DPI 150 e qualità JPEG 60:

| Pagina/barcode | PDF singolo originale (byte) | Output (byte) |
|---|---:|---:|
| S0000830 | 245446 | 180906 |
| S0000831 | 250260 | 183813 |
| S0000855 | 267338 | 187954 |
| S0000854 | 257583 | 182492 |

Totale pagine: 1020627 → 735165 byte, circa **28% in meno**. Tempi osservati
3,72 s con 1 worker, 1,90 s con 2; non sono previsioni delle prestazioni QNAP.
Riletti con successo anche tutti e quattro i barcode nei PDF compressi.
Sul solo percorso template barcode del campione, la rilettura funziona bloccando
gli import di torch, easyocr, transformers e pytesseract. Questo non prova che
quelle librerie siano superflue per template misti o altre modalità del lettore.

Suite completa eseguita: 31 test riusciti (19 split, 12 preesistenti barcode).
I test barcode esistenti possono scrivere eventi sul DB del progetto; segnalano
anche warning preesistenti relativi a datetime UTC e connessione SQLite aperta.