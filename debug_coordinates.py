"""
Script di debug: confronta le zone colonna del template con le posizioni reali
delle parole nel PDF. Supporta sia PDF nativo che PDF scansionato (OCR).
"""
import sys
import json
import pdfplumber
from pathlib import Path
from collections import defaultdict

TEMPLATE_FILE = "templates/YAVEON_INVOICE_SIDEBYSIDE.json"

# Prova diversi PDF
PDF_FILES = ["input/A.pdf", "input/B.pdf", "input/C.pdf", "input/E.pdf"]


def analyze_pdf(pdf_path, tpl):
    table = tpl.get('table', {})
    columns = table.get('columns', [])
    y_min = table.get('y_min', 0)
    y_max = table.get('y_max', 9999)

    # Fix auto: se x_max < x_min, scambia
    fixed_cols = []
    for col in columns:
        c = dict(col)
        if c['x_max'] < c['x_min']:
            c['x_min'], c['x_max'] = c['x_max'], c['x_min']
            print(f"  AUTO-FIX {c['name']}: x_max < x_min -> scambiato")
        fixed_cols.append(c)
    columns = fixed_cols

    words = []
    pdf = pdfplumber.open(pdf_path)
    page = pdf.pages[0]
    width = page.width
    height = page.height

    native_words = page.extract_words()
    if len(native_words) > 0:
        method = "pdfplumber"
        scale_x = 1000.0 / width if width > 0 else 1
        scale_y = 1000.0 / height if height > 0 else 1
        for w in native_words:
            words.append({
                'text': w['text'],
                'x0': w['x0'] * scale_x,
                'top': w['top'] * scale_y,
            })
        print(f"  Metodo: pdfplumber ({len(native_words)} parole, pag={width:.0f}x{height:.0f}pt)")
    else:
        method = "OCR"
        from PIL import Image as PILImage
        img_pil = page.to_image(resolution=200).original
        from image_ocr import _ocr_page_words
        ocr_words, _, degrees = _ocr_page_words(img_pil)
        if degrees:
            print(f"  Auto-rotate: {degrees}°")
        words = ocr_words
        iw, ih = img_pil.size
        print(f"  Metodo: OCR ({len(words)} parole, img={iw}x{ih}px)")

    pdf.close()

    if not words:
        print("  NESSUNA PAROLA!\n")
        return

    lines = defaultdict(list)
    for w in words:
        key = round(w['top'], 0)
        lines[key].append(w)

    row_count = 0
    stats = {c['name']: {'rows': 0, 'words': 0} for c in columns}
    all_missed = []

    for top in sorted(lines.keys()):
        if top < y_min or top > y_max:
            continue
        row_count += 1
        row = sorted(lines[top], key=lambda w: w['x0'])

        for col in columns:
            cap = [w['text'] for w in row
                   if col['x_min'] <= round(w['x0']) < col['x_max']]
            if cap:
                stats[col['name']]['rows'] += 1
                stats[col['name']]['words'] += len(cap)

        # Righe con parole nella zona table ma nessuna colonna matcha
        all_x = [round(w['x0']) for w in row]
        any_match = any(
            col['x_min'] <= x < col['x_max']
            for x in all_x
            for col in columns
        )
        if not any_match and all_x:
            text = " ".join(w['text'] for w in row)
            all_missed.append((top, all_x, text[:80]))

    print(f"  Righe in zona table: {row_count}")
    for col in columns:
        s = stats[col['name']]
        print(f"    {col['name']:6s}: {s['rows']:>3d} righe, {s['words']:>3d} parole  [{col['x_min']}-{col['x_max']}]")

    if all_missed:
        print(f"  Righe NON catturate da nessuna colonna: {len(all_missed)}")
        for top, xs, txt in all_missed[:5]:
            print(f"    Y={top:>4.0f} X={min(xs)}-{max(xs)}: {txt}")

    all_x0 = [round(w['x0']) for top in sorted(lines.keys())
              if y_min <= top <= y_max for w in lines[top]]
    if all_x0:
        print(f"  Range X parole: {min(all_x0)}-{max(all_x0)}")
    print()


def main():
    with open(TEMPLATE_FILE, encoding='utf-8') as f:
        tpl = json.load(f)

    table = tpl.get('table', {})
    columns = table.get('columns', [])

    print(f"=== TEMPLATE: {tpl.get('name')} ===")
    print(f"Table Y: {table.get('y_min')}-{table.get('y_max')}")
    print(f"Colonne:")
    for col in columns:
        w = col['x_max'] - col['x_min']
        flag = " <<< x_max < x_min!" if w < 0 else ""
        print(f"  {col['name']:6s} [{col['x_min']:>4d}-{col['x_max']:>4d}] (w={w}){flag}")
    print()

    for pdf_file in PDF_FILES:
        path = Path(pdf_file)
        if not path.exists():
            continue
        print(f"=== {path.name} ===")
        try:
            analyze_pdf(str(path), tpl)
        except Exception as e:
            print(f"  ERRORE: {e}\n")


if __name__ == "__main__":
    main()