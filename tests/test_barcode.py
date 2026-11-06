"""Run: python -m unittest discover -s tests -v"""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import runpy
import sys
from unittest.mock import patch

from PIL import Image, ImageDraw

import barcode_reader as reader
import template_store as store
from template_engine import apply_template


def fixture(symbols=None):
    # Independently specified Code128-B ABC123, checksum 67 (not reader's table).
    widths = symbols or ['211214', '111323', '131123', '131321', '123221', '223211', '221132', '141122', '2331112']
    image = Image.new('L', (720, 160), 255)
    draw = ImageDraw.Draw(image)
    x = 30
    for pattern in widths:
        dark = True
        for width in pattern:
            end = x + int(width) * 5
            if dark:
                draw.rectangle((x, 20, end - 1, 139), fill=0)
            x = end
            dark = not dark
    return image


class BarcodeTests(unittest.TestCase):
    def test_rotations_and_search_rectangle(self):
        for angle in (0, 7, -12, 90, 180, 270):
            with self.subTest(angle=angle):
                code = fixture().rotate(angle, expand=True, fillcolor=255)
                page = Image.new('L', (1100, 1100), 255)
                page.paste(code, (180, 180))
                self.assertEqual(reader.decode_code128(page), 'ABC123')

    def test_bad_checksum_and_blank(self):
        widths = ['211214', '111323', '131123', '131321', '123221', '223211', '221132', '112214', '2331112']
        self.assertIsNone(reader.decode_code128(fixture(widths), False))
        self.assertIsNone(reader.decode_code128(Image.new('L', (100, 100), 255)))

    def test_sets_shift_gs1(self):
        self.assertEqual(reader._symbols_to_text([105, 12, 34, 100, 33, 0]), '1234A')
        self.assertEqual(reader._symbols_to_text([104, 98, 64, 33, 0]), '\x00A')
        self.assertEqual(reader._symbols_to_text([105, 102, 12, 102, 34, 0]), '12\x1d34')

    def test_ai_global_gate_and_zone_opt_out(self):
        config = copy.deepcopy(store.DEFAULTS['barcode'])
        blank = Image.new('L', (100, 100), 255)
        with patch.object(reader, '_ai_decode') as ai:
            reader.barcode_zone(blank, 0, 0, 100, 100, config, True)
            ai.assert_not_called()
            config['ai']['enabled'] = True
            reader.barcode_zone(blank, 0, 0, 100, 100, config, False)
            ai.assert_not_called()
            ai.return_value = 'ABC123'
            self.assertEqual(reader.barcode_zone(blank, 0, 0, 100, 100, config)['verified'], False)

    def test_header_footer_and_legacy_text(self):
        template = {'name': 'test', 'header': {'fields': {
            'code': {'type': 'barcode', 'page': 2, 'x_min': 0, 'y_min': 0, 'x_max': 1000, 'y_max': 1000},
            'text': {'type': 'regex_full_text', 'pattern': r'ID: (\d+)'}
        }}, 'footer': {'fields': {}}}
        result = apply_template(template, [], 'ID: 42', [None, (fixture(), (720, 160))])
        self.assertEqual(result['code'], 'ABC123')
        self.assertEqual(result['text'], '42')
        self.assertEqual(result['righe'], [])
        self.assertTrue(result['_barcode']['code']['verified'])

    def test_native_text_zone_does_not_require_ocr(self):
        template = {'name': 'mixed', 'header': {'fields': {
            'text': {'type': 'regex_full_text', 'pattern': '(42)',
                     'x_min': 0, 'x_max': 1000, 'y_min': 0, 'y_max': 1000},
        }}}
        lines = [(20, [{'text': '42', 'x0': 10}])]
        result = apply_template(template, lines, '42', [(fixture(), (720, 160))], native_text=True)
        self.assertEqual(result['text'], '42')

    def test_optional_sections(self):
        field = {'type': 'regex_full_text', 'pattern': r'ID: (\d+)'}
        for section in ('header', 'footer'):
            with self.subTest(section=section):
                template = {'name': 'single', section: {'fields': {'id': field}}}
                result = apply_template(template, [], 'ID: 42')
                self.assertEqual(result['id'], '42')
                self.assertEqual(result['righe'], [])
        barcode = {'type': 'barcode', 'x_min': 0, 'x_max': 1000, 'y_min': 0, 'y_max': 1000}
        result = apply_template({'name': 'footer_only', 'footer': {'fields': {'code': barcode}}},
                                [], '', [(fixture(), (720, 160))])
        self.assertEqual(result['code'], 'ABC123')
        self.assertEqual(result['righe'], [])
        complete = {'name': 'complete', 'header': {'fields': {'id': field}},
                    'table': {'columns': []}, 'footer': {'fields': {'footer_id': field}}}
        result = apply_template(complete, [], 'ID: 42')
        self.assertEqual(result['id'], result['footer_id'])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, value in [('TEMPLATE_DIR', root / 'templates'), ('CONFIG_PATH', root / 'config.json')]:
            patcher = patch.object(store, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_defaults_reload_invalid_config(self):
        self.assertFalse(store.load_config()['barcode']['ai']['enabled'])
        store.CONFIG_PATH.write_text('{"barcode":{"renderDpi":400}}', encoding='utf-8')
        self.assertEqual(store.load_config()['barcode']['renderDpi'], 400)
        store.CONFIG_PATH.write_text('{bad', encoding='utf-8')
        with self.assertRaises(ValueError):
            store.load_config()
        self.assertEqual(store.CONFIG_PATH.read_text(), '{bad')

    def test_file_first_backup_and_db_fallback(self):
        template = {'name': 'demo', 'customer_file': 'X'}
        from db import db
        with patch.object(db, 'get_template_by_name', return_value={'name': 'demo', 'db': True}) as lookup:
            self.assertEqual(store.resolve_template('demo')[1], 'db')
            path = store.write_template_file(template)
            lookup.reset_mock()
            self.assertEqual(store.resolve_template('demo'), (template, 'file'))
            lookup.assert_not_called()
            with self.assertRaises(FileExistsError):
                store.write_template_file(template)
            store.write_template_file({**template, 'description': 'new'}, True)
            self.assertEqual(json.loads(path.with_suffix('.json.bak').read_text()), template)
            path.unlink()
            self.assertEqual(store.resolve_template('demo')[1], 'db')
        for name in ('../bad', 'a/b', 'a\\b', 'CON', '..'):
            with self.assertRaises(ValueError):
                store.template_path(name)

    def test_real_isolated_db(self):
        from db import Database
        database = Database('sqlite:///:memory:')
        self.addCleanup(database.engine.dispose)
        template = {'name': 'in_db', 'header': {'fields': {}}}
        database.save_template(template, 'TEST')
        with patch('db.db', database):
            loaded, origin = store.resolve_template('in_db', 'TEST')
            self.assertEqual(origin, 'db')
            self.assertEqual(loaded['name'], 'in_db')
            self.assertIsNone(store.resolve_template('in_db', 'OTHER')[0])

    def test_cli_json_exit_status_and_no_ai_bootstrap(self):
        from db import db
        root = Path(__file__).resolve().parents[1]
        template = {'name': 'CLI_TEST', 'customer_file': 'TEST', 'header': {'fields': {
            'code': {'type': 'barcode', 'x_min': 0, 'x_max': 1000, 'y_min': 0, 'y_max': 1000}
        }}}
        store.write_template_file(template)
        image_path = Path(self.temp.name) / 'image.png'
        fixture().save(image_path)
        output = Path(self.temp.name) / 'result.json'
        args = [str(root / 'main.py'), '-customer', 'TEST', '-template', 'CLI_TEST',
                '-no-ai-bootstrap', '-outputTemplatePath', str(output), '-eoi', str(image_path)]
        with patch.object(sys, 'argv', args), patch.object(db, 'log_event'), patch('sys.stdout', new_callable=io.StringIO):
            runpy.run_path(str(root / 'main.py'), run_name='__main__')
        self.assertEqual(json.loads(output.read_text())['code'], 'ABC123')
        Image.new('L', (100, 100), 255).save(image_path)
        with patch.object(sys, 'argv', args), patch.object(db, 'log_event'), patch('sys.stdout', new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as exit_status:
                runpy.run_path(str(root / 'main.py'), run_name='__main__')
        self.assertEqual(exit_status.exception.code, 1)
        self.assertEqual(json.loads(output.read_text())['code'], 'N/A')
        from main import _bootstrap_ai
        with patch.object(sys, 'argv', ['main.py', '-no-ai-bootstrap']):
            with self.assertRaises(RuntimeError):
                _bootstrap_ai([], 'Enough text for AI but forbidden', 'native', template_name='missing')

    def test_pdf_render_and_editor_api(self):
        from server import app
        from main import estrai_ordine
        app.config['TESTING'] = True
        template = {'name': 'TEST_CODE128', 'customer_file': 'TEST', 'header': {'fields': {
            'barcode': {'type': 'barcode', 'page': 1, 'x_min': 0, 'x_max': 1000, 'y_min': 0, 'y_max': 1000}
        }}}
        path = Path(self.temp.name) / 'sample.pdf'
        fixture().convert('RGB').save(path, 'PDF', resolution=100)
        store.write_template_file(template)
        with patch('barcode_reader._ai_decode', side_effect=AssertionError('Network forbidden')):
            result = estrai_ordine(path, 'TEST', 'TEST_CODE128')
        self.assertEqual(result['barcode'], 'ABC123')
        client = app.test_client()
        with client.session_transaction() as session:
            session['user'] = 'admin'
        loaded = client.get('/api/templates/TEST_CODE128').get_json()
        self.assertEqual(loaded['_origin'], 'file')
        response = client.post('/api/test-pdf', data={
            'file': (io.BytesIO(path.read_bytes()), 'sample.pdf'), 'template': json.dumps(template)
        }, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['barcode'], 'ABC123')
        response = client.post('/api/templates/TEST_CODE128/write-file', json={'template': template})
        self.assertEqual(response.status_code, 409)
        response = client.post('/api/templates/TEST_CODE128/write-file', json={'template': template, 'overwrite': True})
        self.assertEqual(response.status_code, 200)


if __name__ == '__main__':
    unittest.main()