import copy
import errno
import json
import logging
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import soPDF as split
import soPDF_transfer as transfer


class FakeSFTP:
    def __init__(self):
        self.files = {}
        self.directories = {'/'}
        self.fail_put = False
        self.fail_rename = False

    def lstat(self, path):
        if path in self.directories:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_size=0)
        if path in self.files:
            return SimpleNamespace(st_mode=stat.S_IFREG, st_size=len(self.files[path]))
        raise FileNotFoundError(errno.ENOENT, 'missing')

    stat = lstat

    def mkdir(self, path):
        self.directories.add(path)

    def put(self, source, destination, confirm):
        if self.fail_put:
            raise OSError('upload failed')
        self.files[destination] = Path(source).read_bytes()

    def posix_rename(self, source, destination):
        if self.fail_rename:
            raise OSError('rename unsupported')
        self.files[destination] = self.files.pop(source)

    def remove(self, path):
        if path not in self.files:
            raise FileNotFoundError(errno.ENOENT, 'missing')
        del self.files[path]

    def close(self):
        pass


class TransferTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.paths = {key: self.root / key for key in ('out_pdf', 'tempDir', 'reportDir')}
        for path in self.paths.values():
            path.mkdir()
        self.cfg = copy.deepcopy(transfer.DEFAULTS)
        self.cfg.update(enabled=True, host='example.test', remoteDir='/incoming', retries=2)
        self.email = copy.deepcopy(split.DEFAULTS['email'])
        self.sftp = FakeSFTP()
        self.logger = logging.getLogger('test-transfer')
        self.client = Mock()

    def file(self, name='a.pdf', data=b'new'):
        path = self.paths['out_pdf'] / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def run_transfer(self):
        with patch.object(transfer, 'connect', return_value=(self.client, self.sftp)), \
                patch.object(transfer.reporting, 'send_brevo', return_value='message') as send:
            result = transfer.run(self.cfg, self.paths, self.email, self.root, self.logger)
        return result, send

    def test_overwrite_and_delete_local_only_after_success(self):
        source = self.file()
        self.sftp.files['/incoming/a.pdf'] = b'old'
        result, send = self.run_transfer()
        self.assertFalse(result)
        self.assertFalse(source.exists())
        self.assertEqual(self.sftp.files, {'/incoming/a.pdf': b'new'})
        send.assert_not_called()

    def test_directories_merged_remote_only_files_preserved(self):
        self.file('job/sub/a.pdf')
        self.file('job/b.pdf')
        self.sftp.files['/incoming/job/remote-only.pdf'] = b'keep'
        self.assertFalse(self.run_transfer()[0])
        self.assertEqual(list(self.paths['out_pdf'].iterdir()), [])
        self.assertEqual(self.sftp.files['/incoming/job/sub/a.pdf'], b'new')
        self.assertEqual(self.sftp.files['/incoming/job/remote-only.pdf'], b'keep')

    def test_upload_failure_keeps_local_and_old_remote_notifies_then_retries(self):
        source = self.file()
        self.sftp.files['/incoming/a.pdf'] = b'old'
        self.sftp.fail_put = True
        result, send = self.run_transfer()
        self.assertTrue(result)
        self.assertTrue(source.exists())
        self.assertEqual(self.sftp.files['/incoming/a.pdf'], b'old')
        self.assertIn('a.pdf', send.call_args.args[1]['textContent'])
        self.assertEqual(self.run_transfer()[1].call_count, 0)
        self.sftp.fail_put = False
        self.assertFalse(self.run_transfer()[0])
        self.assertFalse(source.exists())

    def test_rename_failure_preserves_old_remote_and_local(self):
        source = self.file()
        self.sftp.files['/incoming/a.pdf'] = b'old'
        self.sftp.fail_rename = True
        self.assertTrue(self.run_transfer()[0])
        self.assertTrue(source.exists())
        self.assertEqual(self.sftp.files, {'/incoming/a.pdf': b'old'})

    def test_connection_failure_lists_all_files(self):
        self.file('job/a.pdf')
        self.file('job/b.pdf')
        with patch.object(transfer, 'connect', side_effect=OSError('secret')), \
                patch.object(transfer.reporting, 'send_brevo', return_value='message') as send:
            self.assertTrue(transfer.run(self.cfg, self.paths, self.email, self.root, self.logger))
        body = send.call_args.args[1]['textContent']
        self.assertIn('job/a.pdf', body)
        self.assertIn('job/b.pdf', body)
        self.assertNotIn('secret', body)

    def test_incomplete_job_outputs_are_sent(self):
        source = self.file('job/a.pdf')
        job = self.paths['tempDir'] / 'pending'
        job.mkdir()
        (job / 'job.json').write_text(json.dumps({'phase': 'pages', 'outDirSeparation': True,
                                                'outputName': 'job'}), encoding='utf-8')
        self.assertFalse(self.run_transfer()[0])
        self.assertFalse(source.exists())
        self.assertEqual(self.sftp.files, {'/incoming/job/a.pdf': b'new'})

    def test_flat_incomplete_job_does_not_block_any_output(self):
        self.file('a.pdf')
        self.file('b.pdf')
        job = self.paths['tempDir'] / 'pending'
        job.mkdir()
        (job / 'job.json').write_text(json.dumps({'phase': 'pages', 'outDirSeparation': False}),
                                      encoding='utf-8')
        self.assertFalse(self.run_transfer()[0])
        self.assertEqual(list(self.paths['out_pdf'].iterdir()), [])
        self.assertEqual(self.sftp.files, {'/incoming/a.pdf': b'new', '/incoming/b.pdf': b'new'})

    def test_failed_email_retried(self):
        self.file()
        self.sftp.fail_put = True
        with patch.object(transfer, 'connect', return_value=(self.client, self.sftp)), \
                patch.object(transfer.reporting, 'send_brevo', side_effect=RuntimeError('secret')):
            transfer.run(self.cfg, self.paths, self.email, self.root, self.logger)
        self.assertEqual(self.run_transfer()[1].call_count, 1)

    def test_disabled_does_not_touch_outputs(self):
        source = self.file()
        self.cfg['enabled'] = False
        self.assertFalse(self.run_transfer()[0])
        self.assertTrue(source.exists())

    def test_size_mismatch_preserves_local(self):
        source = self.file()
        with patch.object(self.sftp, 'put', side_effect=lambda src, dst, confirm: self.sftp.files.update({dst: b''})):
            self.assertTrue(self.run_transfer()[0])
        self.assertTrue(source.exists())
        self.assertEqual(self.sftp.files, {})

    def test_remote_directory_cannot_be_overwritten_by_file(self):
        source = self.file()
        self.sftp.directories.add('/incoming/a.pdf')
        self.assertTrue(self.run_transfer()[0])
        self.assertTrue(source.exists())

    def test_bad_job_state_does_not_block_outputs(self):
        source = self.file()
        job = self.paths['tempDir'] / 'broken'
        job.mkdir()
        (job / 'job.json').write_text('{bad', encoding='utf-8')
        self.assertFalse(self.run_transfer()[0])
        self.assertFalse(source.exists())
        self.assertEqual(self.sftp.files, {'/incoming/a.pdf': b'new'})

    def test_runner_transfers_existing_outputs_on_empty_pass(self):
        source = self.file()
        cfg = copy.deepcopy(split.DEFAULTS)
        cfg['transfer'] = self.cfg
        cfg['paths'] = {key: str(self.root / key) for key in cfg['paths']}
        for folder in cfg['paths'].values():
            Path(folder).mkdir(exist_ok=True)
        runner = split.Runner(cfg, self.logger)
        with patch.object(transfer, 'connect', return_value=(self.client, self.sftp)):
            self.assertEqual(runner.run(), 0)
        self.assertFalse(source.exists())

    def test_runner_returns_error_on_transfer_failure(self):
        source = self.file()
        cfg = copy.deepcopy(split.DEFAULTS)
        cfg['transfer'] = self.cfg
        cfg['paths'] = {key: str(self.root / key) for key in cfg['paths']}
        for folder in cfg['paths'].values():
            Path(folder).mkdir(exist_ok=True)
        with patch.object(transfer, 'connect', side_effect=OSError()), \
                patch.object(transfer.reporting, 'send_brevo', return_value='message'):
            self.assertEqual(split.Runner(cfg, self.logger).run(), 1)
        self.assertTrue(source.exists())

    def test_runner_job_resume_failure_does_not_prevent_transfer(self):
        source = self.file()
        cfg = copy.deepcopy(split.DEFAULTS)
        cfg['transfer'] = self.cfg
        cfg['paths'] = {key: str(self.root / key) for key in cfg['paths']}
        for folder in cfg['paths'].values():
            Path(folder).mkdir(exist_ok=True)
        job = Path(cfg['paths']['tempDir']) / 'broken'
        job.mkdir()
        (job / 'job.json').write_text('{bad', encoding='utf-8')
        with patch.object(transfer, 'connect', return_value=(self.client, self.sftp)), \
                self.assertLogs(self.logger, logging.ERROR):
            self.assertEqual(split.Runner(cfg, self.logger).run(), 1)
        self.assertFalse(source.exists())
        self.assertEqual(self.sftp.files, {'/incoming/a.pdf': b'new'})

    def test_config_validation(self):
        config = self.root / 'config.jsonc'
        for invalid in ({'port': 0}, {'retries': 0}, {'port': 22.5},
                        {'enabled': True, 'host': 'host', 'remoteDir': 'relative'},
                        {'passwordEnvVar': 'BAD NAME'}):
            config.write_text(json.dumps({'transfer': invalid}), encoding='utf-8')
            with self.subTest(invalid=invalid), self.assertRaises(split.ConfigError):
                split.load_config(config)

    def test_auth_password_then_keys_without_user(self):
        import paramiko
        client = Mock()
        sftp = client.open_sftp.return_value
        ssh_config = Mock()
        ssh_config.lookup.return_value = {'hostname': 'example.test', 'user': 'ssh-user'}
        with patch.object(paramiko, 'SSHClient', return_value=client), \
                patch.object(paramiko, 'SSHConfig', return_value=ssh_config), \
                patch.object(transfer.reporting, 'api_key', return_value='test-password'):
            transfer.connect(self.cfg, self.root)
        args = client.connect.call_args.kwargs
        self.assertEqual(args['password'], 'test-password')
        self.assertEqual(args['username'], 'ssh-user')
        self.assertFalse(args['look_for_keys'])
        with patch.object(paramiko, 'SSHClient', return_value=client), \
                patch.object(paramiko, 'SSHConfig', return_value=ssh_config), \
                patch.object(transfer.reporting, 'api_key', return_value=''):
            transfer.connect(self.cfg, self.root)
        args = client.connect.call_args.kwargs
        self.assertIsNone(args['password'])
        self.assertTrue(args['look_for_keys'])
        self.assertTrue(args['allow_agent'])
        sftp.get_channel.return_value.settimeout.assert_called_with(30)


if __name__ == '__main__':
    unittest.main()