"""Run: python -m unittest discover -s tests -p test_split_pdf.py -v"""
import copy
import json
import logging
import os
from pathlib import Path
import socket
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import pypdfium2 as pdfium

import split_pdf as split


def make_pdf(path, count=4):
    with pdfium.PdfDocument.new() as document:
        for _ in range(count):
            page = document.new_page(595.28, 841.89)
            page.close()
        document.save(path)


class SplitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = copy.deepcopy(split.DEFAULTS)
        self.cfg['paths'] = {key: str(self.root / key) for key in self.cfg['paths']}
        self.cfg['minFileAgeSec'] = 0
        self.cfg['compression']['enabled'] = False
        self.logger = logging.getLogger(f'split-test-{id(self)}')
        self.logger.addHandler(logging.NullHandler())
        self.runner = split.Runner(self.cfg, self.logger)
        for folder in self.runner.paths.values():
            folder.mkdir()

    def reader(self, command, **kwargs):
        result = Path(command[command.index('-outputTemplatePath') + 1])
        result.write_text(json.dumps({'barcode_top': 'S0000854'}), encoding='utf-8')
        self.assertNotIn('shell', kwargs)
        return subprocess.CompletedProcess(command, 0, 'OK', '')

    def input(self, name='a.pdf', count=4):
        source = self.runner.paths['in_pdf'] / name
        make_pdf(source, count)
        return source

    def test_jsonc_strings_comments_and_trailing_commas(self):
        text = r'''{
          "url": "https://host/a/*keep*/", // comment
          "path": "\\\\nas\\share", /* comment */
          "quoted": "say \"//hello\"",
          "list": [1, 2,],
        }'''
        data = split.parse_jsonc(text)
        self.assertEqual(data['url'], 'https://host/a/*keep*/')
        self.assertEqual(data['quoted'], 'say "//hello"')
        self.assertEqual(data['list'], [1, 2])
        with self.assertRaises(json.JSONDecodeError) as caught:
            split.parse_jsonc('{\n "a": /* missing')
        self.assertEqual(caught.exception.lineno, 2)
        with self.assertRaises(json.JSONDecodeError):
            split.parse_jsonc('{\n "a": }')

    def test_config_defaults_and_types(self):
        path = self.root / 'config.jsonc'
        for value in ({}, {'parallel': {'maxWorkers': None}}, {'parallel': {'maxWorkers': 0}}):
            path.write_text(json.dumps(value), encoding='utf-8')
            self.assertEqual(split.load_config(path)['parallel']['maxWorkers'], 1)
        for value in ({'extractor': {'timeoutSec': 'abc'}}, {'parallel': {'maxWorkers': True}},
                      {'log': {'maxSizeMB': -1}}, {'unknown': 1},
                      {'barcode': {'validationRegex': '['}}, {'paths': {'in_pdf': '.'}}):
            path.write_text(json.dumps(value), encoding='utf-8')
            with self.subTest(value=value), self.assertRaises(split.ConfigError):
                split.load_config(path)
        self.assertEqual(split.load_config(split.ROOT / 'split_config.jsonc')['log']['maxSizeMB'], 10)

    def test_split_dimensions(self):
        source = self.input()
        folder = self.root / 'pages'
        self.assertEqual(split.split_pages(source, folder), 4)
        for number in range(1, 5):
            with pdfium.PdfDocument(folder / f'{number}.pdf') as doc:
                self.assertEqual(len(doc), 1)
                page = doc[0]
                self.assertAlmostEqual(page.get_width(), 595.28, places=1)
                page.close()

    def test_four_pages_and_duplicates(self):
        self.input()
        with patch.object(split.subprocess, 'run', side_effect=self.reader) as reader:
            self.assertEqual(self.runner.run(), 0)
            self.assertEqual(reader.call_count, 4)
        self.assertEqual(sorted(p.name for p in self.runner.paths['out_pdf'].glob('*.pdf')),
                         ['S0000854.pdf', 'S0000854_1.pdf', 'S0000854_2.pdf', 'S0000854_3.pdf'])
        self.assertEqual(len(list(self.runner.paths['worked_pdf'].glob('*/*.pdf'))), 1)
        self.assertEqual(list(self.runner.paths['in_pdf'].iterdir()), [])
        self.assertEqual(list(self.runner.paths['tempDir'].iterdir()), [])

    def test_readonly_source_cleanup(self):
        source = self.input(count=1)
        source.chmod(stat.S_IRUSR)
        with patch.object(split.subprocess, 'run', side_effect=self.reader):
            self.assertEqual(self.runner.run(), 0)
        self.assertEqual(list(self.runner.paths['tempDir'].iterdir()), [])
        self.assertEqual(len(list(self.runner.paths['worked_pdf'].glob('*/a.pdf'))), 1)

    def test_cleanup_failure_keeps_done_state_and_resumes(self):
        source = self.input(count=1)
        with patch.object(split.subprocess, 'run', side_effect=self.reader), \
                patch.object(split.shutil, 'rmtree', side_effect=PermissionError('locked pages')):
            self.assertEqual(self.runner.run(), 0)
        folder = next(self.runner.paths['tempDir'].iterdir())
        self.assertEqual(json.loads((folder / 'job.json').read_text())['phase'], 'done')
        self.assertFalse((folder / source.name).exists())
        with patch.object(split.subprocess, 'run') as reader:
            self.assertEqual(self.runner.run(), 0)
            reader.assert_not_called()
        self.assertFalse(folder.exists())

    def test_cleanup_final_rmdir_failure_restores_state(self):
        source = self.input(count=1)
        job = self.runner.create_job(source)
        job.state['phase'] = 'done'
        job.save()
        with patch.object(Path, 'rmdir', side_effect=PermissionError('locked folder')):
            self.assertFalse(split.cleanup_job(job, self.logger))
        self.assertEqual(json.loads((job.folder / 'job.json').read_text())['phase'], 'done')
        self.assertTrue(split.cleanup_job(job, self.logger))

    def test_orphan_folder_warns_without_failing_or_deleting(self):
        folder = self.runner.paths['tempDir'] / 'orphan'
        folder.mkdir()
        (folder / 'source.pdf').write_bytes(b'keep')
        with self.assertLogs(self.logger, logging.WARNING) as captured:
            self.assertEqual(self.runner.run(), 0)
        self.assertIn('cartella orfana', captured.output[0])
        self.assertEqual((folder / 'source.pdf').read_bytes(), b'keep')

    def test_homonyms_separate_jobs_and_failed_pages(self):
        def fail_third(command, **kwargs):
            if Path(command[-1]).stem == '3':
                return subprocess.CompletedProcess(command, 1, 'barcode N/A', 'bad barcode')
            return self.reader(command, **kwargs)
        for _ in range(2):
            self.input('a.PDF')
            with patch.object(split.subprocess, 'run', side_effect=fail_third):
                self.assertEqual(self.runner.run(), 1)
        failed = list(self.runner.paths['err_pdf'].glob('*/a_p3.pdf'))
        self.assertEqual(len(failed), 2)
        self.assertNotEqual(failed[0].parent, failed[1].parent)
        self.assertEqual(len(list(self.runner.paths['worked_pdf'].glob('*/*.pdf'))), 2)

    def test_multiple_sources_and_threads(self):
        self.cfg['parallel']['maxWorkers'] = 3
        self.input('a.pdf', 3)
        self.input('b.PDF', 3)
        with patch.object(split.subprocess, 'run', side_effect=self.reader):
            self.assertEqual(self.runner.run(), 0)
        self.assertEqual(len(list(self.runner.paths['out_pdf'].glob('*.pdf'))), 6)
        self.assertEqual(self.runner.totals['jobs'], 2)

    def test_page_errors(self):
        source = self.input(count=1)
        result = self.root / 'result.json'
        scenarios = ['missing', 'corrupt', 'na', 'regex', 'exit', 'timeout']
        for case in scenarios:
            def reader(command, **kwargs):
                if case == 'timeout':
                    raise subprocess.TimeoutExpired(command, 1, output=b'partial')
                if case == 'corrupt':
                    result.write_text('{', encoding='utf-8')
                elif case not in ('missing', 'exit'):
                    result.write_text(json.dumps({'barcode_top': 'N/A' if case == 'na' else 'ABC'}), encoding='utf-8')
                return subprocess.CompletedProcess(command, int(case == 'exit'), 'detail', 'failure')
            cfg = copy.deepcopy(self.cfg)
            cfg['barcode']['validationRegex'] = r'S\d+'
            with self.subTest(case=case), patch.object(split.subprocess, 'run', side_effect=reader):
                with self.assertRaises(split.PageError):
                    split.extract_barcode(source, result, cfg, self.logger, 'test')

    def test_corrupt_pdf_archived_in_error(self):
        (self.runner.paths['in_pdf'] / 'bad.pdf').write_bytes(b'not a PDF')
        self.assertEqual(self.runner.run(), 1)
        self.assertEqual(len(list(self.runner.paths['err_pdf'].glob('*/bad.pdf'))), 1)

    def test_resume_completed_pages_and_publish_intent(self):
        source = self.input(count=2)
        job = self.runner.create_job(source)
        job.state.update(phase='pages', count=split.split_pages(job.folder / source.name, job.folder / 'pages'))
        job.save()
        with patch.object(split.subprocess, 'run', side_effect=self.reader):
            self.runner.process_page(job, 1)
        # Simula morte dopo pubblicazione ma prima del salvataggio esito done.
        record = job.state['pages']['1']
        record['status'] = 'publishing'
        job.save()
        with patch.object(split.subprocess, 'run', side_effect=self.reader) as reader:
            self.assertEqual(self.runner.run(), 0)
            self.assertEqual(reader.call_count, 1)
        self.assertEqual(len(list(self.runner.paths['out_pdf'].glob('*.pdf'))), 2)

    def test_disk_failure_keeps_original_for_resume(self):
        self.input(count=1)
        with patch.object(split.subprocess, 'run', side_effect=self.reader), \
                patch.object(split.os, 'link', side_effect=OSError('disk full')):
            self.assertEqual(self.runner.run(), 1)
        self.assertEqual(len(list(self.runner.paths['tempDir'].glob('*/a.pdf'))), 1)
        self.assertEqual(len(list(self.runner.paths['out_pdf'].glob('*.pdf'))), 0)
        with patch.object(split.subprocess, 'run') as reader:
            # Intento già pronto: nessuna estrazione ripetuta.
            self.assertEqual(self.runner.run(), 0)
            reader.assert_not_called()

    def test_lock_active_and_stale(self):
        path = self.root / '.lock'
        with split.instance_lock(path, 0, self.logger):
            with self.assertRaises(split.LockError):
                with split.instance_lock(path, 0, self.logger):
                    pass
        self.assertFalse(path.exists())
        path.write_text(json.dumps({'pid': 9999999, 'host': socket.gethostname()}), encoding='utf-8')
        with patch.object(split, 'pid_alive', return_value=False):
            with split.instance_lock(path, 0, self.logger):
                self.assertEqual(json.loads(path.read_text())['pid'], os.getpid())
        self.assertFalse(path.exists())

    def test_incomplete_pickup_not_processed(self):
        source = self.input(count=1)
        job = self.runner.create_job(source)
        job.state['phase'] = 'pickup'
        (job.folder / source.name).write_bytes(b'incomplete copy')
        job.save()
        with patch.object(split.subprocess, 'run') as reader:
            self.assertEqual(self.runner.run(), 1)
            reader.assert_not_called()
        self.assertTrue((job.folder / source.name).exists())

    def test_missing_completed_output_not_ignored(self):
        source = self.input(count=2)
        job = self.runner.create_job(source)
        job.state.update(phase='pages', count=split.split_pages(job.folder / source.name, job.folder / 'pages'))
        job.save()
        with patch.object(split.subprocess, 'run', side_effect=self.reader):
            self.runner.process_page(job, 1)
        Path(job.state['pages']['1']['destination']).unlink()
        self.assertEqual(self.runner.run(), 1)
        self.assertTrue((job.folder / source.name).exists())

    def test_file_stability(self):
        source = self.input(count=1)
        self.assertFalse(split.stable_file(source, 60))
        self.assertTrue(split.stable_file(source, 0))
        self.assertFalse(split.stable_file(self.root / 'missing.pdf', 0))

    def test_safe_names(self):
        self.assertEqual(split.safe_stem('A/B:C'), 'A_B_C')
        self.assertEqual(split.safe_stem('CON'), '_CON')
        self.assertLessEqual(len(split.safe_stem('è' * 200).encode()), 160)

    def test_compression_and_fallbacks(self):
        source = self.root / 'image.pdf'
        image = Image.effect_noise((1200, 1700), 70).convert('RGB')
        image.save(source, 'PDF', resolution=144, quality=95)
        image.close()
        cfg = copy.deepcopy(split.DEFAULTS['compression'])
        cfg.update(dpi=72, jpegQuality=30)
        target = self.root / 'compressed.tmp'
        result = split.compress_page(source, target, cfg, self.logger, 'test')
        self.assertEqual(result, target)
        self.assertLess(target.stat().st_size, source.stat().st_size)
        with pdfium.PdfDocument(source) as before, pdfium.PdfDocument(target) as after:
            a, b = before[0], after[0]
            self.assertAlmostEqual(a.get_width(), b.get_width(), delta=1.1)
            a.close()
            b.close()
        cfg['minGainPercent'] = 100
        self.assertEqual(split.compress_page(source, target, cfg, self.logger, 'test'), source)
        cfg['enabled'] = False
        self.assertEqual(split.compress_page(source, target, cfg, self.logger, 'test'), source)
        cfg['enabled'] = True
        with patch.object(split.pdfium, 'PdfDocument', side_effect=RuntimeError('render error')):
            self.assertEqual(split.compress_page(source, target, cfg, self.logger, 'test'), source)

    def test_skip_native_text(self):
        from unittest.mock import MagicMock
        source = self.input(count=1)
        fake = MagicMock()
        fake.__enter__.return_value.__getitem__.return_value.get_textpage.return_value.get_text_range.return_value = 'native text'
        with patch.object(split.pdfium, 'PdfDocument', return_value=fake):
            result = split.compress_page(source, self.root / 'unused', split.DEFAULTS['compression'], self.logger, 'test')
        self.assertEqual(result, source)
        self.assertFalse((self.root / 'unused').exists())

    def test_rotation_with_and_without_backups(self):
        for backups in (0, 2):
            path = self.root / f'log-{backups}.log'
            handler = split.SizeRotatingHandler(path, maxBytes=100, backupCount=backups, encoding='utf-8')
            try:
                for _ in range(15):
                    handler.emit(logging.LogRecord('test', logging.INFO, '', 0, 'x' * 30, (), None))
                self.assertLessEqual(path.stat().st_size, 100)
                self.assertEqual(path.with_name(path.name + '.1').exists(), backups > 0)
            finally:
                handler.close()

    def test_email_stub_never_sends(self):
        cfg = copy.deepcopy(self.cfg['email'])
        cfg['sendOnError'] = True
        with self.assertLogs(self.logger, logging.WARNING) as captured:
            split.notify_errors(cfg, self.logger, 'job', 'a.pdf', [3], ['a_p3.pdf'])
        self.assertIn('NON implementato', captured.output[0])


if __name__ == '__main__':
    unittest.main()