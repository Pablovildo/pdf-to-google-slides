# PDF → Google Slides

Aplicación web que convierte archivos PDF en presentaciones de Google Slides editables.

**Estrategia de conversión:**
- **Fondo**: cada página se renderiza como imagen PNG (preserva el diseño visual original)
- **Texto**: se extrae como objetos de texto editables y transparentes posicionados sobre el fondo
- **Imágenes embebidas**: se agregan como objetos independientes y seleccionables

---

## Requisitos previos

- Python 3.11+
- Una cuenta de Google

---

## 1. Configurar Google Cloud (solo la primera vez)

### 1.1 Crear proyecto

1. Ir a [console.cloud.google.com](https://console.cloud.google.com/)
2. Clic en el selector de proyecto (arriba a la izquierda) → **Nuevo proyecto**
3. Darle un nombre (ej. `pdf-to-slides`) → **Crear**

### 1.2 Habilitar las APIs necesarias

En el menú lateral: **APIs y servicios → Biblioteca**

Buscar y habilitar:
- **Google Slides API** → Habilitar
- **Google Drive API** → Habilitar

### 1.3 Configurar la pantalla de consentimiento OAuth

1. **APIs y servicios → Pantalla de consentimiento de OAuth**
2. Tipo de usuario: **Externo** → Crear
3. Completar:
   - Nombre de la app: `PDF to Slides`
   - Correo de soporte: tu email
   - Correo de desarrollador: tu email
4. Clic en **Guardar y continuar** (en las secciones de permisos y usuarios de prueba se puede dejar vacío por ahora)
5. En **Usuarios de prueba** → agregar tu propio email de Google

### 1.4 Crear credenciales OAuth

1. **APIs y servicios → Credenciales** → **Crear credenciales → ID de cliente de OAuth**
2. Tipo de aplicación: **Aplicación web**
3. Nombre: `PDF to Slides Web`
4. En **URIs de redireccionamiento autorizados** → agregar:
   ```
   http://localhost:5000/auth/callback
   ```
5. Clic en **Crear**
6. En el diálogo que aparece, clic en **Descargar JSON**
7. Renombrar el archivo descargado a `credentials.json`
8. Moverlo a la carpeta raíz de este proyecto (junto a `app.py`)

---

## 2. Instalar Tesseract OCR

Muchos PDFs de presentaciones tienen el texto **rasterizado** (grabado como imagen).
La app usa Tesseract OCR para leer ese texto automáticamente.

### Windows
1. Descargar el instalador desde: https://github.com/UB-Mannheim/tesseract/wiki
   - Elegir la versión más reciente: `tesseract-ocr-w64-setup-X.X.X.exe`
2. Ejecutar el instalador y dejar la ruta por defecto:
   `C:\Program Files\Tesseract-OCR\`
3. La app lo detecta automáticamente — no se necesita configuración adicional.

### macOS
```bash
brew install tesseract
```

### Linux
```bash
sudo apt install tesseract-ocr
```

---

## 3. Instalar dependencias Python

```bash
pip install -r requirements.txt
```

---

## 3. Ejecutar la aplicación

```bash
python app.py
```

Abrir en el navegador: [http://localhost:5000](http://localhost:5000)

---

## 4. Uso

1. La primera vez, hacer clic en **Conectar con Google** y autorizar la app
2. Arrastrar un archivo PDF al área de carga o hacer clic en **Seleccionar archivo**
3. Esperar a que finalice la conversión (se muestra el progreso en tiempo real)
4. Hacer clic en **Abrir en Google Slides**

---

## Notas

- Las imágenes temporales del fondo se guardan en la raíz de tu Google Drive con nombres como `bg_p0.png`, `img_0_0.png`, etc. Se pueden eliminar manualmente después si se desea.
- Si el PDF tiene texto rasterizado (dentro de imágenes), ese texto no será extraíble como texto editable, pero sí aparecerá en el fondo visual.
- El texto extraído usa fuentes aproximadas (Georgia para serif, Open Sans para sans-serif).

---

## Estructura del proyecto

```
.
├── app.py              # Servidor Flask, rutas, OAuth, gestión de jobs
├── pdf_processor.py    # Extracción de texto e imágenes con PyMuPDF
├── slides_creator.py   # Creación de la presentación via Google Slides API
├── requirements.txt
├── credentials.json    # (tú lo creas – ver paso 1.4)
├── token.json          # (se genera automáticamente al autenticarse)
├── templates/
│   └── index.html
└── static/
    ├── style.css
    └── app.js
```
