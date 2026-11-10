"""Split PDF per barcode. CLI esterna, JSONC, job riprendibili, nessun DB.

Run: python soPDF.py [-config soPDF_config.jsonc]
Tests: python -m unittest discover -s tests -p test_soPDF.py -v
"""
import argparse
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import pypdfium2 as pdfium


ROOT = Path(__file__).resolve().parent
PDFIUM_LOCK = threading.RLock()
DEFAULTS = {
    'extractor': {'python': '', 'mainPy': '', 'customer': 'UNKNOWN',
                  'template': 'barcode001', 'extraArgs': ['-no-ai-bootstrap'], 'timeoutSec': 120},
    'paths': {'in_pdf': 'in_pdf', 'out_pdf': 'out_pdf', 'err_pdf': 'err_pdf',
              'worked_pdf': 'worked_pdf', 'tempDir': 'temp_split', 'logDir': 'logs'},
    'barcode': {'jsonField': 'barcode_top', 'validationRegex': '', 'invalidValues': ['N/A', ''],
                'rotationOnFailureReading': True},
    'onDuplicate': 'suffix_n', 'out_pdf_dir_separation': False,
    'minFileAgeSec': 5, 'lockStaleMin': 60, 'keepTemp': False,
    'parallel': {'maxWorkers': 1},
    'compression': {'enabled': True, 'engine': 'raster', 'dpi': 150, 'jpegQuality': 60,
                    'grayscale': False, 'minGainPercent': 10, 'keepOriginalIfLarger': True,
                    'skipIfHasText': True},
    'log': {'fileName': 'split.log', 'maxSizeMB': 10, 'backupCount': 5, 'level': 'INFO'},
    'email': {'sendOnError': False, 'smtpHost': '', 'smtpPort': 587, 'useTls': True,
              'username': '', 'passwordEnvVar': 'SPLIT_SMTP_PASSWORD', 'from': '', 'to': [],
              'subject': 'PDF split: barcode non letto',
              'body': 'File {file}: pagine non lette: {pages}', 'attachFailedPdf': True},
}


class ConfigError(ValueError):
    pass


class LockError(RuntimeError):
    pass


class PageError(RuntimeError):
    """Solo errori di estrazione: non include errori disco/pubblicazione."""


def parse_jsonc(text):
    """Rimuove commenti e virgole finali preservando posizioni e stringhe."""
    chars = list(text)
    i, quoted, escaped = 0, False, False
    while i < len(chars):
        c = chars[i]
        if quoted:
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c == '"':
                quoted = False
            i += 1
        elif c == '"':
            quoted = True
            i += 1
        elif c == '/' and i + 1 < len(chars) and chars[i + 1] in ('/', '*'):
            start, block = i, chars[i + 1] == '*'
            end = text.find('*/', i + 2) if block else text.find('\n', i + 2)
            if block and end < 0:
                raise json.JSONDecodeError('Commento a blocco non terminato', text, start)
            end = (end + 2) if block else (len(chars) if end < 0 else end)
            for j in range(start, end):
                if chars[j] not in '\r\n':
                    chars[j] = ' '
            i = end
        else:
            i += 1
    quoted, escaped = False, False
    for i, c in enumerate(chars):
        if quoted:
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c == '"':
                quoted = False
        elif c == '"':
            quoted = True
        elif c == ',':
            j = i + 1
            while j < len(chars) and chars[j].isspace():
                j += 1
            if j < len(chars) and chars[j] in '}]':
                chars[i] = ' '
    return json.loads(''.join(chars))


def load_config(path):
    try:
        supplied = parse_jsonc(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ConfigError(f'Config {path}: {exc}') from exc

    def merge(default, value, key='config'):
        if not isinstance(value, dict):
            raise ConfigError(f'{key}: richiesto oggetto')
        unknown = set(value) - set(default)
        if unknown:
            raise ConfigError(f'{key}: chiavi sconosciute {sorted(unknown)}')
        result = copy.deepcopy(default)
        for name, item in value.items():
            location = f'{key}.{name}'
            expected = default[name]
            if isinstance(expected, dict):
                result[name] = merge(expected, item, location)
            else:
                nullable = location == 'config.parallel.maxWorkers' and item is None
                numeric = type(expected) in (int, float) and type(item) in (int, float)
                if not nullable and not numeric and type(item) is not type(expected):
                    raise ConfigError(f'{location}: tipo non valido')
                if numeric and not math.isfinite(item):
                    raise ConfigError(f'{location}: numero non finito')
                if isinstance(item, list) and any(not isinstance(v, str) for v in item):
                    raise ConfigError(f'{location}: richiesta lista di stringhe')
                result[name] = item
        return result

    cfg = merge(DEFAULTS, supplied)

    def number(section, key, minimum, maximum=None, integer=False):
        value = cfg[section][key] if section else cfg[key]
        label = f'{section}.{key}' if section else key
        if value < minimum or (maximum is not None and value > maximum) or (
                integer and type(value) is not int):
            raise ConfigError(f'{label}: valore fuori intervallo o non intero')

    number('extractor', 'timeoutSec', 0.001)
    number('', 'minFileAgeSec', 0)
    number('', 'lockStaleMin', 0)
    number('compression', 'dpi', 36, 1200)
    number('compression', 'jpegQuality', 1, 95, True)
    number('compression', 'minGainPercent', 0, 100)
    number('log', 'maxSizeMB', 0.000001)
    number('log', 'backupCount', 0, integer=True)
    number('email', 'smtpPort', 1, 65535, True)
    workers = cfg['parallel']['maxWorkers']
    if workers is not None and (type(workers) is not int or workers < 0):
        raise ConfigError('parallel.maxWorkers: richiesto intero >=0 oppure null')
    cfg['parallel']['maxWorkers'] = max(1, workers or 1)
    if cfg['compression']['engine'] != 'raster' or cfg['onDuplicate'] != 'suffix_n':
        raise ConfigError('engine supportato: raster; onDuplicate supportato: suffix_n')
    if cfg['log']['level'] not in ('DEBUG', 'INFO', 'WARN', 'WARNING', 'ERROR'):
        raise ConfigError('log.level non valido')
    name = cfg['log']['fileName']
    if not name or re.search(r'[\\/:*?"<>|\x00-\x1f]', name) or name in ('.', '..'):
        raise ConfigError('log.fileName: richiesto solo nome file sicuro')
    for key in ('customer', 'template'):
        if not cfg['extractor'][key].strip():
            raise ConfigError(f'extractor.{key}: obbligatorio')
    if not cfg['barcode']['jsonField']:
        raise ConfigError('barcode.jsonField non può essere vuoto')
    try:
        if cfg['barcode']['validationRegex'].strip():
            re.compile(cfg['barcode']['validationRegex'])
    except re.error as exc:
        raise ConfigError(f'barcode.validationRegex: {exc}') from exc
    # Blocca flag che potrebbero alterare l'interfaccia e le destinazioni gestite.
    forbidden = {'-eo', '-eoi', '-outputTemplatePath', '-append', '-customer', '-template', '-t'}
    if forbidden.intersection(cfg['extractor']['extraArgs']):
        raise ConfigError('extractor.extraArgs: flag riservato all’utilità')
    paths = []
    for key, value in cfg['paths'].items():
        if not value.strip():
            raise ConfigError(f'paths.{key}: percorso vuoto')
        resolved = (ROOT / value).resolve()
        if resolved == ROOT or ROOT.is_relative_to(resolved):
            raise ConfigError(f'paths.{key}: non usare root progetto o suoi antenati')
        for previous in paths:
            if resolved == previous or resolved.is_relative_to(previous) or previous.is_relative_to(resolved):
                raise ConfigError('paths: cartelle devono essere distinte e non annidate')
        paths.append(resolved)
    return cfg


class SizeRotatingHandler(RotatingFileHandler):
    """RotatingFileHandler standard non tronca con backupCount=0."""
    def doRollover(self):
        if self.backupCount:
            return super().doRollover()
        if self.stream:
            self.stream.close()
        self.stream = open(self.baseFilename, 'w', encoding=self.encoding)


def setup_logging(cfg, log_dir):
    logger = logging.getLogger('soPDF')
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(cfg['level'])
    logger.propagate = False
    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s', '%Y-%m-%d %H:%M:%S')
    handlers = [logging.StreamHandler(), SizeRotatingHandler(
        log_dir / cfg['fileName'], maxBytes=int(cfg['maxSizeMB'] * 1024 * 1024),
        backupCount=cfg['backupCount'], encoding='utf-8')]
    for handler in handlers:
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def atomic_json(path, data):
    pending = path.with_suffix('.pending')
    with pending.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(pending, path)


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def remove_readonly(function, path, exc_info):
    """Callback rmtree compatibile Python 3.9+: riprova solo accessi negati."""
    if not isinstance(exc_info[1], PermissionError):
        raise exc_info[1]
    os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
    function(path)


def cleanup_job(job, logger):
    """Pulisce un job done. Stato rimosso per ultimo, errori non fatali."""
    folder = job.folder
    state_path = folder / 'job.json'
    try:
        for child in folder.iterdir():
            if child == state_path:
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, onerror=remove_readonly)
            else:
                try:
                    child.unlink()
                except PermissionError:
                    child.chmod(child.stat().st_mode | stat.S_IWUSR)
                    child.unlink()
        state_path.unlink()
        try:
            folder.rmdir()
        except OSError:
            # Anche se fallisce l'ultimo passo, la ripresa deve vedere phase=done.
            job.save()
            raise
        logger.info('CLEANUP | job=%s | completata', folder.name)
        return True
    except OSError as exc:
        logger.warning('CLEANUP | job=%s | job completato; pulizia da riprovare: %s', folder.name, exc)
        return False


def pid_alive(pid):
    if type(pid) is not int or pid <= 0:
        return True  # Stato sconosciuto: non rubare il lock.
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if handle:
            kernel.CloseHandle(handle)
            return True
        return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: PID assente.
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


@contextmanager
def instance_lock(path, stale_minutes, logger):
    token = uuid.uuid4().hex
    record = {'pid': os.getpid(), 'host': socket.gethostname(), 'token': token, 'created': time.time()}
    try:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(record, stream)
    except FileExistsError:
        try:
            old = json.loads(path.read_text(encoding='utf-8'))
            expired = time.time() - path.stat().st_mtime >= stale_minutes * 60
            recoverable = (isinstance(old, dict) and old.get('host') == socket.gethostname()
                           and type(old.get('pid')) is int and old['pid'] > 0
                           and expired and not pid_alive(old['pid']))
        except (OSError, ValueError):
            recoverable = False
        if not recoverable:
            raise LockError(f'Lock attivo o non verificabile: {path}')
        # Rinomina esclusiva di recupero: una sola istanza può effettuare il takeover.
        recovery = path.with_name('.lock.recovery')
        try:
            with recovery.open('x'):
                pass
        except FileExistsError as exc:
            raise LockError('Recupero lock già in corso; verificare .lock.recovery') from exc
        try:
            current = json.loads(path.read_text(encoding='utf-8'))
            if current != old:
                raise LockError('Lock cambiato durante il recupero')
            path.unlink()
            try:
                with path.open('x', encoding='utf-8') as stream:
                    json.dump(record, stream)
            except FileExistsError as exc:
                raise LockError('Altra istanza ha acquisito il lock') from exc
            logger.warning('LOCK | recuperato lock di PID terminato %s', old['pid'])
        finally:
            recovery.unlink(missing_ok=True)
    try:
        yield
    finally:
        try:
            if json.loads(path.read_text(encoding='utf-8')).get('token') == token:
                path.unlink()
        except (OSError, ValueError, AttributeError):
            logger.warning('LOCK | impossibile rilasciare/verificare %s', path)


def safe_stem(value):
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', '_', value).strip().rstrip('. ')
    if not value or value in ('.', '..'):
        raise PageError('Barcode non utilizzabile come nome file')
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', value, re.I):
        value = '_' + value
    # Limite anche in byte per filesystem Linux (255 byte per componente).
    while len(value.encode('utf-8')) > 160:
        value = value[:-1]
    return value


def stable_file(path, seconds):
    try:
        before = path.stat()
        if time.time() - before.st_mtime < seconds:
            return False
        if seconds:
            time.sleep(seconds)
        after = path.stat()
        return before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns
    except OSError:
        return False


def split_pages(source, folder):
    folder.mkdir(exist_ok=True)
    with PDFIUM_LOCK, pdfium.PdfDocument(source) as document:
        count = len(document)
        if not count:
            raise ValueError('PDF senza pagine')
        for index in range(count):
            target = folder / f'{index + 1}.pdf'
            pending = folder / f'{index + 1}.split'
            with pdfium.PdfDocument.new() as single:
                single.import_pages(document, [index])
                single.save(pending)
            os.replace(pending, target)
    return count


def rotate_page_180(source, target):
    """Salva una copia con /Rotate relativo all'orientamento corrente, senza raster."""
    with PDFIUM_LOCK, pdfium.PdfDocument(source) as document:
        page = document[0]
        try:
            page.set_rotation((page.get_rotation() + 180) % 360)
        finally:
            page.close()
        document.save(target)


def compress_page(source, target, cfg, logger, context):
    """Restituisce source oppure target valido. Errori raster = fallback originale."""
    if not cfg['enabled']:
        return source
    image = None
    try:
        with PDFIUM_LOCK, pdfium.PdfDocument(source) as document:
            page = document[0]
            try:
                if cfg['skipIfHasText']:
                    text = page.get_textpage()
                    try:
                        if text.get_text_range().strip():
                            logger.info('COMPRESS | %s | testo nativo: originale conservato', context)
                            return source
                    finally:
                        text.close()
                bitmap = page.render(scale=cfg['dpi'] / 72)
                try:
                    image = bitmap.to_pil().convert('RGB')
                finally:
                    bitmap.close()
            finally:
                page.close()
        if cfg['grayscale']:
            gray = image.convert('L')
            image.close()
            image = gray
        image.save(target, format='PDF', resolution=cfg['dpi'], quality=cfg['jpegQuality'])
        with PDFIUM_LOCK, pdfium.PdfDocument(target) as check:
            if len(check) != 1:
                raise ValueError('PDF compresso non valido')
        old_size, new_size = source.stat().st_size, target.stat().st_size
        gain = 100 * (old_size - new_size) / max(1, old_size)
        if gain < cfg['minGainPercent'] or (cfg['keepOriginalIfLarger'] and new_size >= old_size):
            logger.info('COMPRESS | %s | risparmio %.1f%% insufficiente; originale conservato', context, gain)
            return source
        logger.info('COMPRESS | %s | bytes=%s->%s risparmio=%.1f%% dpi=%s q=%s',
                    context, old_size, new_size, gain, cfg['dpi'], cfg['jpegQuality'])
        return target
    except Exception as exc:
        logger.warning('COMPRESS | %s | fallback originale: %s', context, exc)
        return source
    finally:
        if image is not None:
            image.close()


def tail(value, length=4000):
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='replace')
    return str(value or '')[-length:].replace('\r', '\\r').replace('\n', '\\n')


def extract_barcode(source, result_path, cfg, logger, context):
    settings = cfg['extractor']
    main = (ROOT / (settings['mainPy'] or 'main.py')).resolve()
    interpreter = settings['python'] or sys.executable
    if settings['python'] and ('/' in interpreter or '\\' in interpreter):
        interpreter = str((ROOT / interpreter).resolve())
    result_path.unlink(missing_ok=True)  # Mai leggere un JSON lasciato da tentativi precedenti.
    command = [interpreter, str(main), '-customer', settings['customer'],
               '-template', settings['template'], *settings['extraArgs'],
               '-outputTemplatePath', str(result_path), '-eo', str(source)]
    logger.info('EXTRACT | %s | avvio timeout=%ss', context, settings['timeoutSec'])
    try:
        completed = subprocess.run(command, cwd=main.parent, capture_output=True,
                                   text=True, encoding='utf-8', errors='replace',
                                   timeout=settings['timeoutSec'], check=False)
    except subprocess.TimeoutExpired as exc:
        raise PageError(f'timeout={settings["timeoutSec"]}s stdout={tail(exc.stdout)} stderr={tail(exc.stderr)}') from exc
    except OSError as exc:
        raise PageError(f'avvio lettore fallito: {exc}') from exc
    output = f'exit={completed.returncode} stdout={tail(completed.stdout)} stderr={tail(completed.stderr)}'
    if completed.returncode:
        raise PageError(output)
    try:
        data = json.loads(result_path.read_text(encoding='utf-8-sig'))
        value = data.get(cfg['barcode']['jsonField']) if isinstance(data, dict) else None
        if not isinstance(value, str):
            raise ValueError('barcode assente o non stringa')
        value = value.strip()
        if not value or value in cfg['barcode']['invalidValues']:
            raise ValueError(f'barcode non valido: {value!r}')
        pattern = cfg['barcode'].get('validationRegex', '')
        if pattern.strip() and re.fullmatch(pattern, value) is None:
            raise ValueError(f'barcode non conforme alla regex: {value!r}')
        name = safe_stem(value)
    except (OSError, ValueError) as exc:
        raise PageError(f'{exc}; {output}') from exc
    logger.info('EXTRACT | %s | barcode=%s', context, value)
    return name


class Job:
    def __init__(self, folder, state):
        self.folder = folder
        self.state = state
        self.lock = threading.RLock()

    def save(self):
        atomic_json(self.folder / 'job.json', self.state)

    def publish(self, source, directory, stem, record, logger, overwrite=False):
        """Intento persistente, staging e link esclusivo oppure replace per gli OK piatti."""
        directory.mkdir(parents=True, exist_ok=True)
        with self.lock:
            intent = record.get('intent')
            if intent:
                previous = Path(intent['path'])
                if previous.exists():
                    previous_hash = sha256(previous)
                    if previous_hash != intent['sha256'] and not overwrite:
                        raise OSError(f'Output modificato dopo interruzione: {previous}')
                    exact_name = any(p.name == previous.name for p in directory.iterdir())
                    if previous_hash == intent['sha256'] and (not overwrite or exact_name):
                        record.update(status='done', destination=str(previous))
                        self.save()
                        return previous
                    logger.warning('RESUME | job=%s | ripubblico output o completo cambio nome: %s',
                                   self.folder.name, previous)
            digest = sha256(source)
            # Staging sullo stesso filesystem della destinazione, nessun file parziale .pdf.
            fd, name = tempfile.mkstemp(prefix='.split-', suffix='.tmp', dir=directory)
            staged = Path(name)
            try:
                with os.fdopen(fd, 'wb') as out, source.open('rb') as incoming:
                    shutil.copyfileobj(incoming, out)
                    out.flush()
                    os.fsync(out.fileno())
                if overwrite:
                    candidate = directory / f'{stem}.pdf'
                    record['intent'] = {'path': str(candidate), 'sha256': digest}
                    record['status'] = 'publishing'
                    self.save()
                    matches = [p for p in directory.iterdir()
                               if p.name.casefold() == candidate.name.casefold()]
                    # Su Linux due varianti case possono preesistere: non eliminarle
                    # prima che il nuovo contenuto sia pubblicato con successo.
                    for old in matches:
                        if not old.is_file() or old.is_symlink():
                            raise OSError(f'Destinazione non sicura: {old}')
                        logger.warning('MOVE | job=%s | SOVRASCRITTURA %s sha256=%s -> %s',
                                       self.folder.name, old, sha256(old), digest)
                    target = matches[0] if matches else candidate
                    os.replace(staged, target)
                    if target.name != candidate.name:
                        os.replace(target, candidate)
                    for old in matches:
                        if old.name != candidate.name and old.exists():
                            # Non cancellare il nuovo file su filesystem case-insensitive.
                            if not os.path.samefile(old, candidate):
                                old.unlink()
                    record.update(status='done', destination=str(candidate))
                    self.save()
                    return candidate
                index = 0
                while True:
                    candidate = directory / f'{stem}{"_" + str(index) if index else ""}.pdf'
                    # Se già esiste, nessun intento registrato per il file di un altro job.
                    if any(p.name.casefold() == candidate.name.casefold() for p in directory.iterdir()):
                        index += 1
                        continue
                    record['intent'] = {'path': str(candidate), 'sha256': digest}
                    record['status'] = 'publishing'
                    self.save()
                    try:
                        os.link(staged, candidate)
                        break
                    except FileExistsError:
                        index += 1
                record.update(status='done', destination=str(candidate))
                self.save()
                if index:
                    logger.warning('MOVE | job=%s | collisione: %s', self.folder.name, candidate.name)
                return candidate
            finally:
                staged.unlink(missing_ok=True)


def notify_errors(cfg, logger, job_id, origin, pages, attachments):
    """Predisposizione soltanto. Nessuna rete, nessuna email inviata."""
    if cfg['sendOnError']:
        logger.warning('EMAIL | job=%s | invio NON implementato | file=%s pagine=%s allegati=%s',
                       job_id, origin, pages, [str(p) for p in attachments])
    else:
        logger.info('EMAIL | job=%s | sendOnError=false; nessun invio | pagine=%s', job_id, pages)


class Runner:
    def __init__(self, cfg, logger):
        self.cfg = cfg
        self.logger = logger
        self.paths = {key: (ROOT / value).resolve() for key, value in cfg['paths'].items()}
        self.totals = {'jobs': 0, 'ok': 0, 'ko': 0, 'failed': 0, 'savedBytes': 0}

    def reserve_name(self, stem):
        """Chiamata sequenziale sotto instance_lock; mkdir riserva il nome su disco."""
        occupied = {p.name.casefold() for key in ('out_pdf', 'err_pdf', 'worked_pdf')
                    for p in self.paths[key].iterdir()}
        for state_path in self.paths['tempDir'].glob('*/job.json'):
            try:
                state = json.loads(state_path.read_text(encoding='utf-8'))
                if isinstance(state.get('outputName'), str):
                    occupied.add(state['outputName'].casefold())
            except (OSError, ValueError, AttributeError):
                # La prenotazione su worked_pdf resta; il job danneggiato è segnalato da run.
                self.logger.warning('RESERVE | stato non leggibile: %s', state_path)
        base = safe_stem(stem) + datetime.now().strftime('_%d%m%y')
        index = 0
        while True:
            name = base + (f'_{index}' if index else '')
            if name.casefold() not in occupied:
                try:
                    (self.paths['worked_pdf'] / name).mkdir()
                    return name
                except FileExistsError:
                    occupied.add(name.casefold())
            index += 1

    def flat_out(self, job, record):
        return job.state.get('outDirSeparation') is False and record.get('kind') == 'ok'

    def job_directory(self, job, key):
        """I job preesistenti senza outputName mantengono tutte le destinazioni legacy."""
        if key == 'out_pdf' and job.state.get('outDirSeparation') is False:
            return self.paths[key]
        if 'outputName' in job.state:
            name = job.state['outputName']
            if (not isinstance(name, str) or name in ('', '.', '..')
                    or re.search(r'[\\/:*?"<>|\x00-\x1f]', name)):
                raise ValueError('outputName non valido')
            return self.paths[key] / name
        return self.paths[key] if key == 'out_pdf' else self.paths[key] / job.folder.name

    def create_job(self, source):
        job_id = datetime.now().strftime('%Y%m%d_%H%M%S_') + safe_stem(source.stem)[:40] + '_' + uuid.uuid4().hex
        folder = self.paths['tempDir'] / job_id
        folder.mkdir()
        job = Job(folder, {'version': 1, 'origin': source.name, 'phase': 'pickup',
                          'sourceHash': sha256(source),
                          'outputName': self.reserve_name(source.stem),
                          'outDirSeparation': self.cfg['out_pdf_dir_separation'],
                          'count': 0, 'pages': {}, 'archive': {}})
        job.save()
        try:
            shutil.move(str(source), str(folder / source.name))
            if os.name == 'nt':
                picked = folder / source.name
                picked.chmod(picked.stat().st_mode | stat.S_IWUSR)
        except Exception:
            # Non rimuovere nulla: una copia cross-filesystem può essere incompleta.
            self.logger.exception('PICKUP | job=%s | presa in carico fallita; verifica manuale', job_id)
            raise
        job.state['phase'] = 'split'
        job.save()
        self.logger.info('DESTINATION | job=%s | outputName=%s', job_id, job.state['outputName'])
        self.logger.info('PICKUP | job=%s | file=%s bytes=%s sha256=%s',
                         job_id, source.name, (folder / source.name).stat().st_size,
                         sha256(folder / source.name))
        return job

    def process_page(self, job, number):
        context = f'job={job.folder.name} p{number}'
        source = job.folder / 'pages' / f'{number}.pdf'
        with job.lock:
            record = job.state['pages'].setdefault(str(number), {})
            if record.get('status') == 'done':
                self.logger.info('RESUME | %s | già completata', context)
                return
            if record.get('intent'):
                # Riprende la pubblicazione con lo stesso file e la stessa destinazione.
                prepared = Path(record['prepared'])
                target = Path(record['intent']['path'])
                destination = job.publish(prepared, target.parent, record['stem'], record, self.logger,
                                          overwrite=self.flat_out(job, record))
                self.logger.info('MOVE | %s | ripresa -> %s', context, destination)
                return
        rotated = False
        try:
            try:
                stem = extract_barcode(source, source.with_suffix('.json'), self.cfg, self.logger, context)
            except PageError as first_error:
                if not self.cfg['barcode']['rotationOnFailureReading']:
                    raise
                self.logger.warning('ROTATE | %s | prima lettura fallita: %s; riprova a 180°',
                                    context, first_error)
                rotated_source = source.with_suffix('.rot180.pdf')
                rotate_page_180(source, rotated_source)
                try:
                    stem = extract_barcode(rotated_source, rotated_source.with_suffix('.json'),
                                           self.cfg, self.logger, context)
                except PageError as second_error:
                    raise PageError(f'prima lettura: {first_error}; lettura a 180°: {second_error}') from second_error
                source = rotated_source
                rotated = True
                self.logger.info('ROTATE | %s | barcode letto dopo rotazione di 180°', context)
        except PageError as exc:
            self.logger.error('EXTRACT | %s | %s', context, exc)
            kind, prepared = 'ko', source
            stem = job.state.get('outputName', safe_stem(Path(job.state['origin']).stem)) + f'_p{number}'
            directory = self.job_directory(job, 'err_pdf')
            reason = str(exc)
        else:
            kind, reason = 'ok', ''
            prepared = compress_page(source, source.with_suffix('.compressed'),
                                     self.cfg['compression'], self.logger, context)
            directory = self.job_directory(job, 'out_pdf')
        with job.lock:
            record.update(kind=kind, reason=reason, prepared=str(prepared), stem=stem, rotated=rotated,
                          savedBytes=source.stat().st_size - prepared.stat().st_size)
            job.save()
            destination = job.publish(prepared, directory, stem, record, self.logger,
                                      overwrite=self.flat_out(job, record))
        self.logger.info('MOVE | %s | %s -> %s', context, kind.upper(), destination)

    def process_job(self, job):
        state, folder = job.state, job.folder
        source = folder / state['origin']
        if state['phase'] == 'done':
            if not self.cfg['keepTemp']:
                cleanup_job(job, self.logger)
            return
        if state['phase'] == 'pickup':
            # Stato salvato prima dello spostamento: se l'origine è presente il pickup è riuscito.
            if not source.is_file() or sha256(source) != state.get('sourceHash'):
                raise OSError(f'Pickup incompleto: verificare manualmente {folder}')
            state['phase'] = 'split'
            job.save()
        if state['phase'] == 'split':
            try:
                state['count'] = split_pages(source, folder / 'pages')
            except pdfium.PdfiumError as exc:
                self.logger.error('SPLIT | job=%s | PDF non leggibile: %s', folder.name, exc)
                state['phase'], state['splitError'] = 'archive_error', str(exc)
                job.save()
            except ValueError as exc:
                self.logger.error('SPLIT | job=%s | %s', folder.name, exc)
                state['phase'], state['splitError'] = 'archive_error', str(exc)
                job.save()
            else:
                state['phase'] = 'pages'
                job.save()
                self.logger.info('SPLIT | job=%s | %s pagine', folder.name, state['count'])
        if state['phase'] == 'pages':
            for record in state['pages'].values():
                if record.get('status') == 'done':
                    destination = Path(record['destination'])
                    if self.flat_out(job, record) and not destination.is_file() and destination.parent.is_dir():
                        destination = next((p for p in destination.parent.iterdir()
                                            if p.name.casefold() == destination.name.casefold()), destination)
                    if (not destination.is_file() or
                            (not self.flat_out(job, record) and sha256(destination) != record['intent']['sha256'])):
                        raise OSError(f'Output completato assente o modificato: {destination}')
            pending = [n for n in range(1, state['count'] + 1)
                       if state['pages'].get(str(n), {}).get('status') != 'done']
            workers = self.cfg['parallel']['maxWorkers']
            if state.get('outDirSeparation') is False and workers > 1:
                self.logger.warning('OVERWRITE | job=%s | workers=%s: ordine sovrascritture non garantito; '
                                    'usare maxWorkers 0 o 1 per ordine sequenziale', folder.name, workers)
            if workers == 1:
                for number in pending:
                    self.process_page(job, number)
            else:
                with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='barcode') as pool:
                    futures = [pool.submit(self.process_page, job, number) for number in pending]
                    for future in as_completed(futures):
                        future.result()
            state['phase'] = 'archive'
            job.save()
        if state['phase'] in ('archive', 'archive_error'):
            failed = state['phase'] == 'archive_error'
            bad = sorted(int(n) for n, record in state['pages'].items() if record.get('kind') == 'ko')
            if bad or failed:
                notify_errors(self.cfg['email'], self.logger, folder.name, state['origin'], bad,
                              [Path(state['pages'][str(n)]['destination']) for n in bad])
            directory = self.job_directory(job, 'err_pdf' if failed else 'worked_pdf')
            stem = state.get('outputName', Path(state['origin']).stem)
            destination = job.publish(source, directory, stem, state['archive'], self.logger)
            self.logger.info('ARCHIVE | job=%s | -> %s', folder.name, destination)
            state['phase'] = 'done'
            job.save()
            self.totals['jobs'] += 1
            self.totals['failed'] += int(failed)
            for record in state['pages'].values():
                self.totals[record['kind']] += 1
                self.totals['savedBytes'] += record['savedBytes']
            if not self.cfg['keepTemp']:
                cleanup_job(job, self.logger)

    def run(self):
        errors = False
        # Snapshot prima anche della ripresa: nuovi arrivi attendono il prossimo cron.
        incoming = sorted((p for p in self.paths['in_pdf'].iterdir()
                           if p.is_file() and not p.is_symlink() and p.suffix.lower() == '.pdf'),
                          key=lambda p: (p.name.casefold(), p.name))
        # Prima la ripresa, poi i nuovi ingressi. Nessun ricalcolo di pagine già smistate.
        for folder in sorted(self.paths['tempDir'].iterdir()):
            if not folder.is_dir():
                continue
            if not (folder / 'job.json').exists():
                self.logger.warning('RESUME | job=%s | cartella orfana senza job.json; '
                                    'nessuna azione, verificare manualmente: %s', folder.name, folder)
                continue
            try:
                state = json.loads((folder / 'job.json').read_text(encoding='utf-8'))
                if (state.get('version') != 1 or not isinstance(state.get('origin'), str)
                        or Path(state['origin']).name != state['origin']
                        or ('outDirSeparation' in state and type(state['outDirSeparation']) is not bool)
                        or state['phase'] not in ('pickup', 'split', 'pages', 'archive', 'archive_error', 'done')):
                    raise ValueError('stato job non valido')
                self.process_job(Job(folder, state))
            except Exception:
                errors = True
                self.logger.exception('RESUME | job=%s | job conservato; intervento/riprova richiesto', folder.name)
        for source in incoming:
            if not stable_file(source, self.cfg['minFileAgeSec']):
                self.logger.info('SKIP | file=%s | non stabile o non disponibile', source.name)
                continue
            try:
                self.process_job(self.create_job(source))
            except Exception:
                errors = True
                self.logger.exception('JOB | file=%s | errore infrastrutturale; conservato per ripresa', source.name)
        self.logger.info('SUMMARY | %s', self.totals)
        return int(errors or self.totals['ko'] > 0 or self.totals['failed'] > 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description='soPDF: split PDF per barcode, una passata su in_pdf.')
    parser.add_argument('-config', '--config', default=str(ROOT / 'soPDF_config.jsonc'))
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f'CONFIG | {exc}', file=sys.stderr)
        return 3
    logger = None
    try:
        paths = {key: (ROOT / value).resolve() for key, value in cfg['paths'].items()}
        for folder in paths.values():
            folder.mkdir(parents=True, exist_ok=True)
        logger = setup_logging(cfg['log'], paths['logDir'])
        with instance_lock(paths['tempDir'] / '.lock', cfg['lockStaleMin'], logger):
            logger.info('START | workers=%s | config=%s', cfg['parallel']['maxWorkers'], args.config)
            return Runner(cfg, logger).run()
    except LockError as exc:
        if logger:
            logger.error('LOCK | %s', exc)
        return 2
    except Exception as exc:
        if logger:
            logger.exception('FATAL | %s', exc)
        else:
            print(f'FATAL | {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())