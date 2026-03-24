/* ── PDF → Google Slides  |  Frontend logic ── */

const uploadArea   = document.getElementById("upload-area");
const fileInput    = document.getElementById("file-input");
const progressPanel = document.getElementById("progress-panel");
const resultPanel  = document.getElementById("result-panel");
const errorPanel   = document.getElementById("error-panel");

// ── Drag & drop ──────────────────────────────────────────────

if (uploadArea) {
  uploadArea.addEventListener("dragover", (e) => {
    e.preventDefault();
    uploadArea.classList.add("drag-over");
  });

  uploadArea.addEventListener("dragleave", () => {
    uploadArea.classList.remove("drag-over");
  });

  uploadArea.addEventListener("drop", (e) => {
    e.preventDefault();
    uploadArea.classList.remove("drag-over");
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  });

  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) handleFile(fileInput.files[0]);
  });
}

// ── Upload & conversión ──────────────────────────────────────

async function handleFile(file) {
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    showError("El archivo debe ser un PDF.");
    return;
  }

  showProgress(file.name);

  const formData = new FormData();
  formData.append("file", file);

  let res;
  try {
    res = await fetch("/upload", { method: "POST", body: formData });
  } catch {
    showError("No se pudo conectar con el servidor.");
    return;
  }

  if (res.status === 401) {
    const data = await res.json();
    if (data.error === "not_authenticated") {
      showError('Sesión expirada. <a href="/auth">Vuelve a conectar tu cuenta de Google</a>.');
      return;
    }
  }

  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    showError(data.error || "Error al subir el archivo.");
    return;
  }

  const { job_id } = await res.json();
  listenProgress(job_id);
}

// ── Server-Sent Events ───────────────────────────────────────

function listenProgress(jobId) {
  const evtSource = new EventSource(`/progress/${jobId}`);

  evtSource.onmessage = (e) => {
    const job = JSON.parse(e.data);

    setProgress(job.progress, job.message);

    if (job.status === "done") {
      evtSource.close();
      showResult(job.url);
    } else if (job.status === "error") {
      evtSource.close();
      showError(job.message);
    }
  };

  evtSource.onerror = () => {
    evtSource.close();
    showError("Se perdió la conexión con el servidor.");
  };
}

// ── UI helpers ───────────────────────────────────────────────

function showProgress(fileName) {
  hide(uploadArea);
  hide(resultPanel);
  hide(errorPanel);
  show(progressPanel);

  document.getElementById("file-name").textContent = fileName;
  document.getElementById("status-badge").textContent = "Procesando";
  document.getElementById("status-badge").className = "badge badge-processing";
  setProgress(0, "Iniciando...");
}

function setProgress(pct, message) {
  document.getElementById("progress-bar").style.width = pct + "%";
  document.getElementById("progress-message").textContent = message;
}

function showResult(url) {
  setProgress(100, "¡Listo!");
  document.getElementById("status-badge").textContent = "Completado";
  document.getElementById("status-badge").className = "badge badge-done";

  document.getElementById("slides-link").href = url;

  setTimeout(() => {
    hide(progressPanel);
    show(resultPanel);
  }, 600);
}

function showError(message) {
  hide(uploadArea);
  hide(progressPanel);
  hide(resultPanel);
  show(errorPanel);

  document.getElementById("error-message").innerHTML = message;
}

function resetUI() {
  hide(progressPanel);
  hide(resultPanel);
  hide(errorPanel);
  if (uploadArea) show(uploadArea);
  if (fileInput) fileInput.value = "";
}

function show(el) { if (el) el.classList.remove("hidden"); }
function hide(el) { if (el) el.classList.add("hidden"); }
