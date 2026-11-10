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
                'errors': [{'file': 'A_101126_p3.pdf', 'page': 3, 'reason': '<non letto>'}] if failed else []}

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


if __name__ == '__main__':
    unittest.main()