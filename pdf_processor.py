"""
Extrae texto, imágenes y fondo de cada página de un PDF.
Si el texto está rasterizado, se usa OCR como fallback.
"""
import fitz  # PyMuPDF
from dataclasses import dataclass
from typing import List, Tuple, Iterator
from collections import defaultdict

# ── OCR opcional (requiere Tesseract instalado) ──────────────────────────────
try:
    import pytesseract
    from PIL import Image
    import numpy as np

    # Ruta estándar de Tesseract en Windows
    import os, sys
    _WIN_TESS = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if sys.platform == "win32" and os.path.exists(_WIN_TESS):
        pytesseract.pytesseract.tesseract_cmd = _WIN_TESS

    # Verificar que tesseract responde
    pytesseract.get_tesseract_version()
    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class TextLine:
    text: str
    bbox: Tuple[float, float, float, float]  # x0, y0, x1, y1 en puntos
    font_size: float
    font_name: str
    color: int   # entero RGB (ej. 0xFFFFFF = blanco)
    bold: bool
    italic: bool


@dataclass
class EmbeddedImage:
    image_bytes: bytes
    bbox: Tuple[float, float, float, float]
    ext: str  # 'png', 'jpeg', etc.


@dataclass
class PageData:
    background_png: bytes
    text_lines: List[TextLine]
    embedded_images: List[EmbeddedImage]
    width_pt: float
    height_pt: float
    ocr_used: bool = False          # True si se usó OCR para esta página


class PDFProcessor:
    RENDER_DPI = 150    # DPI para imagen de fondo
    OCR_DPI    = 200    # DPI para OCR (mayor = más preciso, más lento)

    def __init__(self, pdf_path: str):
        self.doc = fitz.open(pdf_path)
        self.page_count = len(self.doc)

    def process_pages(self) -> Iterator[PageData]:
        for i in range(self.page_count):
            yield self._process_page(self.doc[i])

    def _process_page(self, page: fitz.Page) -> PageData:
        text_lines = self._extract_text_native(page)
        ocr_used = False

        if not text_lines:
            if OCR_AVAILABLE:
                text_lines = self._extract_text_ocr(page)
                ocr_used = bool(text_lines)
            # Si OCR no está disponible, text_lines queda vacío

        return PageData(
            background_png=self._render(page),
            text_lines=text_lines,
            embedded_images=self._extract_images(page),
            width_pt=page.rect.width,
            height_pt=page.rect.height,
            ocr_used=ocr_used,
        )

    # ── Renderizado ──────────────────────────────────────────────────────────

    def _render(self, page: fitz.Page) -> bytes:
        """Renderiza la página completa como PNG (fondo de slide)."""
        mat = fitz.Matrix(self.RENDER_DPI / 72, self.RENDER_DPI / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return pix.tobytes("png")

    # ── Extracción de texto nativa (PyMuPDF) ─────────────────────────────────

    def _extract_text_native(self, page: fitz.Page) -> List[TextLine]:
        """Extrae líneas de texto con posición y estilo (texto vectorial)."""
        lines = []
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                text = "".join(s.get("text", "") for s in spans)
                if not text.strip():
                    continue
                dominant = max(spans, key=lambda s: s.get("size", 0))
                flags = dominant.get("flags", 0)
                lines.append(TextLine(
                    text=text,
                    bbox=tuple(line["bbox"]),
                    font_size=dominant.get("size", 12),
                    font_name=dominant.get("font", ""),
                    color=dominant.get("color", 0),
                    bold=bool(flags & 16),
                    italic=bool(flags & 2),
                ))
        return lines

    # ── Extracción de texto por OCR (pytesseract) ────────────────────────────

    def _extract_text_ocr(self, page: fitz.Page) -> List[TextLine]:
        """
        Fallback OCR: renderiza la página a alta resolución y lee el texto
        con Tesseract. Maneja fondos oscuros (texto blanco) invirtiendo la
        imagen antes del OCR, y muestrea el color real del texto del original.
        """
        DPI = self.OCR_DPI
        mat = fitz.Matrix(DPI / 72, DPI / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        original_img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        # Detectar fondo oscuro → invertir para que Tesseract lea texto claro
        arr = np.array(original_img)
        is_dark = arr.mean() < 128
        ocr_img = Image.fromarray(255 - arr) if is_dark else original_img

        scale = 72 / DPI  # pixels OCR → puntos PDF

        try:
            data = pytesseract.image_to_data(
                ocr_img,
                output_type=pytesseract.Output.DICT,
                config="--psm 11 --oem 3",
            )
        except Exception:
            return []

        # Agrupar palabras por línea (block_num, par_num, line_num)
        line_groups = defaultdict(list)
        n = len(data["text"])
        for i in range(n):
            text = data["text"][i].strip()
            try:
                conf = int(data["conf"][i])
            except (ValueError, TypeError):
                conf = 0
            if not text or conf < 30:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            line_groups[key].append({
                "text":  text,
                "left":  data["left"][i],
                "top":   data["top"][i],
                "w":     data["width"][i],
                "h":     data["height"][i],
            })

        lines = []
        for key in sorted(line_groups.keys()):
            words = line_groups[key]
            if not words:
                continue

            line_text = " ".join(w["text"] for w in words)
            x0 = min(w["left"]         for w in words)
            y0 = min(w["top"]          for w in words)
            x1 = max(w["left"] + w["w"] for w in words)
            y1 = max(w["top"]  + w["h"] for w in words)

            # Muestrear color del texto desde la imagen ORIGINAL (sin invertir)
            cx = min((x0 + x1) // 2, original_img.width  - 1)
            cy = min((y0 + y1) // 2, original_img.height - 1)
            r, g, b = original_img.getpixel((cx, cy))[:3]
            color = (r << 16) | (g << 8) | b

            bbox_pt = (x0 * scale, y0 * scale, x1 * scale, y1 * scale)
            font_size = max((y1 - y0) * scale * 0.72, 7)

            lines.append(TextLine(
                text=line_text,
                bbox=bbox_pt,
                font_size=font_size,
                font_name="",
                color=color,
                bold=False,
                italic=False,
            ))

        return lines

    # ── Extracción de imágenes embebidas ─────────────────────────────────────

    def _extract_images(self, page: fitz.Page) -> List[EmbeddedImage]:
        images = []
        seen = set()
        info_map = {
            info["xref"]: info
            for info in page.get_image_info(hashes=False)
            if "xref" in info
        }
        for img_ref in page.get_images(full=True):
            xref = img_ref[0]
            if xref in seen or xref not in info_map:
                continue
            seen.add(xref)
            try:
                base_img = self.doc.extract_image(xref)
                bbox = info_map[xref].get("bbox")
                if not bbox:
                    continue
                images.append(EmbeddedImage(
                    image_bytes=base_img["image"],
                    bbox=tuple(bbox),
                    ext=base_img.get("ext", "png"),
                ))
            except Exception:
                continue
        return images

    def close(self):
        self.doc.close()
