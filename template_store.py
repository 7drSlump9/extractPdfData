"""Shared configuration and explicit file-first template storage."""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "web" / "src" / "config.json"
TEMPLATE_DIR = ROOT / "templates" / "decode"
DEFAULTS = {
    "ocrDpi": 200,
    "aiBootstrap": {"enabled": True},
    "barcode": {
        "renderDpi": 300, "searchMargin": 0.10, "tryRotations": True,
        "ai": {"enabled": False, "model": "google/gemini-2.5-flash", "timeout": 60},
    },
}


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _merge(base, extra):
    result = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config():
    """Read at each operation; invalid existing files are never overwritten."""
    if not CONFIG_PATH.exists():
        # Exclusive creation avoids overwriting a concurrently created config.
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            with CONFIG_PATH.open("x", encoding="utf-8") as stream:
                json.dump(DEFAULTS, stream, indent=2)
        except FileExistsError:
            pass
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Config non valida: {CONFIG_PATH}, riga {exc.lineno}: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Config deve essere un oggetto JSON: {CONFIG_PATH}")
    settings = _merge(DEFAULTS, data)
    barcode = settings.get('barcode')
    bootstrap = settings.get('aiBootstrap')
    if not isinstance(barcode, dict) or not isinstance(bootstrap, dict):
        raise ValueError('barcode e aiBootstrap devono essere oggetti JSON')
    ai = barcode.get('ai')
    if (not isinstance(ai, dict) or not isinstance(ai.get('enabled'), bool)
            or not isinstance(bootstrap.get('enabled'), bool)
            or not isinstance(barcode.get('tryRotations'), bool)):
        raise ValueError('I flag enabled e tryRotations devono essere booleani JSON')
    if not isinstance(ai.get('model'), str) or not ai['model'].strip():
        raise ValueError('barcode.ai.model deve essere una stringa non vuota')
    if not isinstance(ai.get('timeout'), (int, float)) or not 0 < ai['timeout'] <= 600:
        raise ValueError('barcode.ai.timeout deve essere tra 0 e 600 secondi')
    return settings


def template_path(name):
    # A template name is a filename stem, never a path (including on Linux).
    if (not isinstance(name, str) or not name.strip() or name != name.strip()
            or any(c in name for c in '<>:"/\\|?*')
            or any(ord(c) < 32 for c in name) or name in (".", "..")
            or name.endswith((".", " "))):
        raise ValueError("Nome template non valido per un file")
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
        raise ValueError("Nome template riservato")
    path = TEMPLATE_DIR / f"{name}.json"
    if path.resolve().parent != TEMPLATE_DIR.resolve() or path.is_symlink():
        raise ValueError("Percorso template non valido")
    return path


def resolve_template(name, customer_name=None):
    if not name:
        return None, None
    path = template_path(name)
    if path.exists():
        try:
            template = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Template non valido: {path}, riga {exc.lineno}: {exc.msg}") from exc
        if not isinstance(template, dict) or template.get("name") != name:
            raise ValueError(f"Il template in {path} deve avere name={name!r}")
        if customer_name and template.get("customer_file", "UNKNOWN") != customer_name:
            raise ValueError(f"Template su file '{name}' appartiene a un altro cliente")
        return template, "file"
    from db import db
    template = db.get_template_by_name(name, customer_name=customer_name)
    return template, "db" if template else None


def write_template_file(template, overwrite=False):
    path = template_path(template.get("name"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"Il file {path.name} esiste gia'")
        backup = path.with_suffix(".json.bak")
        if backup.is_symlink():
            raise ValueError("Backup template non valido")
        shutil.copy2(path, backup)
    atomic_json(path, template)
    return path


def list_templates(customer_name=None):
    from db import db
    templates = {t["name"]: (t, "db") for t in db.get_all_templates(customer_name=customer_name)}
    if TEMPLATE_DIR.exists():
        for path in sorted(TEMPLATE_DIR.glob("*.json")):
            # A file shadows the DB, even when filtered by another customer.
            templates.pop(path.stem, None)
            template, source = resolve_template(path.stem)
            if not customer_name or template.get("customer_file", "UNKNOWN") == customer_name:
                templates[path.stem] = (template, source)
    return list(templates.values())