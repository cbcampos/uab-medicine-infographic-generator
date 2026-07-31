import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AlertCircle,
  CheckCircle2,
  Download,
  FileText,
  ImageIcon,
  Info,
  Loader2,
  PlusCircle,
  RotateCcw,
  Trash2,
  Upload,
  X
} from "lucide-react";
import "./tokens.css";
import "./styles.css";

type Audience = {
  id: string;
  label: string;
};

type StyleOption = {
  id: string;
  name: string;
  description: string;
  sampleUrl: string;
};

type AppConfig = {
  audiences: Audience[];
  styles: StyleOption[];
  defaultAudience: string;
  defaultStyle: string;
  maxUploadMb: number;
};

type GenerateResponse = {
  imageBase64: string;
  filename: string;
  promptSha256: string;
  promptText: string;
  structuredBriefSha256: string;
  topic: string;
  citation: string;
};

type GenerateJobStartResponse = {
  jobId: string;
  status: string;
};

type GenerateJobStatusResponse = {
  jobId: string;
  status: "queued" | "running" | "succeeded" | "failed";
  result?: GenerateResponse | null;
  error?: string | null;
};

type EditPin = {
  id: number;
  xPercent: number;
  yPercent: number;
  comment: string;
};

type EditMode = "pin" | "paint";

const initialConfig: AppConfig = {
  audiences: [],
  styles: [],
  defaultAudience: "academic",
  defaultStyle: "uab-corporate",
  maxUploadMb: 10
};

function elapsedLabel(seconds: number): string {
  const mm = Math.floor(seconds / 60).toString().padStart(2, "0");
  const ss = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${mm}:${ss}`;
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function readJsonResponse<T>(response: Response): Promise<T> {
  const contentType = response.headers.get("content-type") || "";
  const body = await response.text();
  if (!contentType.includes("application/json")) {
    const summary = body.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
    throw new Error(summary || `Server returned ${response.status} ${response.statusText}.`);
  }
  const payload = JSON.parse(body) as T & { detail?: string };
  if (!response.ok) {
    throw new Error(payload.detail || `Server returned ${response.status} ${response.statusText}.`);
  }
  return payload as T;
}

function App() {
  const [config, setConfig] = useState<AppConfig>(initialConfig);
  const [audience, setAudience] = useState("academic");
  const [style, setStyle] = useState("uab-corporate");
  const [context, setContext] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [phiConfirmed, setPhiConfirmed] = useState(false);
  const [result, setResult] = useState<GenerateResponse | null>(null);
  const [error, setError] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [isRevising, setIsRevising] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [pins, setPins] = useState<EditPin[]>([]);
  const [nextPinId, setNextPinId] = useState(1);
  const [editMode, setEditMode] = useState<EditMode>("pin");
  const [brushSize, setBrushSize] = useState(56);
  const [maskDirty, setMaskDirty] = useState(false);
  const [showStyleGuide, setShowStyleGuide] = useState(false);
  const mainRef = useRef<HTMLElement | null>(null);
  const maskCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const drawingRef = useRef(false);

  useEffect(() => {
    fetch("/api/config")
      .then((response) => {
        if (!response.ok) throw new Error("Configuration could not be loaded.");
        return response.json() as Promise<AppConfig>;
      })
      .then((data) => {
        setConfig(data);
        setAudience(data.defaultAudience);
        setStyle(data.defaultStyle);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!isGenerating && !isRevising) return;
    setElapsed(0);
    const timer = window.setInterval(() => setElapsed((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [isGenerating, isRevising]);

  const selectedStyle = useMemo(
    () => config.styles.find((item) => item.id === style),
    [config.styles, style]
  );

  const isWorking = isGenerating || isRevising;
  const canGenerate = Boolean(phiConfirmed && (context.trim() || files.length > 0) && !isWorking);
  const canApplyEdits = Boolean(result && phiConfirmed && pins.some((pin) => pin.comment.trim()) && !isWorking);
  const targetSeconds = 180;
  const progress = Math.min(100, Math.round((elapsed / targetSeconds) * 100));

  async function pollJob(jobId: string): Promise<GenerateResponse> {
    const pollStartedAt = Date.now();
    while (Date.now() - pollStartedAt < 15 * 60 * 1000) {
      await wait(5000);
      const statusResponse = await fetch(`/api/generate-jobs/${jobId}`);
      const status = await readJsonResponse<GenerateJobStatusResponse>(statusResponse);

      if (status.status === "succeeded" && status.result) {
        return status.result;
      }
      if (status.status === "failed") {
        throw new Error(status.error || "Generation failed.");
      }
    }
    throw new Error("Generation is taking longer than expected. Please try again.");
  }

  async function handleGenerate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canGenerate) return;

    setError("");
    setResult(null);
    setPins([]);
    clearPaintMask();
    setIsGenerating(true);

    const form = new FormData();
    form.append("audience", audience);
    form.append("style", style);
    form.append("context", context);
    form.append("phiConfirmed", String(phiConfirmed));
    files.forEach((file) => form.append("files", file, file.name));

    try {
      const startResponse = await fetch("/api/generate-jobs", { method: "POST", body: form });
      const started = await readJsonResponse<GenerateJobStartResponse>(startResponse);
      setResult(await pollJob(started.jobId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Generation failed.");
    } finally {
      setIsGenerating(false);
    }
  }

  async function handleApplyEdits() {
    if (!result || !canApplyEdits) return;

    setError("");
    setIsRevising(true);

    const form = new FormData();
    form.append("audience", audience);
    form.append("style", style);
    form.append("context", context);
    form.append("phiConfirmed", String(phiConfirmed));
    form.append("imageBase64", result.imageBase64);
    form.append("currentTopic", result.topic);
    form.append("currentCitation", result.citation);
    const paintMaskBase64 = getPaintMaskBase64();
    if (paintMaskBase64) {
      form.append("paintMaskBase64", paintMaskBase64);
    }
    form.append(
      "pinsJson",
      JSON.stringify(
        pins
          .filter((pin) => pin.comment.trim())
          .map((pin) => ({
            id: pin.id,
            xPercent: pin.xPercent,
            yPercent: pin.yPercent,
            comment: pin.comment.trim()
          }))
      )
    );
    files.forEach((file) => form.append("files", file, file.name));

    try {
      const startResponse = await fetch("/api/revision-jobs", { method: "POST", body: form });
      const started = await readJsonResponse<GenerateJobStartResponse>(startResponse);
      setResult(await pollJob(started.jobId));
      setPins([]);
      clearPaintMask();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Revision failed.");
    } finally {
      setIsRevising(false);
    }
  }

  function updateFiles(fileList: FileList | null) {
    setFiles(fileList ? Array.from(fileList) : []);
  }

  function addPin(event: React.MouseEvent<HTMLDivElement>) {
    if (!result || isWorking || editMode !== "pin") return;
    const rect = event.currentTarget.getBoundingClientRect();
    const xPercent = Math.min(100, Math.max(0, ((event.clientX - rect.left) / rect.width) * 100));
    const yPercent = Math.min(100, Math.max(0, ((event.clientY - rect.top) / rect.height) * 100));
    const id = nextPinId;
    setPins((current) => [...current, { id, xPercent, yPercent, comment: "" }]);
    setNextPinId((value) => value + 1);
  }

  function addCenteredPin() {
    if (!result || isWorking) return;
    const id = nextPinId;
    setPins((current) => [...current, { id, xPercent: 50, yPercent: 50, comment: "" }]);
    setNextPinId((value) => value + 1);
  }

  function updatePinComment(id: number, comment: string) {
    setPins((current) => current.map((pin) => (pin.id === id ? { ...pin, comment } : pin)));
  }

  function removePin(id: number) {
    setPins((current) => current.filter((pin) => pin.id !== id));
  }

  function addQuickEdit(text: string) {
    if (!pins.length) return;
    const lastPin = pins[pins.length - 1];
    updatePinComment(lastPin.id, text);
  }

  function syncPaintCanvas(event: React.SyntheticEvent<HTMLImageElement>) {
    const image = event.currentTarget;
    const canvas = maskCanvasRef.current;
    if (!canvas || !image.naturalWidth || !image.naturalHeight) return;
    if (canvas.width === image.naturalWidth && canvas.height === image.naturalHeight) return;
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    setMaskDirty(false);
  }

  function canvasPoint(event: React.PointerEvent<HTMLCanvasElement>) {
    const canvas = event.currentTarget;
    const rect = canvas.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / rect.width) * canvas.width,
      y: ((event.clientY - rect.top) / rect.height) * canvas.height
    };
  }

  function paintAt(event: React.PointerEvent<HTMLCanvasElement>) {
    const canvas = event.currentTarget;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const rect = canvas.getBoundingClientRect();
    const scale = Math.max(canvas.width / Math.max(rect.width, 1), canvas.height / Math.max(rect.height, 1));
    const point = canvasPoint(event);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = "rgba(0, 101, 168, 0.58)";
    ctx.fillStyle = "rgba(0, 101, 168, 0.58)";
    ctx.lineWidth = brushSize * scale;
    ctx.lineTo(point.x, point.y);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(point.x, point.y, (brushSize * scale) / 2, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.moveTo(point.x, point.y);
    setMaskDirty(true);
  }

  function startPainting(event: React.PointerEvent<HTMLCanvasElement>) {
    if (editMode !== "paint" || isWorking) return;
    event.preventDefault();
    event.stopPropagation();
    drawingRef.current = true;
    event.currentTarget.setPointerCapture(event.pointerId);
    const ctx = event.currentTarget.getContext("2d");
    const point = canvasPoint(event);
    ctx?.beginPath();
    ctx?.moveTo(point.x, point.y);
    paintAt(event);
  }

  function continuePainting(event: React.PointerEvent<HTMLCanvasElement>) {
    if (!drawingRef.current || editMode !== "paint" || isWorking) return;
    event.preventDefault();
    event.stopPropagation();
    paintAt(event);
  }

  function stopPainting(event: React.PointerEvent<HTMLCanvasElement>) {
    if (!drawingRef.current) return;
    event.preventDefault();
    event.stopPropagation();
    drawingRef.current = false;
    event.currentTarget.releasePointerCapture(event.pointerId);
  }

  function clearPaintMask() {
    const canvas = maskCanvasRef.current;
    if (canvas) {
      const ctx = canvas.getContext("2d");
      ctx?.clearRect(0, 0, canvas.width, canvas.height);
    }
    setMaskDirty(false);
  }

  function getPaintMaskBase64(): string {
    const canvas = maskCanvasRef.current;
    if (!canvas || !maskDirty || !canvas.width || !canvas.height) return "";
    return canvas.toDataURL("image/png").split(",", 2)[1] || "";
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <SiteHeader />
      <main id="main-content" className="main-content" ref={mainRef}>
        <section className="hero-panel" aria-labelledby="page-title">
          <div>
            <p className="eyebrow">UAB Medicine infographic studio</p>
            <h1 id="page-title">Create a branded scientific infographic draft</h1>
            <p>
              Upload a source document, choose an audience and visual style, then generate a
              UAB Medicine concept graphic for human review.
            </p>
          </div>
        </section>

        {error ? (
          <div className="alert alert-error" role="alert">
            <AlertCircle aria-hidden="true" />
            <span>{error}</span>
          </div>
        ) : null}

        <form className="workflow-grid" onSubmit={handleGenerate}>
          <section className="work-card source-card" aria-labelledby="setup-title">
            <div className="section-heading">
              <span className="step-badge">Step 1</span>
              <div>
                <h2 id="setup-title">Set up your infographic</h2>
                <p>Add source material and choose how the graphic should be framed.</p>
              </div>
            </div>

            <label className="field-label" htmlFor="context">
              Optional context
            </label>
            <textarea
              id="context"
              value={context}
              onChange={(event) => setContext(event.target.value)}
              placeholder="Add a short goal, preferred framing, or key point to emphasize."
              rows={6}
            />

            <div className="upload-box">
              <Upload aria-hidden="true" />
              <div>
                <label className="upload-label" htmlFor="files">
                  Upload documents
                </label>
                <p>PDF, DOCX, or TXT. Up to {config.maxUploadMb} MB per file.</p>
                <input
                  id="files"
                  type="file"
                  multiple
                  accept=".pdf,.docx,.txt"
                  onChange={(event) => updateFiles(event.target.files)}
                />
              </div>
            </div>

            {files.length ? (
              <ul className="file-list" aria-label="Selected files">
                {files.map((file) => (
                  <li key={`${file.name}-${file.size}`}>
                    <FileText aria-hidden="true" />
                    <span>{file.name}</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </section>

          <section className="work-card options-card" aria-labelledby="options-title">
            <div className="section-heading">
              <span className="step-badge">Step 2</span>
              <div>
                <h2 id="options-title">Choose audience and style</h2>
                <p>Select who the infographic is for and how it should look.</p>
              </div>
            </div>

            <fieldset>
              <legend>Choose audience</legend>
              <div className="choice-list">
                {config.audiences.map((item) => (
                  <label className="choice-row" key={item.id}>
                    <input
                      type="radio"
                      name="audience"
                      value={item.id}
                      checked={audience === item.id}
                      onChange={() => setAudience(item.id)}
                    />
                    <span>{item.label}</span>
                  </label>
                ))}
              </div>
            </fieldset>

            <label className="field-label" htmlFor="style">
              Choose style
            </label>
            <select id="style" value={style} onChange={(event) => setStyle(event.target.value)}>
              {config.styles.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            {selectedStyle ? <p className="muted">{selectedStyle.description}</p> : null}

            <button type="button" className="secondary-button" onClick={() => setShowStyleGuide(true)}>
              <ImageIcon aria-hidden="true" />
              Open style guide
            </button>
          </section>

          <section className="work-card generate-card" aria-labelledby="generate-title">
            <div className="section-heading">
              <span className="step-badge">Step 3</span>
              <div>
                <h2 id="generate-title">Generate</h2>
                <p>Most image generations take about three minutes.</p>
              </div>
            </div>
            <label className="phi-box">
              <input
                type="checkbox"
                checked={phiConfirmed}
                onChange={(event) => setPhiConfirmed(event.target.checked)}
              />
              <span>I confirm this content does not contain protected health information (PHI).</span>
            </label>
            <button className="primary-button" type="submit" disabled={!canGenerate}>
              {isGenerating ? (
                <>
                  <Loader2 className="spin" aria-hidden="true" />
                  Generating
                </>
              ) : (
                "Generate infographic"
              )}
            </button>
            {!context.trim() && !files.length ? (
              <p className="helper-text">Add optional context or upload at least one document.</p>
            ) : null}
            {!phiConfirmed ? <p className="helper-text">PHI confirmation is required.</p> : null}
          </section>
        </form>

        {isWorking ? (
          <section className="status-card" aria-live="polite" aria-label="Generation progress">
            <div>
              <h2>{isRevising ? "Applying your edits" : "Generating your infographic"}</h2>
              <p>
                {isRevising
                  ? "Reading pinned comments, planning the revision, and rendering the updated draft."
                  : "Cleaning source text, planning the visual structure, and rendering the draft."}
                Elapsed time: {elapsedLabel(elapsed)} / 03:00 target.
              </p>
            </div>
            <div className="progress-track" aria-hidden="true">
              <div className="progress-bar" style={{ width: `${progress}%` }} />
            </div>
          </section>
        ) : null}

        {result ? (
          <section className="result-card" aria-labelledby="result-title">
            <div className="result-header">
              <div>
                <p className="eyebrow">Generated draft</p>
                <h2 id="result-title">{result.topic}</h2>
                {result.citation ? <p>{result.citation}</p> : null}
              </div>
              <a
                className="primary-button download-link"
                href={`data:image/png;base64,${result.imageBase64}`}
                download={result.filename}
              >
                <Download aria-hidden="true" />
                Download PNG
              </a>
            </div>
            <div className="edit-workspace">
              <div className="edit-toolbar">
                <div>
                  <h3>Review and edit</h3>
                  <p>Pin comments describe what to change. Painting optionally marks the exact edit area.</p>
                </div>
                <div className="edit-actions">
                  <button type="button" className="secondary-button" onClick={() => setPins([])} disabled={!pins.length || isWorking}>
                    <RotateCcw aria-hidden="true" />
                    Clear pins
                  </button>
                  <button type="button" className="primary-button" onClick={handleApplyEdits} disabled={!canApplyEdits}>
                    {isRevising ? (
                      <>
                        <Loader2 className="spin" aria-hidden="true" />
                        Applying edits
                      </>
                    ) : (
                      <>
                        <PlusCircle aria-hidden="true" />
                        Apply pinned edits
                      </>
                    )}
                  </button>
                </div>
              </div>

              <div className="quick-edits" aria-label="Quick edit suggestions">
                {[
                  "Reduce text in this area",
                  "Make this section larger",
                  "Improve spacing here",
                  "Move this away from the logo",
                  "Simplify this chart",
                  "Remove this element"
                ].map((text) => (
                  <button type="button" key={text} onClick={() => addQuickEdit(text)} disabled={!pins.length || isWorking}>
                    {text}
                  </button>
                ))}
              </div>

              <div className="edit-mode-panel" aria-label="Edit mode controls">
                <div className="segmented-control">
                  <button
                    type="button"
                    className={editMode === "pin" ? "active" : ""}
                    onClick={() => setEditMode("pin")}
                    disabled={isWorking}
                  >
                    Pin comments
                  </button>
                  <button
                    type="button"
                    className={editMode === "paint" ? "active" : ""}
                    onClick={() => setEditMode("paint")}
                    disabled={isWorking}
                  >
                    Paint edit area
                  </button>
                </div>
                {editMode === "paint" ? (
                  <div className="paint-controls">
                    <label>
                      Brush
                      <input
                        type="range"
                        min="18"
                        max="140"
                        value={brushSize}
                        onChange={(event) => setBrushSize(Number(event.target.value))}
                        disabled={isWorking}
                      />
                    </label>
                    <button type="button" className="secondary-button" onClick={clearPaintMask} disabled={!maskDirty || isWorking}>
                      Clear painted area
                    </button>
                    <span>{maskDirty ? "Painted area will scope the edit." : "Paint over the part to revise."}</span>
                  </div>
                ) : (
                  <p>Click the infographic to add a numbered pin, then write the edit below.</p>
                )}
              </div>

              <div
                className={`image-editor ${editMode === "paint" ? "painting" : ""}`}
                onClick={addPin}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    addCenteredPin();
                  }
                }}
                role="button"
                tabIndex={0}
                aria-label="Click image to add an edit pin"
              >
                <img
                  className="generated-image"
                  src={`data:image/png;base64,${result.imageBase64}`}
                  alt="Generated UAB Medicine infographic draft"
                  onLoad={syncPaintCanvas}
                />
                <canvas
                  ref={maskCanvasRef}
                  className="paint-mask-canvas"
                  aria-hidden="true"
                  onPointerDown={startPainting}
                  onPointerMove={continuePainting}
                  onPointerUp={stopPainting}
                  onPointerCancel={stopPainting}
                  onClick={(event) => event.stopPropagation()}
                />
                {pins.map((pin, index) => (
                  <button
                    type="button"
                    key={pin.id}
                    className="edit-pin"
                    style={{ left: `${pin.xPercent}%`, top: `${pin.yPercent}%` }}
                    onClick={(event) => event.stopPropagation()}
                    aria-label={`Edit pin ${index + 1}`}
                  >
                    {index + 1}
                  </button>
                ))}
              </div>

              {pins.length ? (
                <div className="pin-list">
                  {pins.map((pin, index) => (
                    <label className="pin-row" key={pin.id}>
                      <span>{index + 1}</span>
                      <input
                        value={pin.comment}
                        onChange={(event) => updatePinComment(pin.id, event.target.value)}
                        placeholder="Describe the edit for this pinned location."
                        disabled={isWorking}
                      />
                      <button type="button" className="icon-button" onClick={() => removePin(pin.id)} disabled={isWorking} aria-label={`Remove pin ${index + 1}`}>
                        <Trash2 aria-hidden="true" />
                      </button>
                    </label>
                  ))}
                </div>
              ) : null}
            </div>
            <div className="metadata-row">
              <CheckCircle2 aria-hidden="true" />
              <span>Prompt hash: {result.promptSha256.slice(0, 12)}</span>
              {result.structuredBriefSha256 ? (
                <span>Structured brief: {result.structuredBriefSha256.slice(0, 12)}</span>
              ) : null}
            </div>
            {result.promptText ? (
              <details className="expert-info">
                <summary>Expert info: exact image prompt used</summary>
                <p>
                  This is the final prompt sent for the displayed graphic after planning and prompt
                  optimization.
                </p>
                <textarea readOnly value={result.promptText} rows={14} aria-label="Exact image prompt used" />
              </details>
            ) : null}
          </section>
        ) : null}
      </main>
      <SiteFooter />
      {showStyleGuide ? (
        <StyleGuideDialog styles={config.styles} onClose={() => setShowStyleGuide(false)} onUseStyle={setStyle} />
      ) : null}
    </div>
  );
}

function SiteHeader() {
  return (
    <header className="site-header">
      <div className="top-rule" />
      <div className="header-inner">
        <img src="/uab-medicine-logo-white.png" alt="UAB Medicine" className="brand-logo" />
        <span className="brand-divider" aria-hidden="true" />
        <div className="product-lockup">
          <span>Infographic Generator</span>
          <small>Division of General Internal Medicine &amp; Population Science</small>
        </div>
      </div>
    </header>
  );
}

function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="footer-brand">
        <div>
          <img src="/uab-medicine-logo-white.png" alt="UAB Medicine" className="footer-logo" />
          <p>Division of General Internal Medicine &amp; Population Science</p>
        </div>
        <p className="system-text">The University of Alabama System</p>
      </div>
      <div className="footer-legal">
        <a href="https://www.uab.edu/policies/content/Pages/UAB-AA-POL-0000056.aspx">Nondiscrimination</a>
        <a href="https://www.uab.edu/">UAB</a>
        <a href="https://www.uabmedicine.org/">UAB Medicine</a>
        <span>© {new Date().getFullYear()} The University of Alabama at Birmingham</span>
      </div>
    </footer>
  );
}

function StyleGuideDialog({
  styles,
  onClose,
  onUseStyle
}: {
  styles: StyleOption[];
  onClose: () => void;
  onUseStyle: (id: string) => void;
}) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="style-guide-title">
        <div className="modal-header">
          <div>
            <h2 id="style-guide-title">Style guide</h2>
            <p>Only production-approved UAB styles are shown.</p>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Close style guide">
            <X aria-hidden="true" />
          </button>
        </div>
        <div className="style-grid">
          {styles.map((style) => (
            <article className="style-card" key={style.id}>
              <img src={style.sampleUrl} alt={`${style.name} example`} />
              <div>
                <h3>{style.name}</h3>
                <p>{style.description}</p>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => {
                    onUseStyle(style.id);
                    onClose();
                  }}
                >
                  Use this style
                </button>
              </div>
            </article>
          ))}
        </div>
        <p className="modal-note">
          <Info aria-hidden="true" />
          Generated drafts still require human review before publication.
        </p>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
