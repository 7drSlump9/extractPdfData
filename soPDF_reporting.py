"""Report persistente per lancio e riepilogo Brevo. Nessun allegato."""
from datetime import datetime
import html
import json
import os
from pathlib import Path
import string
import threading
import urllib.error
import urllib.request
import uuid


BREVO_URL = 'https://api.brevo.com/v3/smtp/email'
RUN_FIELDS = {'run_start', 'run_end', 'run_duration', 'total_pdfs', 'total_pages',
              'ok', 'ko', 'rotated', 'status', 'error_count', 'files'}
FILE_FIELDS = {'file', 'output_name', 'started', 'finished', 'duration', 'pages',
               'processed', 'ok', 'ko', 'rotated', 'status', 'errors', 'resumed'}
ERROR_FIELDS = {'error_file', 'error_page', 'error_reason'}


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def duration(start, end):
    if not start or not end:
        return None
    return max(0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix('.pending')
    with pending.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(pending, path)


def load_template(path, parse):
    template = parse(Path(path).read_text(encoding='utf-8-sig'))
    allowed = {'subject': RUN_FIELDS - {'files'}, 'subjectOnError': RUN_FIELDS - {'files'},
               'bodyText': RUN_FIELDS, 'bodyHtml': RUN_FIELDS,
               'fileTemplate': FILE_FIELDS, 'errorTemplate': ERROR_FIELDS}
    if not isinstance(template, dict) or set(template) - set(allowed):
        raise ValueError('Template email: struttura o chiavi non valide')
    for required in ('subject', 'bodyText', 'fileTemplate', 'errorTemplate'):
        if not isinstance(template.get(required), str) or not template[required].strip():
            raise ValueError(f'Template email: {required} obbligatorio')
    for key, fields in allowed.items():
        text = template.setdefault(key, '')
        if not isinstance(text, str):
            raise ValueError(f'Template email: {key} deve essere stringa')
        for _, field, spec, conversion in string.Formatter().parse(text):
            if field is not None and (field not in fields or spec or conversion):
                raise ValueError(f'Template email: segnaposto non valido in {key}: {field}')
    if any(c in template['subject'] + template['subjectOnError'] for c in '\r\n'):
        raise ValueError('Template email: oggetto su una sola riga')
    return template


def render_email(reports, template):
    """Dati disponibili al chiamante: context per oggetto/corpo, PDF dentro reports."""
    # In recupero una passata interrotta e quella di ripresa possono citare lo
    # stesso job: nel riepilogo vince l'osservazione più recente, senza doppio conto.
    latest = {}
    for report in sorted(reports, key=lambda r: r['startedAt']):
        for entry in report['pdfs']:
            latest[entry['jobId']] = entry
    documents = list(latest.values())
    starts = [r['startedAt'] for r in reports]
    ends = [r.get('finishedAt') or r['startedAt'] for r in reports]
    context = {'run_start': min(starts), 'run_end': max(ends),
               'run_duration': sum(r.get('durationSec') or 0 for r in reports),
               'total_pdfs': len(documents),
               'total_pages': sum(d['totalPages'] or 0 for d in documents),
               'ok': sum(d['ok'] for d in documents), 'ko': sum(d['ko'] for d in documents),
               'rotated': sum(d['rotated'] for d in documents),
               'error_count': sum(len(d['errors']) for d in documents)}
    context['status'] = 'ERRORI' if context['error_count'] else 'OK'
    rows = []
    for doc in documents:
        errors = '\n'.join(template['errorTemplate'].format(
            error_file=e['file'], error_page=e.get('page') or '-', error_reason=e['reason'])
                           for e in doc['errors']) or 'Nessuno'
        values = {'file': doc['file'], 'output_name': doc['outputName'],
                  'started': doc['startedAt'] or 'non disponibile', 'finished': doc['finishedAt'],
                  'duration': doc['durationSec'] if doc['durationSec'] is not None else 'non disponibile',
                  'pages': doc['totalPages'] if doc['totalPages'] is not None else 'non disponibile',
                  'processed': doc['processedPages'], 'ok': doc['ok'], 'ko': doc['ko'],
                  'rotated': doc['rotated'], 'status': doc['status'], 'errors': errors,
                  'resumed': 'si' if doc['resumed'] else 'no'}
        rows.append(template['fileTemplate'].format(**values))
    context['files'] = '\n\n'.join(rows)
    subject = template['subjectOnError'] if context['error_count'] and template['subjectOnError'] else template['subject']
    rendered = {'subject': subject.format(**context).replace('\r', ' ').replace('\n', ' '),
                'textContent': template['bodyText'].format(**context)}
    if template['bodyHtml']:
        rendered['htmlContent'] = template['bodyHtml'].format(
            **{k: html.escape(str(v)) for k, v in context.items()})
    return rendered, context


def api_key(settings, root):
    name = settings['apiKeyEnvVar']
    if os.environ.get(name):
        return os.environ[name].strip()
    path = root / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            key, sep, value = line.strip().partition('=')
            if sep and key.strip() == name:
                return value.strip().strip('"').strip("'")
    return ''


def send_brevo(settings, rendered, root):
    key = api_key(settings, root)
    if not key:
        raise RuntimeError('Variabile API Brevo assente o vuota')
    payload = dict(rendered, sender=settings['from'], to=[{'email': email} for email in settings['to']])
    request = urllib.request.Request(BREVO_URL, data=json.dumps(payload).encode('utf-8'),
                                     headers={'api-key': key, 'Content-Type': 'application/json',
                                              'Accept': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=settings['timeoutSec']) as response:
            result = json.loads(response.read().decode('utf-8'))
            if response.status != 201 or not isinstance(result.get('messageId'), str):
                raise RuntimeError('Risposta Brevo inattesa')
            return result['messageId']
    except urllib.error.HTTPError as exc:
        # Non mostrare body, header o eccezione completa: potrebbero contenere segreti.
        status = exc.code
        exc.close()
        raise RuntimeError(f'Brevo HTTP {status}') from None
    except Exception as exc:
        raise RuntimeError(f'Invio Brevo non confermato ({type(exc).__name__})') from None


class RunReport:
    def __init__(self, folder, settings, template, root, logger):
        self.folder, self.settings, self.template = folder, settings, template
        self.root, self.logger = root, logger
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.path = folder / f'run_{stamp}_{uuid.uuid4().hex[:12]}.json'
        self.data = {'version': 1, 'runId': self.path.stem, 'startedAt': now(),
                     'finishedAt': None, 'durationSec': None, 'pdfs': [],
                     'email': {'status': 'pending', 'attempts': 0}}
        self.lock = threading.RLock()

    def record(self, entry):
        with self.lock:
            self.data['pdfs'] = [d for d in self.data['pdfs'] if d['jobId'] != entry['jobId']]
            self.data['pdfs'].append(entry)
            self.checkpoint()

    def checkpoint(self):
        _, context = render_email([self.data], self.template)
        self.data['totals'] = {k: context[k] for k in ('total_pdfs', 'total_pages', 'ok', 'ko',
                                                     'rotated', 'error_count', 'status')}
        save_json(self.path, self.data)

    def finish(self):
        if self.data['pdfs']:
            self.data['finishedAt'] = now()
            self.data['durationSec'] = duration(self.data['startedAt'], self.data['finishedAt'])
            self.checkpoint()
        # Anche in una passata vuota si possono ritentare riepiloghi precedenti.
        if not self.settings['enabled'] or self.settings['sendOn'] == 'never':
            if self.data['pdfs']:
                self.data['email']['status'] = 'skipped'
                self.checkpoint()
            return
        pending = []
        for path in sorted(self.folder.glob('run_*.json')):
            try:
                report = json.loads(path.read_text(encoding='utf-8'))
                if report['email']['status'] not in ('pending', 'failed'):
                    continue
                if not report.get('pdfs'):
                    continue
                if report['email'].get('expired'):
                    continue
                if not report.get('finishedAt') and path != self.path:
                    # Report lasciato da un arresto: segnala esplicitamente gli incompleti.
                    for doc in report['pdfs']:
                        if doc['status'] == 'in corso':
                            doc['status'] = 'interrotto'
                            doc['errors'].append({'file': doc['file'], 'page': None,
                                                  'reason': 'Processo interrotto prima del completamento'})
                    report['totals']['error_count'] = sum(len(d['errors']) for d in report['pdfs'])
                age = duration(report['startedAt'], now()) / 86400
                if age > self.settings['retryDays']:
                    report['email'].update(status='failed', expired=True)
                    save_json(path, report)
                    continue
                if self.settings['sendOn'] == 'error' and not report['totals']['error_count']:
                    report['email']['status'] = 'skipped'
                    save_json(path, report)
                    continue
                pending.append((path, report))
            except (OSError, ValueError, KeyError, TypeError):
                self.logger.warning('REPORT | report precedente non leggibile: %s', path.name)
        if not pending:
            return
        rendered, _ = render_email([r for _, r in pending], self.template)
        for path, report in pending:
            report['email'].update(attempts=report['email'].get('attempts', 0) + 1,
                                   lastAttemptAt=now(), status='pending')
            save_json(path, report)
        try:
            message_id = send_brevo(self.settings, rendered, self.root)
        except Exception as exc:
            # Non loggare str(exc): anche un errore inatteso potrebbe contenere la chiave.
            self.logger.warning('EMAIL | invio non confermato (%s); report conservati per retry', type(exc).__name__)
            email_result = {'status': 'failed', 'error': type(exc).__name__}
        else:
            self.logger.info('EMAIL | riepilogo accettato da Brevo | messageId=%s', message_id)
            email_result = {'status': 'sent', 'messageId': message_id, 'sentAt': now()}
        for path, report in pending:
            report['email'].update(email_result)
            if email_result['status'] == 'sent':
                report['email'].pop('error', None)
            save_json(path, report)