"""
Extrae texto, imágenes y fondo de cada página de un PDF.
"""
import fitz  # PyMuPDF
from dataclasses import dataclass
from typing import List, Tuple, Iterator


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
    background_png: bytes          # Página completa renderizada como PNG
    text_lines: List[TextLine]     # Líneas de texto extraídas
    embedded_images: List[EmbeddedImage]  # Imágenes embebidas con posición
    width_pt: float                # Ancho de página en puntos PDF
    height_pt: float               # Alto de página en puntos PDF


class PDFProcessor:
    RENDER_DPI = 150  # Resolución del fondo renderizado

    def __init__(self, pdf_path: str):
        self.doc = fitz.open(pdf_path)
        self.page_count = len(self.doc)

    def process_pages(self) -> Iterator[PageData]:
        for i in range(self.page_count):
            yield self._process_page(self.doc[i])

    def _process_page(self, page: fitz.Page) -> PageData:
        return PageData(
            background_png=self._render(page),
            text_lines=self._extract_text(page),
            embedded_images=self._extract_images(page),
            width_pt=page.rect.width,
            height_pt=page.rect.height,
        )

    def _render(self, page: fitz.Page) -> bytes:
        """Renderiza la página completa como PNG (fondo de slide)."""
        mat = fitz.Matrix(self.RENDER_DPI / 72, self.RENDER_DPI / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return pix.tobytes("png")

    def _extract_text(self, page: fitz.Page) -> List[TextLine]:
        """Extrae líneas de texto con posición y estilo."""
        lines = []
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

        for block in blocks:
            if block.get("type") != 0:  # solo bloques de texto
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue

                # Unir todos los spans de la línea en un solo texto
                text = "".join(s.get("text", "") for s in spans)
                if not text.strip():
                    continue

                # Tomar el span dominante (mayor font size) para el estilo
                dominant = max(spans, key=lambda s: s.get("size", 0))
                flags = dominant.get("flags", 0)

                lines.append(TextLine(
                    text=text,
                    bbox=tuple(line["bbox"]),
                    font_size=dominant.get("size", 12),
                    font_name=dominant.get("font", ""),
                    color=dominant.get("color", 0),
                    bold=bool(flags & 16),   # bit 4 = negrita
                    italic=bool(flags & 2),  # bit 1 = cursiva
                ))

        return lines

    def _extract_images(self, page: fitz.Page) -> List[EmbeddedImage]:
        """Extrae imágenes embebidas con su posición en la página."""
        images = []
        seen = set()

        # Mapa xref → bbox desde la información de imágenes de la página
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
