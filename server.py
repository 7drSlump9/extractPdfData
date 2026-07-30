"""
Flask API server per l'editor visuale template.
Endpoint: login, template CRUD, test estrazione.
"""

import json
from pathlib import Path
from flask import Flask, request, jsonify, session, send_from_directory
from functools import wraps

from db import db
from template_engine import match_template, apply_template

app = Flask(__name__)
app.secret_key = "cambiami-in-produzione-1234"  # TODO: env var

TEMPLATES_DIR = Path(__file__).parent / "templates"
OUTPUT_DIR = Path(__file__).parent / "output"

# Utente hardcoded (poi tabella users)
ADMIN_USER = {"username": "admin", "password": "admin"}


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return jsonify({"error": "Non autenticato"}), 401
        return f(*args, **kwargs)
    return decorated


# ──────────────────────── AUTH ────────────────────────

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Body JSON richiesto"}), 400
    username = data.get("username", "")
    password = data.get("password", "")
    if username == ADMIN_USER["username"] and password == ADMIN_USER["password"]:
        session["user"] = username
        return jsonify({"ok": True, "username": username})
    return jsonify({"error": "Credenziali errate"}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("user", None)
    return jsonify({"ok": True})


@app.route("/api/whoami", methods=["GET"])
def api_whoami():
    user = session.get("user")
    return jsonify({"logged_in": bool(user), "username": user})


# ──────────────────────── TEMPLATE CRUD ────────────────────────

@app.route("/api/templates", methods=["GET"])
@login_required
def api_list_templates():
    customer = request.args.get("customer_name")
    templates = db.get_all_templates(customer_name=customer or None)
    # Solo name, description, customer_name (niente json_data pieno)
    result = []
    for t in templates:
        result.append({
            "name": t.get("name", "?"),
            "description": t.get("description", ""),
            "customer_name": t.get("customer_file", "UNKNOWN"),
            "signature_count": len(t.get("signature", [])),
        })
    return jsonify(result)


@app.route("/api/templates/<name>", methods=["GET"])
@login_required
def api_get_template(name):
    template = db.get_template_by_name(name)
    if not template:
        return jsonify({"error": "Template non trovato"}), 404
    return jsonify(template)


@app.route("/api/templates", methods=["POST"])
@login_required
def api_save_template():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Body JSON richiesto"}), 400
    name = data.get("name")
    if not name:
        return jsonify({"error": "Campo 'name' obbligatorio"}), 400
    customer = data.get("customer_file", data.get("customer_name", "UNKNOWN"))
    try:
        result = db.save_template(data, customer_name=customer)
        return jsonify({"ok": True, "db": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/templates/<name>", methods=["DELETE"])
@login_required
def api_delete_template(name):
    """Soft-delete: imposta is_active=False nel DB."""
    try:
        result = db.deactivate_template(name)
        if result:
            return jsonify({"ok": True, "deactivated": name})
        return jsonify({"error": "Template non trovato nel DB"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ──────────────────────── PDF TEST ────────────────────────

@app.route("/api/test-pdf", methods=["POST"])
@login_required
def api_test_pdf():
    """
    Testa un template direttamente su un PDF caricato.
    Riceve multipart/form-data: file (PDF) + template (JSON string).
    Usa pdfplumber per estrarre testo e applica il template.
    """
    if 'file' not in request.files:
        return jsonify({"error": "File PDF richiesto"}), 400
    file = request.files['file']
    template_str = request.form.get('template', '')
    if not template_str:
        return jsonify({"error": "Parametro 'template' richiesto"}), 400

    try:
        template = json.loads(template_str)
    except json.JSONDecodeError as e:
        return jsonify({"error": f"Template JSON non valido: {e}"}), 400

    try:
        import pdfplumber
        import io
        pdf_bytes = file.read()
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            from template_engine import get_lines, line_text, apply_template
            all_lines = []
            y_offset = 0.0
            text_parts = []
            for page in pdf.pages:
                page = page.dedupe_chars(tolerance=1)
                page_lines = get_lines(page)
                for top, row in page_lines:
                    all_lines.append((top + y_offset, row))
                text_parts.append("\n".join(line_text(row) for _, row in page_lines))
                y_offset += float(page.height or 0) + 10.0
            full_text = "\n".join(text_parts)

        result = apply_template(template, all_lines, full_text)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ──────────────────────── PDF TEST (legacy) ────────────────────────

@app.route("/api/test-template", methods=["POST"])
@login_required
def api_test_template():
    """
    Testa un template su un PDF (va passato testo/lines).
    Frontend manda: {template: {...}, lines: [...], full_text: "..."}
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Body JSON richiesto"}), 400
    template = data.get("template")
    lines = data.get("lines")
    full_text = data.get("full_text", "")
    if not template or not lines:
        return jsonify({"error": "Campi 'template' e 'lines' obbligatori"}), 400
    try:
        result = apply_template(template, lines, full_text)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ──────────────────────── REACT STATIC (produzione) ────────────────────────

WEB_BUILD = Path(__file__).parent / "web" / "build"


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_react(path):
    if WEB_BUILD.exists() and (WEB_BUILD / "index.html").exists():
        if path and (WEB_BUILD / path).exists():
            return send_from_directory(WEB_BUILD, path)
        return send_from_directory(WEB_BUILD, "index.html")
    return jsonify({"message": "API server running. React build non trovato."})


# ──────────────────────── MAIN ────────────────────────

if __name__ == "__main__":
    print("Avvio Flask API server su http://localhost:8000")
    app.run(host="0.0.0.0", port=8000, debug=True)