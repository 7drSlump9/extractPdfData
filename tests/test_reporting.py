"""Test report/email isolati: nessuna rete, nessuna chiave reale."""
import copy
import io
import json
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import urllib.error

import soPDF as split
import soPDF_reporting as reporting


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = copy.deepcopy(split.DEFAULTS['email'])
        self.settings['enabled'] = True
        self.template = reporting.load_template(split.ROOT / 'soPDF_email.jsonc', split.parse_jsonc)
        self.logger = logging.getLogger(f'report-test-{id(self)}')
        self.logger.addHandler(logging.NullHandler())

    def run_report(self):
        return reporting.RunReport(self.root / 'reports', self.settings, self.template, self.root, self.logger)

    def entry(self, name='A.pdf', failed=False):
        return {'jobId': name, 'file': name, 'outputName': name + '_101126',
                'startedAt': reporting.now(), 'finishedAt': reporting.now(), 'durationSec': 2,
                'totalPages': 3, 'processedPages': 3, 'ok': 2 if failed else 3,
                'ko': int(failed), 'rotated': 1, 'resumed': False,
                'status': 'parziale' if failed else 'ok',
                'errors': [{'file': 'A_101126_p3.pdf', 'page': 3, 'reason': '<non letto>',
                            'reasonShort': 'Barcode <non letto>'}] if failed else []}

    def test_one_report_one_email_three_pdfs(self):
        report = self.run_report()
        for name in ('A.pdf', 'B.pdf', 'C.pdf'):
            report.record(self.entry(name, name == 'B.pdf'))
        with patch.object(reporting, 'send_brevo', return_value='test-message') as sender:
            report.finish()
        sender.assert_called_once()
        stored = json.loads(report.path.read_text(encoding='utf-8'))
        self.assertEqual(stored['totals']['total_pdfs'], 3)
        self.assertEqual(stored['totals']['total_pages'], 9)
        self.assertEqual(stored['totals']['ok'], 8)
        self.assertEqual(stored['totals']['ko'], 1)
        self.assertEqual(stored['email']['status'], 'sent')
        self.assertIn('A_101126_p3.pdf', sender.call_args.args[1]['textContent'])
        self.assertIn('&lt;non letto&gt;', sender.call_args.args[1]['htmlContent'])
        self.assertEqual(len(list(report.folder.glob('*.json'))), 1)

    def test_empty_run_no_report_no_email(self):
        report = self.run_report()
        with patch.object(reporting, 'send_brevo') as sender:
            report.finish()
        sender.assert_not_called()
        self.assertFalse(report.path.exists())

    def test_failure_and_retry_single_email(self):
        report = self.run_report()
        report.record(self.entry())
        with patch.object(reporting, 'send_brevo', side_effect=RuntimeError('SECRET-DO-NOT-LOG')), \
                self.assertLogs(self.logger, logging.WARNING) as logs:
            report.finish()
        self.assertNotIn('SECRET-DO-NOT-LOG', '\n'.join(logs.output))
        self.assertNotIn('SECRET-DO-NOT-LOG', report.path.read_text())
        self.assertEqual(json.loads(report.path.read_text())['email']['status'], 'failed')
        next_run = self.run_report()
        next_run.record(self.entry('B.pdf'))
        with patch.object(reporting, 'send_brevo', return_value='retry-id') as sender:
            next_run.finish()
        sender.assert_called_once()
        self.assertIn('A.pdf', sender.call_args.args[1]['textContent'])
        self.assertIn('B.pdf', sender.call_args.args[1]['textContent'])
        self.assertEqual(json.loads(report.path.read_text())['email']['attempts'], 2)
        self.assertEqual(json.loads(report.path.read_text())['email']['status'], 'sent')

    def test_send_on_error_and_never(self):
        for mode, failed, expected in [('error', False, False), ('error', True, True), ('never', True, False)]:
            with self.subTest(mode=mode, failed=failed):
                self.settings['sendOn'] = mode
                report = self.run_report()
                report.record(self.entry(failed=failed))
                with patch.object(reporting, 'send_brevo', return_value='id') as sender:
                    report.finish()
                self.assertEqual(sender.called, expected)

    def test_expired_report_is_not_retried(self):
        report = self.run_report()
        report.data['startedAt'] = '2020-01-01T00:00:00+00:00'
        report.record(self.entry())
        with patch.object(reporting, 'send_brevo') as sender:
            report.finish()
        sender.assert_not_called()
        self.assertTrue(json.loads(report.path.read_text())['email']['expired'])

    def test_template_validation_and_custom_subject(self):
        for text in ('{unknown}', '{total_pdfs.__class__}', '{total_pdfs!r}', '{total_pdfs:03d}'):
            template = dict(self.template, subject=text)
            path = self.root / 'bad.jsonc'
            path.write_text(json.dumps(template), encoding='utf-8')
            with self.subTest(text=text), self.assertRaises(ValueError):
                reporting.load_template(path, split.parse_jsonc)
        self.template['subject'] = 'Personalizzato: {total_pdfs} documenti'
        report = self.run_report()
        report.record(self.entry())
        rendered, data = reporting.render_email([report.data], self.template)
        self.assertEqual(rendered['subject'], 'Personalizzato: 1 documenti')
        self.assertEqual(data['total_pdfs'], 1)

    def test_http_payload_and_dotenv_no_key_leak(self):
        (self.root / '.env').write_text('BREVO_API_KEY="fake-key"\n')
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 201
        response.read.return_value = b'{"messageId":"test-id"}'
        with patch.dict('os.environ', {}, clear=True), \
                patch.object(reporting.urllib.request, 'urlopen', return_value=response) as http:
            result = reporting.send_brevo(self.settings, {'subject': 'Test', 'textContent': 'Corpo'}, self.root)
        self.assertEqual(result, 'test-id')
        req = http.call_args.args[0]
        self.assertEqual(req.full_url, reporting.BREVO_URL)
        self.assertEqual(req.get_method(), 'POST')
        self.assertEqual(req.get_header('Api-key'), 'fake-key')
        body = json.loads(req.data)
        self.assertEqual(body['sender']['email'], 'consegne@sogesispa.it')
        self.assertEqual(body['to'], [{'email': 'd.cimarelli@keyfor.it'}])
        self.assertNotIn('attachment', body)

    def test_missing_key_and_http_error_redacted(self):
        with patch.dict('os.environ', {}, clear=True), self.assertRaises(RuntimeError):
            reporting.send_brevo(self.settings, {}, self.root)
        error = urllib.error.HTTPError(reporting.BREVO_URL, 401, 'fake-secret', {}, io.BytesIO(b'fake-secret'))
        with patch.dict('os.environ', {'BREVO_API_KEY': 'fake-secret'}, clear=True), \
                patch.object(reporting.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                reporting.send_brevo(self.settings, {}, self.root)
        self.assertNotIn('fake-secret', str(caught.exception))
        self.assertIn('401', str(caught.exception))

    def test_recovered_job_not_counted_twice(self):
        old = self.run_report()
        old.record(self.entry())
        new = self.run_report()
        new.record(dict(self.entry(), resumed=True))
        _, context = reporting.render_email([old.data, new.data], self.template)
        self.assertEqual(context['total_pdfs'], 1)

    def test_legacy_verbose_errors_one_row_per_page_and_clean_in_both_bodies(self):
        report = self.run_report()
        doc = self.entry('ptv_ruotata.pdf', True)
        detail = (r'prima lettura: exit=1 stdout=C:\Users\utente\temp_split\23.pdf '
                  r'\n======= ORDINE ESTRATTO barcode_top: N/A stderr=; lettura a 180°: exit=1 stdout=')
        doc['errors'] = [{'file': f'ptv_ruotata_101126_p{n}.pdf', 'page': n, 'reason': detail}
                         for n in (23, 24, 25)]
        report.record(doc)
        rendered, _ = reporting.render_email([report.data], self.template)
        for body in (rendered['textContent'], rendered['htmlContent']):
            self.assertNotIn('23, 24, 25', body)
            for number in (23, 24, 25):
                self.assertIn(f'ptv_ruotata_101126_p{number}.pdf', body)
            self.assertIn('Barcode non letto (anche dopo rotazione 180°)', body)
            self.assertNotIn('stdout', body)
            self.assertNotIn('stderr', body)
            self.assertNotIn('C:\\Users', body)
            self.assertNotIn('=======', body)
        self.assertIn('stdout', json.loads(report.path.read_text())['pdfs'][0]['errors'][0]['reason'])
        self.assertIn('<table', rendered['htmlContent'])
        self.assertIn('FOGLI TOTALI', rendered['htmlContent'])
        self.assertIn('FOGLI OK', rendered['htmlContent'])
        self.assertIn('FOGLI KO', rendered['htmlContent'])
        self.assertNotIn('<pre>', rendered['htmlContent'])
        for number in (23, 24, 25):
            self.assertIn(f'Foglio {number}:', rendered['textContent'])
            row = next(row for row in rendered['htmlContent'].split('<tr')
                       if f'ptv_ruotata_101126_p{number}.pdf' in row)
            self.assertIn(f'>{number}</td>', row)
            self.assertIn('Barcode non letto', row)

    def test_html_escapes_filename_and_error_and_sorts_errors_first(self):
        report = self.run_report()
        report.record(self.entry('A_OK.pdf'))
        report.record(self.entry('<img src=x onerror=alert(1)>.pdf', True))
        rendered, _ = reporting.render_email([report.data], self.template)
        body = rendered['htmlContent']
        self.assertNotIn('<img', body)
        self.assertIn('&lt;img', body)
        self.assertIn('&lt;non letto&gt;', body)
        self.assertLess(body.index('&lt;img'), body.index('A_OK.pdf'))

    def test_no_error_section_and_readable_dates(self):
        report = self.run_report()
        doc = self.entry()
        doc['startedAt'] = '2026-11-10T17:30:09+01:00'
        report.record(doc)
        rendered, _ = reporting.render_email([report.data], self.template)
        self.assertIn('Nessun errore', rendered['htmlContent'])
        self.assertNotIn('intervento richiesto', rendered['htmlContent'])
        self.assertIn('10/11/2026 17:30:09', rendered['textContent'])

    def test_many_errors_limited_without_truncating_report(self):
        report = self.run_report()
        doc = self.entry(failed=True)
        doc['errors'] = [{'file': f'file_p{n}.pdf', 'page': n, 'reason': 'N/A'} for n in range(1, 121)]
        report.record(doc)
        rendered, _ = reporting.render_email([report.data], self.template)
        self.assertIn('altri 20', rendered['htmlContent'])
        self.assertNotIn('file_p101.pdf', rendered['htmlContent'])
        self.assertEqual(len(report.data['pdfs'][0]['errors']), 120)

    def test_external_html_validated_and_resolved_relative_to_jsonc(self):
        template = dict(self.template, bodyHtmlFile='layout.html')
        (self.root / 'layout.html').write_text('<p>{invalid}</p>', encoding='utf-8')
        path = self.root / 'template.jsonc'
        path.write_text(json.dumps(template), encoding='utf-8')
        with self.assertRaises(ValueError):
            reporting.load_template(path, split.parse_jsonc)
        (self.root / 'layout.html').write_text('<p>{ok}</p>', encoding='utf-8')
        self.assertEqual(reporting.load_template(path, split.parse_jsonc)['bodyHtml'], '<p>{ok}</p>')

    def test_pdf_blocks_have_individual_sorted_rows_and_compact_ok(self):
        report = self.run_report()
        failed = self.entry('B_errori.pdf', True)
        failed['errors'] = [
            {'file': 'pagina_24.pdf', 'page': 24, 'reasonShort': 'Lettore in timeout (120 s)'},
            {'file': 'pagina_3.pdf', 'page': 3, 'reasonShort': 'Barcode non letto'},
            {'file': 'pagina_18.pdf', 'page': 18, 'reasonShort': 'Barcode non conforme alle regole configurate'},
            {'file': 'documento.pdf', 'page': None, 'reasonShort': 'PDF non leggibile'},
        ]
        report.record(self.entry('A_ok.pdf'))
        report.record(failed)
        message, _ = reporting.render_email([report.data], self.template)
        body = message['htmlContent']
        self.assertEqual(body.count('B_errori.pdf'), 1)
        self.assertEqual(body.count('A_ok.pdf'), 1)
        self.assertLess(body.index('B_errori.pdf'), body.index('A_ok.pdf'))
        positions = [body.index(name) for name in ('pagina_3.pdf', 'pagina_18.pdf', 'pagina_24.pdf', 'documento.pdf')]
        self.assertEqual(positions, sorted(positions))
        for filename, reason in (('pagina_24.pdf', 'Lettore in timeout'),
                                 ('pagina_3.pdf', 'Barcode non letto'),
                                 ('pagina_18.pdf', 'Barcode non conforme')):
            row = next(row for row in body.split('<tr') if filename in row)
            self.assertIn(reason, row)
        self.assertIn('>Documento</td>', body)
        self.assertIn('Nessun errore', body)
        self.assertNotIn('Tutti i PDF elaborati', body)
        for color in ('#1f2d3d', '#2f6b4f', '#8c2f39', '#d9dee5', '#f6f8fa'):
            self.assertIn(color, body)


if __name__ == '__main__':
    unittest.main()