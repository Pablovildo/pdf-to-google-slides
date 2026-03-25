"""
PDF → Google Slides  |  Aplicación web Flask
"""
import os
import json
import uuid
import tempfile
import threading
import time

from flask import (
    Flask, render_template, request, jsonify,
    Response, redirect, url_for, session,
)
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow

from pdf_processor import PDFProcessor
from slides_creator import SlidesCreator

# Permitir OAuth sin HTTPS en localhost
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

app = Flask(__name__)
app.secret_key = os.urandom(24)  # solo para session; se regenera en cada reinicio

CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
SCOPES = [
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive.file",
]

# Diccionario en memoria: job_id → estado
jobs: dict = {}


# ──────────────────────────────────────────────
# Helpers de autenticación
# ──────────────────────────────────────────────

def _get_creds() -> Credentials | None:
    """Devuelve credenciales válidas o None."""
    if not os.path.exists(TOKEN_FILE):
        return None
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())
        except Exception:
            return None
    return creds if creds.valid else None


# ──────────────────────────────────────────────
# Rutas
# ──────────────────────────────────────────────

@app.route("/")
def index():
    return render_template(
        "index.html",
        authenticated=_get_creds() is not None,
        has_credentials=os.path.exists(CREDENTIALS_FILE),
    )


@app.route("/auth")
def auth():
    if not os.path.exists(CREDENTIALS_FILE):
        return "Falta credentials.json. Sigue las instrucciones del README.", 400

    flow = Flow.from_client_secrets_file(
        CREDENTIALS_FILE,
        scopes=SCOPES,
        redirect_uri=url_for("auth_callback", _external=True),
    )
    auth_url, state = flow.authorization_url(access_type="offline", prompt="consent")
    session["oauth_state"] = state
    return redirect(auth_url)


@app.route("/auth/callback")
def auth_callback():
    flow = Flow.from_client_secrets_file(
        CREDENTIALS_FILE,
        scopes=SCOPES,
        state=session.get("oauth_state"),
        redirect_uri=url_for("auth_callback", _external=True),
    )
    flow.fetch_token(authorization_response=request.url)
    with open(TOKEN_FILE, "w") as f:
        f.write(flow.credentials.to_json())
    return redirect(url_for("index"))


@app.route("/auth/logout")
def logout():
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
    return redirect(url_for("index"))


@app.route("/upload", methods=["POST"])
def upload():
    creds = _get_creds()
    if not creds:
        return jsonify({"error": "not_authenticated"}), 401

    if "file" not in request.files:
        return jsonify({"error": "No se proporcionó archivo."}), 400

    file = request.files["file"]
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "El archivo debe ser un PDF."}), 400

    # Guardar PDF en archivo temporal
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    file.save(tmp.name)
    tmp.close()

    title = os.path.splitext(file.filename)[0]
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "processing",
        "progress": 0,
        "message": "Iniciando conversión...",
        "url": None,
        "error": None,
    }

    # Convertir credenciales a JSON para pasarlas al hilo (los objetos no son thread-safe)
    creds_json = creds.to_json()
    thread = threading.Thread(
        target=_run_conversion,
        args=(job_id, tmp.name, title, creds_json),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/progress/<job_id>")
def progress(job_id):
    """Server-Sent Events para reportar el progreso de la conversión."""
    def stream():
        while True:
            job = jobs.get(job_id, {
                "status": "error",
                "progress": 0,
                "message": "Trabajo no encontrado.",
                "url": None,
            })
            yield f"data: {json.dumps(job)}\n\n"
            if job["status"] in ("done", "error"):
                break
            time.sleep(0.8)

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ──────────────────────────────────────────────
# Lógica de conversión (corre en hilo separado)
# ──────────────────────────────────────────────

def _run_conversion(job_id: str, pdf_path: str, title: str, creds_json: str):
    def update(msg: str, pct: int):
        jobs[job_id].update({"message": msg, "progress": pct})

    try:
        creds = Credentials.from_authorized_user_info(json.loads(creds_json), SCOPES)
        processor = PDFProcessor(pdf_path)
        creator = SlidesCreator(creds)
        total = processor.page_count

        update(f"Creando presentación ({total} páginas)...", 3)
        presentation_id = creator.create_presentation(title)

        total_text   = 0
        total_imgs   = 0
        ocr_pages    = 0
        failed_pages = []

        for i, page_data in enumerate(processor.process_pages()):
            pct = 5 + int(i / total * 90)
            n_txt = len(page_data.text_lines)
            ocr_label = " [OCR]" if page_data.ocr_used else ""
            update(
                f"Página {i + 1}/{total}{ocr_label} — {n_txt} textos encontrados...",
                pct,
            )
            try:
                stats = creator.add_page(presentation_id, page_data, i)
                total_text += stats["text_boxes"]
                total_imgs += stats["images"]
                if page_data.ocr_used:
                    ocr_pages += 1
            except Exception as page_err:
                # Registrar el fallo pero continuar con las páginas restantes
                failed_pages.append(i + 1)
                print(f"  [warn] página {i + 1} omitida: {page_err}")

        # Armar mensaje de resumen
        warnings = ""
        if failed_pages:
            warnings = f" ⚠️ Páginas omitidas por error: {failed_pages}."

        if total_text == 0 and not failed_pages:
            text_summary = (
                "⚠️ No se extrajo texto. "
                "Verificá que Tesseract OCR está instalado (ver README)."
            )
        elif ocr_pages > 0:
            text_summary = (
                f"✅ {total_text} bloques de texto creados con OCR "
                f"({ocr_pages}/{total} páginas).{warnings}"
            )
        else:
            text_summary = f"✅ {total_text} bloques de texto editables creados.{warnings}"

        url = f"https://docs.google.com/presentation/d/{presentation_id}/edit"
        jobs[job_id].update({
            "status": "done",
            "progress": 100,
            "message": text_summary,
            "url": url,
        })

    except Exception as e:
        jobs[job_id].update({
            "status": "error",
            "progress": 0,
            "message": f"Error durante la conversión: {e}",
        })
    finally:
        try:
            os.unlink(pdf_path)
        except OSError:
            pass


# ──────────────────────────────────────────────
# Entrada
# ──────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True)
