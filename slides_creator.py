"""
Crea una presentación de Google Slides a partir de los datos extraídos del PDF.

Estrategia por slide:
  1. Fondo  → imagen PNG renderizada de la página completa (background fill)
  2. Texto  → text boxes con borde punteado posicionados sobre el fondo (editables y visibles)
  3. Imágenes embebidas → objetos imagen independientes (seleccionables)
"""
import io
import re
import time
import unicodedata
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2.credentials import Credentials
from pdf_processor import PageData, TextLine, EmbeddedImage

# Dimensiones estándar de Google Slides 16:9 en EMUs
SLIDE_W = 9_144_000   # 10 pulgadas
SLIDE_H = 5_143_500   # 5.625 pulgadas
SLIDE_W_PT = SLIDE_W / 12_700  # 720 pt

# Keywords para resolución de familia de fuentes
_SERIF = ("serif", "times", "georgia", "playfair", "garamond", "palatino", "book")
_MONO  = ("mono", "courier", "consol", "code", "fixed")


def _font_family(font_name: str) -> str:
    n = font_name.lower()
    if any(k in n for k in _MONO):
        return "Courier New"
    if any(k in n for k in _SERIF):
        return "Georgia"
    return "Open Sans"


def _rgb(color_int: int) -> dict:
    """Convierte entero 0xRRGGBB → dict {red, green, blue} en [0, 1]."""
    return {
        "red":   ((color_int >> 16) & 0xFF) / 255,
        "green": ((color_int >>  8) & 0xFF) / 255,
        "blue":  ( color_int        & 0xFF) / 255,
    }


def _bbox_emu(bbox, pw: float, ph: float) -> dict:
    """Escala un bbox de puntos PDF a EMUs del slide."""
    x0, y0, x1, y1 = bbox
    return {
        "x": round(x0 / pw * SLIDE_W),
        "y": round(y0 / ph * SLIDE_H),
        "w": max(round((x1 - x0) / pw * SLIDE_W), 20_000),
        "h": max(round((y1 - y0) / ph * SLIDE_H), 20_000),
    }


def _clean_text(text: str) -> str:
    """
    Elimina caracteres de control y bytes nulos que rompen la API de Slides.
    Conserva espacios, saltos de línea y caracteres imprimibles normales.
    """
    cleaned = "".join(
        ch for ch in text
        if ch in ("\n", "\t") or unicodedata.category(ch)[0] not in ("C", "Z") or ch == " "
    )
    # Eliminar saltos de línea múltiples y espacios sobrantes
    cleaned = re.sub(r"[\r\n]+", " ", cleaned)
    return cleaned.strip()


def _chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


class SlidesCreator:
    def __init__(self, creds: Credentials):
        self.slides = build("slides", "v1", credentials=creds)
        self.drive  = build("drive",  "v3", credentials=creds)

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def create_presentation(self, title: str) -> str:
        """Crea presentación vacía y devuelve su ID."""
        pres = self.slides.presentations().create(body={"title": title}).execute()
        pid = pres["presentationId"]

        # Eliminar el slide en blanco que Google crea por defecto
        default = pres.get("slides", [])
        if default:
            self._batch(pid, [{"deleteObject": {"objectId": default[0]["objectId"]}}])

        return pid

    def add_page(self, pid: str, page: PageData, index: int) -> dict:
        """
        Agrega un slide con fondo, texto e imágenes.
        Devuelve estadísticas: {"text_boxes": N, "images": N}.
        """
        slide_id = f"slide_{index}"
        pw, ph = page.width_pt, page.height_pt

        # 1. Crear slide en blanco
        self._batch(pid, [{
            "createSlide": {
                "objectId": slide_id,
                "insertionIndex": index,
                "slideLayoutReference": {"predefinedLayout": "BLANK"},
            }
        }])

        # 2. Subir imagen de fondo y asignarla al slide
        bg_url = self._upload(page.background_png, f"bg_p{index}.png", "image/png")
        self._batch(pid, [{
            "updatePageProperties": {
                "objectId": slide_id,
                "pageProperties": {
                    "pageBackgroundFill": {
                        "stretchedPictureFill": {"contentUrl": bg_url}
                    }
                },
                "fields": "pageBackgroundFill",
            }
        }])

        # 3. Agregar text boxes (capa de texto editable)
        n_text = self._add_text_boxes(pid, slide_id, page.text_lines, pw, ph, index)

        # 4. Agregar imágenes embebidas como objetos independientes
        n_img = self._add_embedded_images(pid, slide_id, page.embedded_images, pw, ph, index)

        return {"text_boxes": n_text, "images": n_img}

    # ------------------------------------------------------------------
    # Capa de texto
    # ------------------------------------------------------------------

    def _add_text_boxes(self, pid, slide_id, lines, pw, ph, page_idx) -> int:
        """
        Crea un text box por cada línea de texto extraída del PDF.

        Cada text box se envía en su propio batchUpdate para aislar errores:
        si una línea tiene caracteres que rompen la API, las demás no se ven afectadas.

        Los text boxes tienen:
          - Fondo transparente (no tapa el diseño del fondo)
          - Borde punteado azul claro (hace visible dónde está el texto editable)
          - Texto con color, fuente y tamaño aproximados al PDF original
        """
        font_scale = SLIDE_W_PT / pw
        created = 0

        for j, line in enumerate(lines):
            text = _clean_text(line.text)
            if not text:
                continue

            oid = f"txt_{page_idx}_{j}"
            pos = _bbox_emu(line.bbox, pw, ph)
            font_pt = max(round(line.font_size * font_scale), 6)

            # 4 operaciones atómicas por text box en una sola llamada a la API
            reqs = [
                # ── 1. Crear la forma TEXT_BOX ──────────────────────────
                {"createShape": {
                    "objectId": oid,
                    "shapeType": "TEXT_BOX",
                    "elementProperties": {
                        "pageObjectId": slide_id,
                        "size": {
                            "width":  {"magnitude": pos["w"], "unit": "EMU"},
                            # Alto generoso para que el texto no quede cortado
                            "height": {"magnitude": pos["h"] + 100_000, "unit": "EMU"},
                        },
                        "transform": {
                            "scaleX": 1, "scaleY": 1,
                            "translateX": pos["x"],
                            "translateY": pos["y"],
                            "unit": "EMU",
                        },
                    },
                }},

                # ── 2. Insertar el texto ─────────────────────────────────
                {"insertText": {
                    "objectId": oid,
                    "text": text,
                }},

                # ── 3. Aplicar estilo de texto ───────────────────────────
                {"updateTextStyle": {
                    "objectId": oid,
                    "style": {
                        "fontSize":        {"magnitude": font_pt, "unit": "PT"},
                        "foregroundColor": {"opaqueColor": {"rgbColor": _rgb(line.color)}},
                        "bold":            line.bold,
                        "italic":          line.italic,
                        "fontFamily":      _font_family(line.font_name),
                    },
                    "fields": "fontSize,foregroundColor,bold,italic,fontFamily",
                }},

                # ── 4. Fondo transparente + borde punteado visible ───────
                #    El borde azul punteado hace que el usuario pueda ver
                #    y hacer clic en los text boxes para editarlos.
                {"updateShapeProperties": {
                    "objectId": oid,
                    "shapeProperties": {
                        "shapeBackgroundFill": {
                            "propertyState": "NOT_RENDERED"   # fondo transparente
                        },
                        "outline": {
                            "propertyState": "RENDERED",
                            "outlineFill": {
                                "solidFill": {
                                    "color": {
                                        "rgbColor": {
                                            "red": 0.27,
                                            "green": 0.51,
                                            "blue": 0.96,
                                        }
                                    },
                                    "alpha": 0.55,   # semi-transparente
                                }
                            },
                            "weight": {"magnitude": 1.0, "unit": "PT"},
                            "dashStyle": "DASH",
                        },
                    },
                    "fields": "shapeBackgroundFill,outline",
                }},
            ]

            # Cada text box en su propio batchUpdate para aislar fallos
            try:
                self._batch(pid, reqs)
                created += 1
            except Exception as e:
                # Registrar el fallo pero continuar con el resto
                print(f"  [warn] text box {oid} omitido: {e}")

            # Pausa mínima para no superar cuotas de la API
            time.sleep(0.05)

        return created

    # ------------------------------------------------------------------
    # Capa de imágenes embebidas
    # ------------------------------------------------------------------

    def _add_embedded_images(self, pid, slide_id, images, pw, ph, page_idx) -> int:
        """Agrega imágenes embebidas del PDF como objetos independientes en el slide."""
        created = 0
        for j, img in enumerate(images):
            mime = "image/jpeg" if img.ext in ("jpg", "jpeg") else f"image/{img.ext}"
            try:
                url = self._upload(img.image_bytes, f"img_{page_idx}_{j}.{img.ext}", mime)
                pos = _bbox_emu(img.bbox, pw, ph)
                self._batch(pid, [{"createImage": {
                    "objectId": f"img_{page_idx}_{j}",
                    "url": url,
                    "elementProperties": {
                        "pageObjectId": slide_id,
                        "size": {
                            "width":  {"magnitude": pos["w"], "unit": "EMU"},
                            "height": {"magnitude": pos["h"], "unit": "EMU"},
                        },
                        "transform": {
                            "scaleX": 1, "scaleY": 1,
                            "translateX": pos["x"],
                            "translateY": pos["y"],
                            "unit": "EMU",
                        },
                    },
                }}])
                created += 1
            except Exception as e:
                print(f"  [warn] imagen img_{page_idx}_{j} omitida: {e}")

        return created

    # ------------------------------------------------------------------
    # Helpers: Drive upload y Slides batchUpdate
    # ------------------------------------------------------------------

    def _upload(self, data: bytes, name: str, mime: str) -> str:
        """Sube archivo a Google Drive y devuelve URL pública."""
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=False)
        f = self.drive.files().create(
            body={"name": name},
            media_body=media,
            fields="id",
        ).execute()
        fid = f["id"]
        # URL pública para que la API de Slides pueda acceder al archivo
        self.drive.permissions().create(
            fileId=fid,
            body={"type": "anyone", "role": "reader"},
        ).execute()
        return f"https://drive.google.com/uc?id={fid}"

    def _batch(self, pid: str, requests: list):
        if requests:
            self.slides.presentations().batchUpdate(
                presentationId=pid,
                body={"requests": requests},
            ).execute()
