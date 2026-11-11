"""Trasferimento SFTP su SSH: pubblicazione atomica, poi rimozione locale."""
import copy
import errno
import getpass
import json
from pathlib import Path, PurePosixPath
import re
import stat
import uuid

import soPDF_reporting as reporting


DEFAULTS = {'enabled': False, 'host': '', 'port': 22, 'user': '',
            'remoteDir': '', 'passwordEnvVar': 'SOPDF_SCP_PASSWORD',
            'identityFile': '', 'knownHostsFile': '', 'timeoutSec': 30,
            'retries': 3, 'notify': {'enabled': True, 'to': [], 'reminderHours': 24}}


def validate(cfg):
    if type(cfg['port']) is not int or not 1 <= cfg['port'] <= 65535:
        raise ValueError('transfer.port: richiesto intero 1-65535')
    if cfg['timeoutSec'] <= 0 or type(cfg['retries']) is not int or not 1 <= cfg['retries'] <= 10:
        raise ValueError('transfer: timeout positivo, retries intero 1-10')
    if cfg['notify']['reminderHours'] < 0:
        raise ValueError('transfer.notify.reminderHours: richiesto valore >=0')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', cfg['passwordEnvVar']):
        raise ValueError('transfer.passwordEnvVar non valido')
    if any(not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', a) for a in cfg['notify']['to']):
        raise ValueError('transfer.notify.to non valido')
    if cfg['enabled']:
        if not cfg['host'].strip() or not cfg['remoteDir'].startswith('/'):
            raise ValueError('transfer: host obbligatorio e remoteDir assoluta POSIX')
        if any(c in cfg['remoteDir'] for c in '\x00\r\n') or '..' in PurePosixPath(cfg['remoteDir']).parts:
            raise ValueError('transfer.remoteDir non valida')


def local_path(root, value):
    return (root / Path(value).expanduser()).resolve()


def connect(cfg, root):
    # Import differito: trasferimento disabilitato non richiede paramiko.
    import paramiko
    alias = cfg['host']
    ssh_config = paramiko.SSHConfig()
    config_file = Path.home() / '.ssh' / 'config'
    if config_file.is_file():
        with config_file.open(encoding='utf-8') as stream:
            ssh_config.parse(stream)
    settings = ssh_config.lookup(alias)
    client = paramiko.SSHClient()
    try:
        client.load_system_host_keys()
        if cfg['knownHostsFile']:
            client.load_host_keys(str(local_path(root, cfg['knownHostsFile'])))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        password = reporting.api_key({'apiKeyEnvVar': cfg['passwordEnvVar']}, root) or None
        identity = ([str(local_path(root, cfg['identityFile']))] if cfg['identityFile']
                    else settings.get('identityfile'))
        client.connect(hostname=settings.get('hostname', alias), port=cfg['port'],
                       username=cfg['user'] or settings.get('user') or getpass.getuser(),
                       password=password, key_filename=identity,
                       allow_agent=password is None, look_for_keys=password is None,
                       timeout=cfg['timeoutSec'], auth_timeout=cfg['timeoutSec'],
                       banner_timeout=cfg['timeoutSec'])
        sftp = client.open_sftp()
        sftp.get_channel().settimeout(cfg['timeoutSec'])
        return client, sftp
    except Exception:
        client.close()
        raise


def mkdirs(sftp, directory):
    current = PurePosixPath('/')
    for part in PurePosixPath(directory).parts[1:]:
        current /= part
        try:
            attrs = sftp.lstat(str(current))
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise
            sftp.mkdir(str(current))
        else:
            if not stat.S_ISDIR(attrs.st_mode):
                raise OSError('Destinazione non directory o link simbolico')


def upload(sftp, source, destination):
    """Server deve supportare posix-rename: mai rimuovere prima il vecchio file."""
    before = source.stat()
    pending = str(PurePosixPath(destination).with_name('.sopdf-' + uuid.uuid4().hex + '.part'))
    try:
        try:
            attrs = sftp.lstat(destination)
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise
        else:
            if not stat.S_ISREG(attrs.st_mode):
                raise OSError('Destinazione non file regolare')
        sftp.put(str(source), pending, confirm=True)
        if sftp.stat(pending).st_size != before.st_size:
            raise OSError('Dimensione remota non valida')
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise OSError('File locale cambiato durante upload')
        sftp.posix_rename(pending, destination)
        source.unlink()
    finally:
        try:
            sftp.remove(pending)
        except OSError:
            pass


def notify(cfg, email, root, state_path, failures, logger):
    previous = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    state = {'failures': failures, 'checkedAt': reporting.now(),
             'email': previous.get('email', {})}
    reporting.save_json(state_path, state)
    if not failures or not cfg['notify']['enabled']:
        return
    elapsed = reporting.duration(state['email'].get('sentAt'), reporting.now())
    if (previous.get('failures') == failures and elapsed is not None
            and elapsed < cfg['notify']['reminderHours'] * 3600):
        return
    settings = copy.deepcopy(email)
    settings['to'] = cfg['notify']['to'] or email['to']
    rendered = {'subject': f'soPDF - {len(failures)} file non spostati',
                'textContent': 'Trasferimento SSH/SFTP non completato. File conservati localmente.\n'
                + f"Destinazione: {cfg['host']}:{cfg['remoteDir']}\n\n"
                + '\n'.join(f"- {f['file']}: {f['reason']}" for f in failures)}
    try:
        message_id = reporting.send_brevo(settings, rendered, root)
        state['email'] = {'status': 'sent', 'sentAt': reporting.now(), 'messageId': message_id}
    except Exception as exc:
        state['email'] = {'status': 'failed', 'error': type(exc).__name__}
        logger.warning('TRANSFER EMAIL | invio fallito (%s); retry prossima passata', type(exc).__name__)
    reporting.save_json(state_path, state)


def run(cfg, paths, email, root, logger):
    if not cfg['enabled']:
        return False
    failures = []
    client = sftp = None
    try:
        files = []
        for path in sorted(paths['out_pdf'].rglob('*')):
            if path.is_symlink():
                failures.append({'file': path.relative_to(paths['out_pdf']).as_posix(),
                                 'reason': 'Link simbolico non trasferito'})
            elif path.is_file():
                relative = path.relative_to(paths['out_pdf']).as_posix()
                files.append((path, relative))
        logger.info('TRANSFER | avvio: %s file da inviare', len(files))
        for source, relative in files:
            error = None
            for _ in range(cfg['retries']):
                try:
                    if client is None:
                        client, sftp = connect(cfg, root)
                    remote = str(PurePosixPath(cfg['remoteDir']) / relative)
                    mkdirs(sftp, str(PurePosixPath(remote).parent))
                    upload(sftp, source, remote)
                    logger.info('TRANSFER | inviato e rimosso: %s', relative)
                    error = None
                    break
                except Exception as exc:
                    error = type(exc).__name__
                    if sftp is not None:
                        sftp.close()
                    if client is not None:
                        client.close()
                    client = sftp = None
            if error:
                failures.append({'file': relative, 'reason': f'Trasferimento non completato ({error})'})
        # Rimuove solo directory vuote: i file falliti restano al loro posto.
        for directory in sorted(paths['out_pdf'].rglob('*'), key=lambda p: len(p.parts), reverse=True):
            if directory.is_dir() and not directory.is_symlink():
                try:
                    directory.rmdir()
                except OSError:
                    pass
    finally:
        if sftp is not None:
            sftp.close()
        if client is not None:
            client.close()
    notify(cfg, email, root, paths['reportDir'] / 'transfer_state.json', failures, logger)
    return bool(failures)