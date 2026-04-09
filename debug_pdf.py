"""
Diagnóstico: muestra cuánto texto puede extraer PyMuPDF del PDF.
Uso: python debug_pdf.py "ruta/al/archivo.pdf"
"""
import sys
import fitz  # PyMuPDF

if len(sys.argv) < 2:
    print("Uso: python debug_pdf.py \"ruta/al/archivo.pdf\"")
    sys.exit(1)

pdf_path = sys.argv[1]
doc = fitz.open(pdf_path)

print(f"\nPDF: {pdf_path}")
print(f"Páginas: {len(doc)}")
print("=" * 60)

total_lines = 0

for i, page in enumerate(doc):
    # Intentar extracción normal
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
    lines = []
    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(s.get("text", "") for s in line.get("spans", []))
            if text.strip():
                lines.append(text.strip())

    print(f"\n--- Página {i + 1} → {len(lines)} líneas extraídas ---")
    for ln in lines[:8]:
        print(f"  • {ln[:90]}")
    if len(lines) > 8:
        print(f"  ... y {len(lines) - 8} líneas más")

    total_lines += len(lines)

print("\n" + "=" * 60)
print(f"TOTAL: {total_lines} líneas de texto extraíbles en todo el PDF")
print()

if total_lines == 0:
    print("❌ DIAGNÓSTICO: El PDF NO tiene texto extraíble.")
    print("   El texto está rasterizado (grabado en imágenes).")
    print("   → Para hacer el texto editable se necesita OCR.")
else:
    print("✅ DIAGNÓSTICO: El PDF SÍ tiene texto extraíble.")
    print("   Si los text boxes no aparecen en Slides, el problema")
    print("   está en la comunicación con la API de Google.")
