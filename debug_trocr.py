"""
Debug TrOCR: prendi un'immagine + zona (pixel), salva i crop che entrano nel
modello e stampa il testo estratto. Serve per capire PERCHE' TrOCR non tira fuori
testo: quasi sempre il problema e' il crop che entra deformato/vuoto.

Uso:
  # zona esplicita in pixel:
  python debug_trocr.py path/immagine.png --x 100 --y 200 --w 600 --h 60

  # zona in permille (0-1000, come i template):
  python debug_trocr.py path/immagine.png --x 100 --y 200 --w 600 --h 60 --permille

  # forza modello / disattiva preprocess:
  python debug_trocr.py img.png --x .. --model microsoft/trocr-base-printed --no-preprocess

Output crop salvati in ./templates/draft_ocr/debug_trocr/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

import image_ocr


OUT_DIR = Path("templates/draft_ocr/debug_trocr")


def main():
    ap = argparse.ArgumentParser(description="Debug TrOCR su una zona immagine")
    ap.add_argument("image", help="path immagine")
    ap.add_argument("--x", type=float, required=True)
    ap.add_argument("--y", type=float, required=True)
    ap.add_argument("--w", type=float, required=True)
    ap.add_argument("--h", type=float, required=True)
    ap.add_argument("--permille", action="store_true",
                    help="coordinate in 0-1000 (converti in pixel)")
    ap.add_argument("--model", default="microsoft/trocr-base-printed")
    ap.add_argument("--handwritten", action="store_true",
                    help="scorciatoia per model=microsoft/trocr-base-handwritten")
    ap.add_argument("--no-preprocess", action="store_true")
    ap.add_argument("--no-multiline", action="store_true")
    ap.add_argument("--margin", type=float, default=0.10)
    ap.add_argument("--line-height", type=int, default=64)
    ap.add_argument("--num-beams", type=int, default=8)
    args = ap.parse_args()

    img_path = Path(args.image)
    if not img_path.exists():
        print(f"ERRORE: immagine '{img_path}' non trovata")
        sys.exit(1)

    image = Image.open(img_path)
    image.load()
    iw, ih = image.size
    print(f"Immagine: {img_path.name} = {iw}x{ih}px")

    x, y, w, h = args.x, args.y, args.w, args.h
    if args.permille:
        x = x / 1000.0 * iw
        y = y / 1000.0 * ih
        w = w / 1000.0 * iw
        h = h / 1000.0 * ih
    x, y, w, h = int(x), int(y), int(w), int(h)
    print(f"Zona pixel: x={x} y={y} w={w} h={h}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    model_name = "microsoft/trocr-base-handwritten" if args.handwritten else args.model
    preprocess = not args.no_preprocess
    multiline = not args.no_multiline

    # Ricostruisco il crop come fa htr_zone_trocr, ma salvo su disco
    mx = max(3, int(w * args.margin))
    my = max(3, int(h * args.margin))
    x1 = max(0, x - mx)
    y1 = max(0, y - my)
    x2 = min(iw, x + w + mx)
    y2 = min(ih, y + h + my)
    cropped = image.crop((x1, y1, x2, y2))
    crop_path = OUT_DIR / "01_crop_raw.png"
    cropped.save(crop_path)
    print(f"[debug] crop grezzo salvato -> {crop_path} ({cropped.size[0]}x{cropped.size[1]})")

    cw, ch = cropped.size

    # Segmentazione righe
    line_boxes = []
    if multiline:
        try:
            line_boxes = image_ocr._segment_lines(cropped)
        except Exception as e:
            print(f"[debug] segmentazione fallita: {e}")
    if not line_boxes:
        line_boxes = [(0, ch)]
    print(f"[debug] righe rilevate: {len(line_boxes)} -> {line_boxes}")

    # Salva ogni riga preprocessata (quella che entra nel modello)
    for idx, (top, bottom) in enumerate(line_boxes):
        line_crop = cropped.crop((0, top, cw, bottom))
        prepared = image_ocr._prep_line_for_trocr(
            line_crop, preprocess, target_h=args.line_height
        )
        p = OUT_DIR / f"02_line_{idx:02d}_input.png"
        prepared.save(p)
        print(f"[debug] riga {idx} input modello -> {p} ({prepared.size[0]}x{prepared.size[1]})")

    # Ora esegui la vera htr_zone_trocr con la stessa config
    cfg = {
        "margin": args.margin,
        "model": model_name,
        "preprocess": preprocess,
        "multiline": multiline,
        "lineHeight": args.line_height,
        "num_beams": args.num_beams,
        "max_length": 64,
    }
    print(f"\n[debug] eseguo TrOCR con config: {cfg}\n")
    text = image_ocr.htr_zone_trocr(image, x, y, w, h, config=cfg)

    print("\n===== RISULTATO =====")
    print(repr(text))
    print("=====================")
    print(f"\nGuarda i crop in: {OUT_DIR.resolve()}")
    print("Se i crop input sono deformati/vuoti/neri -> il problema e' il preprocess o le coordinate.")


if __name__ == "__main__":
    main()
