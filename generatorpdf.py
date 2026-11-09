import io
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import white, black
from reportlab.graphics.barcode import code128
from reportlab.pdfgen import canvas

# Utilità per la generazione di un PDF con barcode Code 128 su ogni pagina  
# utilizzato per testare il programma di split PDF con barcode


# ==============================================================================
# CONFIGURAZIONE
# ==============================================================================
INPUT_PDF = "originale.pdf"          # Nome del tuo file PDF originale
OUTPUT_PDF = "modulo_1000_pagine.pdf"  # Nome del PDF finale
PREFIX = "S"
START_NUM = 830                       # Pagina 1: S0000830
NUM_PAGINE = 1000                      # Pagine totali
PAD_LENGTH = 7                        # 830 -> 0000830

# ==============================================================================
# COORDINATE ESATTE DELL'AREA BARCODE
# (Origine 0,0 in basso a sinistra della pagina A4)
# ==============================================================================
# La toppa bianca copre TUTTA l'area tra Y=672 e Y=740:
# Cancella al 100% il vecchio barcode e il vecchio testo,
# senza toccare la scritta "- Tracciabilità presta" che si trova sopra (a Y >= 742).
BOX_X = 400
BOX_Y = 672
BOX_WIDTH = 135
BOX_HEIGHT = 68

CENTER_X = BOX_X + (BOX_WIDTH / 2.0)  # Centro orizzontale (467.5 pt)

# Parametri barcode Code 128
BARCODE_BASE_Y = 702    # Altezza a cui poggia il barcode
BAR_HEIGHT = 22         # Altezza delle barre
BAR_WIDTH = 0.85        # Spessore barra Code 128 (nitido e leggibile)
FONT_SIZE = 8           # Dimensione del testo


def crea_overlay_barcode(codice_testo, page_width, page_height):
    """Crea una pagina overlay che cancella il vecchio barcode e disegna il nuovo Code 128."""
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=(page_width, page_height))

    # 1. Cancella completamente il vecchio barcode e il vecchio testo
    can.setFillColor(white)
    can.setStrokeColor(white)
    can.rect(BOX_X, BOX_Y, BOX_WIDTH, BOX_HEIGHT, fill=True, stroke=False)

    # 2. Ripristina il colore nero per disegnare il nuovo barcode
    can.setFillColor(black)
    can.setStrokeColor(black)

    # 3. Genera il barcode CODE 128 vettoriale relativo al codice di QUESTA pagina
    bc = code128.Code128(
        codice_testo,
        barHeight=BAR_HEIGHT,
        barWidth=BAR_WIDTH,
        humanReadable=True,
        quiet=0
    )
    bc.fontSize = FONT_SIZE
    bc.fontName = "Helvetica"

    # Centra il barcode all'interno dell'area bianca
    bc_x = CENTER_X - (bc.width / 2.0)
    bc.drawOn(can, bc_x, BARCODE_BASE_Y)

    can.save()
    packet.seek(0)
    return PdfReader(packet).pages[0]


def main():
    with open(INPUT_PDF, "rb") as f:
        template_bytes = f.read()

    reader_init = PdfReader(io.BytesIO(template_bytes))
    template_page = reader_init.pages[0]
    page_width = float(template_page.mediabox.width)
    page_height = float(template_page.mediabox.height)

    writer = PdfWriter()

    primo = f"{PREFIX}{str(START_NUM).zfill(PAD_LENGTH)}"
    ultimo = f"{PREFIX}{str(START_NUM + NUM_PAGINE - 1).zfill(PAD_LENGTH)}"
    print(f"Generazione Code 128 di {NUM_PAGINE} pagine in corso (da {primo} a {ultimo})...")

    for i in range(NUM_PAGINE):
        numero = START_NUM + i
        codice = f"{PREFIX}{str(numero).zfill(PAD_LENGTH)}"

        # Crea l'overlay con il barcode Code 128 specifico per questa pagina
        overlay = crea_overlay_barcode(codice, page_width, page_height)

        # Ricarica una copia fresca della pagina originale
        pagina_pulita = PdfReader(io.BytesIO(template_bytes)).pages[0]

        # Sovrappone il nuovo barcode
        pagina_nel_writer = writer.add_page(pagina_pulita)
        pagina_nel_writer.merge_page(overlay)

        if (i + 1) % 10 == 0 or (i + 1) == NUM_PAGINE:
            print(f"  -> Pagina {i+1:3d}/{NUM_PAGINE}: barcode Code 128 [{codice}]")

    with open(OUTPUT_PDF, "wb") as f_out:
        writer.write(f_out)

    print(f"\nOperazione completata con successo! File salvato in: {OUTPUT_PDF}")


if __name__ == "__main__":
    main()