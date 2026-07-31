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
  Palette,
  ShieldCheck,
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
  structuredBriefSha256: string;
  topic: string;
  citation: string;
};

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
  const [elapsed, setElapsed] = useState(0);
  const [showStyleGuide, setShowStyleGuide] = useState(false);
  const mainRef = useRef<HTMLElement | null>(null);

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
    if (!isGenerating) return;
    setElapsed(0);
    const timer = window.setInterval(() => setElapsed((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [isGenerating]);

  const selectedStyle = useMemo(
    () => config.styles.find((item) => item.id === style),
    [config.styles, style]
  );

  const canGenerate = Boolean(phiConfirmed && (context.trim() || files.length > 0) && !isGenerating);
  const targetSeconds = 180;
  const progress = Math.min(100, Math.round((elapsed / targetSeconds) * 100));

  async function handleGenerate(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canGenerate) return;

    setError("");
    setResult(null);
    setIsGenerating(true);

    const form = new FormData();
    form.append("audience", audience);
    form.append("style", style);
    form.append("context", context);
    form.append("phiConfirmed", String(phiConfirmed));
    files.forEach((file) => form.append("files", file, file.name));

    try {
      const response = await fetch("/api/generate", { method: "POST", body: form });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload?.detail || "Generation failed.");
      }
      setResult(payload as GenerateResponse);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Generation failed.");
    } finally {
      setIsGenerating(false);
    }
  }

  function updateFiles(fileList: FileList | null) {
    setFiles(fileList ? Array.from(fileList) : []);
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
          <div className="hero-note" role="note">
            <ShieldCheck aria-hidden="true" />
            <span>Azure generation is configured server-side. API credentials are never shown.</span>
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

          <aside className="work-card options-card" aria-labelledby="options-title">
            <div className="section-heading compact">
              <Palette aria-hidden="true" />
              <div>
                <h2 id="options-title">Audience and style</h2>
                <p>Structured planning is applied automatically.</p>
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

            <label className="phi-box">
              <input
                type="checkbox"
                checked={phiConfirmed}
                onChange={(event) => setPhiConfirmed(event.target.checked)}
              />
              <span>I confirm this content does not contain protected health information (PHI).</span>
            </label>
          </aside>

          <section className="work-card generate-card" aria-labelledby="generate-title">
            <div className="section-heading">
              <span className="step-badge">Step 2</span>
              <div>
                <h2 id="generate-title">Generate</h2>
                <p>Most image generations take about three minutes.</p>
              </div>
            </div>
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

        {isGenerating ? (
          <section className="status-card" aria-live="polite" aria-label="Generation progress">
            <div>
              <h2>Generating your infographic</h2>
              <p>
                Cleaning source text, planning the visual structure, and rendering through Azure.
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
            <img
              className="generated-image"
              src={`data:image/png;base64,${result.imageBase64}`}
              alt="Generated UAB Medicine infographic draft"
            />
            <div className="metadata-row">
              <CheckCircle2 aria-hidden="true" />
              <span>Prompt hash: {result.promptSha256.slice(0, 12)}</span>
              {result.structuredBriefSha256 ? (
                <span>Structured brief: {result.structuredBriefSha256.slice(0, 12)}</span>
              ) : null}
            </div>
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
