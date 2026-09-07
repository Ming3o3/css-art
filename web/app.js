(() => {
  const state = { file: null, html: "", report: null, sourceUrl: "", outputUrl: "" };
  const $ = (id) => document.getElementById(id);
  const fileInput = $("file-input");
  const dropZone = $("drop-zone");
  const chooseButton = $("choose-button");
  const convertForm = $("convert-form");
  const convertButton = $("convert-button");
  const formNote = $("form-note");
  const sourceRow = $("source-row");
  const sourceThumb = $("source-thumb");
  const sourceName = $("source-name");
  const sourceMeta = $("source-meta");
  const emptyPreview = $("empty-preview");
  const artPreview = $("art-preview");
  const loadingOverlay = $("loading-overlay");
  const outputActions = $("output-actions");
  const resultStrip = $("result-strip");
  const errorMessage = $("error-message");

  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  }

  function setError(message) {
    errorMessage.textContent = message || "";
    errorMessage.classList.toggle("is-hidden", !message);
  }

  function localizeError(message) {
    const text = String(message || "");
    if (text.includes("Cannot fit the illustration within")) return "当前体积限制下无法生成此插画，请提高输出上限或降低精度。";
    if (text.includes("HTML exceeds --max-output-mb")) return "生成的 HTML 超过体积上限，请降低精度或提高输出上限。";
    if (text.includes("Animated/multipage inputs are unsupported")) return "不支持动画或多帧图片，请先导出为单帧图片。";
    if (text.includes("File already exists")) return "输出文件已经存在，请更换文件或使用覆盖选项。";
    return text;
  }

  function setFile(file) {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setError("请选择 PNG、JPG、WebP 或其他图片文件。");
      return;
    }
    state.file = file;
    if (state.sourceUrl) URL.revokeObjectURL(state.sourceUrl);
    state.sourceUrl = URL.createObjectURL(file);
    sourceThumb.src = state.sourceUrl;
    sourceName.textContent = file.name;
    sourceMeta.textContent = `${formatBytes(file.size)} · 准备描摹`;
    sourceRow.classList.remove("is-hidden");
    $("upload-title").textContent = "已选择图片";
    $("upload-subtitle").textContent = "可拖入另一张图片替换";
    convertButton.disabled = false;
    formNote.textContent = "准备转换。";
    setError("");
  }

  function clearFile() {
    state.file = null;
    fileInput.value = "";
    sourceRow.classList.add("is-hidden");
    $("upload-title").textContent = "将图片拖到这里";
    $("upload-subtitle").textContent = "或选择 PNG、JPG 或 WebP 文件";
    convertButton.disabled = true;
    formNote.textContent = "请选择图片开始。";
    if (state.sourceUrl) URL.revokeObjectURL(state.sourceUrl);
    state.sourceUrl = "";
  }

  function showLoading(show) {
    loadingOverlay.classList.toggle("is-hidden", !show);
    convertButton.disabled = show || !state.file;
    if (show) {
      $("loading-title").textContent = "正在描摹轮廓";
      $("loading-subtitle").textContent = "正在量化颜色并拟合形状……";
    }
  }

  function buildSettings() {
    return {
      preset: $("preset").value,
      max_width: $("max-width").value,
      colors: $("colors").value,
      background: $("background").value,
      max_output_mb: $("max-output-mb").value,
      fit: $("fit").value,
      title: $("title").value,
      no_gradients: !$("gradients").checked,
      no_underpainting: !$("underpainting").checked,
      score: $("score").checked,
    };
  }

  function updateResult(report) {
    $("stat-shapes").textContent = report.shapes ?? "-";
    $("stat-gradients").textContent = report.gradient_fills ?? "-";
    $("stat-bytes").textContent = formatBytes(report.bytes || 0);
    $("stat-similarity").textContent = report.similarity ? `${report.similarity.mae} MAE` : "未计算";
    resultStrip.classList.remove("is-hidden");
  }

  function showOutput(html, report, sourceNameValue) {
    state.html = html;
    state.report = report;
    if (state.outputUrl) URL.revokeObjectURL(state.outputUrl);
    state.outputUrl = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    artPreview.srcdoc = html;
    artPreview.style.display = "block";
    emptyPreview.style.display = "none";
    outputActions.classList.remove("is-hidden");
    $("download-button").href = state.outputUrl;
    $("download-button").download = `${sourceNameValue || "css-art"}.html`;
    updateResult(report);
  }

  chooseButton.addEventListener("click", (event) => { event.stopPropagation(); fileInput.click(); });
  dropZone.addEventListener("click", () => fileInput.click());
  dropZone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); fileInput.click(); }
  });
  fileInput.addEventListener("change", () => setFile(fileInput.files[0]));
  $("remove-source").addEventListener("click", clearFile);
  ["dragenter", "dragover"].forEach((eventName) => dropZone.addEventListener(eventName, (event) => {
    event.preventDefault(); dropZone.classList.add("is-dragging");
  }));
  ["dragleave", "drop"].forEach((eventName) => dropZone.addEventListener(eventName, (event) => {
    event.preventDefault(); dropZone.classList.remove("is-dragging");
  }));
  dropZone.addEventListener("drop", (event) => setFile(event.dataTransfer.files[0]));
  $("background").addEventListener("input", (event) => { $("background-value").textContent = event.target.value; });

  convertForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.file) return;
    setError("");
    showLoading(true);
    formNote.textContent = "正在本地处理……";
    const body = new FormData();
    body.append("file", state.file, state.file.name);
    body.append("settings", JSON.stringify(buildSettings()));
    try {
      const response = await fetch("/api/convert", { method: "POST", body });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "转换失败。");
      showOutput(payload.html, payload.report, payload.source_name);
      formNote.textContent = "转换完成。";
    } catch (error) {
      setError(localizeError(error.message) || "转换失败。");
      formNote.textContent = "转换失败。";
    } finally {
      showLoading(false);
    }
  });

  $("open-button").addEventListener("click", () => {
    if (!state.outputUrl) return;
    window.open(state.outputUrl, "_blank", "noopener");
  });
})();
