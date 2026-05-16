import { useState, useEffect, useRef } from "react";
import { Streamdown } from "streamdown";

// API base URL:
// - VITE_BACKEND_URL env var if set at build time (override)
// - Hardcoded Railway URL if the current origin is NOT Railway (i.e., Manus/Vercel deploy)
// - Empty string (relative) if already on Railway (same-origin)
const RAILWAY_URL = "https://zirowork-brain-agent-production.up.railway.app";
const API_BASE: string = (() => {
  const envUrl = import.meta.env.VITE_BACKEND_URL as string | undefined;
  if (envUrl) return envUrl.replace(/\/$/, "");
  // If running on the Railway domain itself, use relative paths (same-origin)
  if (typeof window !== "undefined" && window.location.hostname.endsWith("railway.app")) return "";
  // Otherwise (Manus, Vercel, local dev with no proxy) — call Railway directly
  return RAILWAY_URL;
})();

type StepStatus = "idle" | "running" | "done" | "error" | "skipped";
type Mode = "private" | "public";

interface PipelineStep {
  id: number;
  label: string;
  description: string;
  status: StepStatus;
}

interface ProcessResult {
  status: "success" | "error";
  filename?: string;
  drive_url?: string | null;
  preview?: string;
  message?: string;
  error?: string;
  code?: string;
  creator?: string;
  category?: string;
  mode?: Mode;
  email_sent?: boolean;
  importance?: "high" | "medium" | "low" | string;
}

function buildInitialSteps(mode: Mode): PipelineStep[] {
  return [
    { id: 1, label: "Extract Creator",      description: "Get uploader from Instagram metadata",          status: "idle" },
    { id: 2, label: "Extract Audio",        description: "Apify pulls video media from Instagram",         status: "idle" },
    { id: 3, label: "Transcribe",           description: "OpenAI Whisper converts speech to text",         status: "idle" },
    { id: 4, label: "Auto-Categorize",      description: "Backend scores topic from transcript",           status: "idle" },
    { id: 5, label: "Process with Claude",  description: "Claude cleans, structures, and expands",        status: "idle" },
    { id: 6, label: "Format Markdown",      description: "YAML front matter + structured sections",       status: "idle" },
    mode === "public"
      ? { id: 7, label: "Email Output",     description: "Send markdown to submitter and route review copy", status: "idle" }
      : { id: 7, label: "Save to Drive",    description: "Write to ZiroWork-Brain/Raw Videos/",           status: "idle" },
    { id: 8, label: "Cleanup",              description: "Delete temp audio files",                       status: "idle" },
  ];
}

function StepIcon({ status }: { status: StepStatus }) {
  if (status === "done")    return <span style={{ color: "var(--lime)", fontSize: 14, fontWeight: 700 }}>✓</span>;
  if (status === "error")   return <span style={{ color: "var(--red)", fontSize: 14, fontWeight: 700 }}>✗</span>;
  if (status === "running") return <PulseRing />;
  if (status === "skipped") return <span style={{ color: "var(--muted2-color)", fontSize: 14 }}>—</span>;
  return <span style={{ color: "var(--border2-color)", fontSize: 14 }}>○</span>;
}

function PulseRing() {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 14, height: 14 }}>
      <span className="zw-pulse" style={{ width: 7, height: 7 }} />
    </span>
  );
}

function ModeButton({ active, title, subtitle, onClick, disabled }: {
  active: boolean;
  title: string;
  subtitle: string;
  onClick: () => void;
  disabled: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      style={{
        flex: 1,
        textAlign: "left",
        padding: "14px 16px",
        borderRadius: 10,
        border: active ? "1px solid rgba(197,241,53,0.75)" : "1px solid var(--border-color)",
        background: active ? "rgba(197,241,53,0.08)" : "rgba(255,255,255,0.02)",
        color: active ? "#fff" : "var(--muted-color)",
        cursor: disabled ? "not-allowed" : "pointer",
      }}
    >
      <div style={{
        fontFamily: "'Bebas Neue', sans-serif",
        fontSize: 18,
        letterSpacing: 1.4,
        color: active ? "var(--lime)" : "#fff",
        marginBottom: 4,
      }}>
        {title}
      </div>
      <div style={{ fontSize: 12, lineHeight: 1.45, color: "var(--muted2-color)" }}>{subtitle}</div>
    </button>
  );
}

export default function Home() {
  const [link, setLink]         = useState("");
  const [mode, setMode]         = useState<Mode>("private");
  const [email, setEmail]       = useState("");
  const [name, setName]         = useState("");
  const [steps, setSteps]       = useState<PipelineStep[]>(buildInitialSteps("private"));
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult]     = useState<ProcessResult | null>(null);
  const [backendStatus, setBackendStatus] = useState<"unknown" | "online" | "offline">("unknown");
  const resultRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/health`)
      .then((r) => r.json())
      .then(() => setBackendStatus("online"))
      .catch(() => setBackendStatus("offline"));
  }, []);

  useEffect(() => {
    if (result && resultRef.current) {
      resultRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [result]);

  function resetSteps(nextMode: Mode = mode) {
    setSteps(buildInitialSteps(nextMode));
  }

  function setStepStatus(id: number, status: StepStatus) {
    setSteps((prev) => prev.map((s) => s.id === id ? { ...s, status } : s));
  }

  function markAllAfterError(fromId: number) {
    setSteps((prev) => prev.map((s) => s.id > fromId ? { ...s, status: "skipped" } : s));
  }

  async function simulateProgress(abortSignal: AbortSignal): Promise<void> {
    const delays = [800, 1200, 8000, 2000, 12000, 800, 1500, 400];
    for (let i = 0; i < buildInitialSteps(mode).length; i++) {
      if (abortSignal.aborted) return;
      setStepStatus(i + 1, "running");
      await new Promise((res) => setTimeout(res, delays[i]));
      if (abortSignal.aborted) return;
    }
  }

  function handleModeChange(nextMode: Mode) {
    setMode(nextMode);
    setResult(null);
    resetSteps(nextMode);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!link.trim()) return;
    if (mode === "public" && !email.trim()) return;

    setIsRunning(true);
    setResult(null);
    resetSteps(mode);

    const abortController = new AbortController();
    const progressPromise = simulateProgress(abortController.signal);

    const payload: Record<string, string> = {
      instagram_link: link.trim(),
      mode,
    };
    if (mode === "public") {
      payload.email = email.trim();
      if (name.trim()) payload.name = name.trim();
    }

    try {
      const response = await fetch(`${API_BASE}/api/process-video`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(360_000),
      });

      abortController.abort();

      const data: ProcessResult = await response.json();

      if (data.status === "success") {
        setSteps(buildInitialSteps(mode).map((s) => ({ ...s, status: "done" })));
      } else {
        setStepStatus(1, "error");
        markAllAfterError(1);
      }

      setResult(data);
    } catch (err: unknown) {
      abortController.abort();
      const errorMsg = err instanceof Error ? err.message : "Network error";
      setStepStatus(1, "error");
      markAllAfterError(1);
      setResult({
        status: "error",
        error: errorMsg.includes("timeout")
          ? "Request timed out. Large videos can take up to 5 minutes — try again."
          : `Network error: ${errorMsg}. Is the backend running?`,
        code: "TIMEOUT",
      });
    } finally {
      await progressPromise.catch(() => {});
      setIsRunning(false);
    }
  }

  const activeSteps = buildInitialSteps(mode);
  const completedSteps = steps.filter((s) => s.status === "done").length;
  const progress = isRunning
    ? (steps.findIndex((s) => s.status === "running") + 1) / activeSteps.length * 100
    : result?.status === "success" ? 100 : 0;

  const canSubmit = Boolean(link.trim()) && !isRunning && (mode === "private" || Boolean(email.trim()));
  const resultMode: Mode = result?.mode === "public" ? "public" : "private";

  return (
    <div style={{ minHeight: "100vh", background: "var(--black)", color: "var(--text-color)" }}>

      {/* ── Nav ── */}
      <nav className="zw-nav">
        <span className="zw-logo">ZIROWORK</span>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div className={`zw-status ${backendStatus === "online" ? "running" : backendStatus === "offline" ? "error" : "idle"}`}>
            <span className={`zw-pulse`} style={{
              background: backendStatus === "online" ? "var(--lime)" : backendStatus === "offline" ? "var(--red)" : "var(--muted-color)"
            }} />
            {backendStatus === "online" ? "BACKEND ONLINE" : backendStatus === "offline" ? "BACKEND OFFLINE" : "CHECKING..."}
          </div>
        </div>
      </nav>

      {/* ── Backend offline warning ── */}
      {backendStatus === "offline" && (
        <div className="zw-callout-purple" style={{ margin: 0, borderLeft: "none", borderBottom: "1px solid rgba(139,92,246,0.3)" }}>
          <div className="zw-wrap" style={{ padding: "10px 20px" }}>
            <span style={{ fontSize: 12, fontWeight: 600, letterSpacing: 1 }}>
              Backend not detected — start it with: <code style={{ color: "var(--purple-glow)", background: "rgba(139,92,246,0.1)", padding: "2px 6px" }}>cd backend && python main.py</code>
            </span>
          </div>
        </div>
      )}

      {/* ── Main content ── */}
      <main className="zw-wrap" style={{ paddingTop: 48, paddingBottom: 80 }}>
        <div className="zw-fade-up">

          {/* ── Hero ── */}
          <div style={{ marginBottom: 40 }}>
            <div className="zw-badge" style={{ marginBottom: 18 }}>
              <span className="zw-pulse" />
              INTELLIGENCE PROCESSOR
            </div>
            <h1 style={{
              fontFamily: "'Bebas Neue', sans-serif",
              fontSize: "clamp(52px, 13vw, 72px)",
              lineHeight: 0.92,
              letterSpacing: "0.5px",
              color: "#fff",
              margin: "0 0 16px",
            }}>
              BRAIN<br />
              <span style={{ color: "var(--lime)" }}>AGENT</span>
            </h1>
            <p style={{
              fontSize: 16,
              color: "var(--muted-color)",
              lineHeight: 1.6,
              margin: 0,
              maxWidth: 520,
            }}>
              Paste an Instagram link. Private mode saves clean markdown to Zach's Drive. Share mode emails the markdown to the submitter and silently routes a review copy for ZiroWork research.
            </p>
          </div>

          {/* ── Pipeline overview ── */}
          <div className="zw-callout" style={{ marginBottom: 32, fontSize: 13 }}>
            <span style={{ color: "var(--lime)", fontWeight: 700 }}>PIPELINE:</span>{" "}
            Link → Creator → Audio → Whisper → Categorize → Claude → {mode === "public" ? "Email + Review Copy" : "Google Drive"}
          </div>

          {/* ── Form card ── */}
          <div className="zw-card" style={{ marginBottom: 32 }}>
            <div style={{ padding: "20px 24px", borderBottom: "1px solid var(--border-color)" }}>
              <span style={{
                fontFamily: "'Bebas Neue', sans-serif",
                fontSize: 18,
                letterSpacing: 2,
                color: "#fff",
              }}>PROCESS VIDEO</span>
            </div>

            <form onSubmit={handleSubmit} style={{ padding: "24px" }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>

                {/* Mode toggle */}
                <div>
                  <label className="zw-label">Mode</label>
                  <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                    <ModeButton
                      active={mode === "private"}
                      title="Private"
                      subtitle="Zach workflow: save straight to Google Drive."
                      onClick={() => handleModeChange("private")}
                      disabled={isRunning}
                    />
                    <ModeButton
                      active={mode === "public"}
                      title="Share"
                      subtitle="Public workflow: email the markdown and store a hidden review copy."
                      onClick={() => handleModeChange("public")}
                      disabled={isRunning}
                    />
                  </div>
                </div>

                {/* Instagram link */}
                <div>
                  <label className="zw-label">Instagram Link</label>
                  <input
                    className="zw-input"
                    type="url"
                    value={link}
                    onChange={(e) => setLink(e.target.value)}
                    placeholder="https://www.instagram.com/reel/..."
                    disabled={isRunning}
                    required
                  />
                </div>

                {/* Public fields */}
                {mode === "public" && (
                  <>
                    <div>
                      <label className="zw-label">Email Address</label>
                      <input
                        className="zw-input"
                        type="email"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        placeholder="you@example.com"
                        disabled={isRunning}
                        required
                      />
                    </div>
                    <div>
                      <label className="zw-label">Name <span style={{ color: "var(--muted2-color)", fontWeight: 400 }}>(optional)</span></label>
                      <input
                        className="zw-input"
                        type="text"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        placeholder="Your name"
                        disabled={isRunning}
                      />
                    </div>
                    <div className="zw-callout" style={{ fontSize: 12, lineHeight: 1.5, marginTop: -4 }}>
                      By submitting, you agree ZiroWork may store a private review copy to improve research workflows.
                    </div>
                  </>
                )}

                {/* Submit */}
                <button
                  type="submit"
                  className="zw-btn-primary"
                  disabled={!canSubmit}
                  style={{ marginTop: 4 }}
                >
                  {isRunning ? (
                    <>
                      <span className="zw-pulse" />
                      PROCESSING...
                    </>
                  ) : mode === "public" ? (
                    "EMAIL MARKDOWN →"
                  ) : (
                    "SAVE TO DRIVE →"
                  )}
                </button>

              </div>
            </form>
          </div>

          {/* ── Pipeline steps ── */}
          {(isRunning || result) && (
            <div className="zw-card" style={{ marginBottom: 32 }}>
              <div style={{
                padding: "16px 24px",
                borderBottom: "1px solid var(--border-color)",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}>
                <span style={{
                  fontFamily: "'Bebas Neue', sans-serif",
                  fontSize: 18,
                  letterSpacing: 2,
                  color: "#fff",
                }}>PIPELINE STATUS</span>
                <span style={{ fontSize: 12, color: "var(--muted-color)", fontWeight: 600 }}>
                  {completedSteps}/{activeSteps.length} STEPS
                </span>
              </div>

              {/* Progress bar */}
              <div className="zw-progress-track">
                <div
                  className={`zw-progress-fill ${isRunning ? "processing" : ""}`}
                  style={{ width: `${progress}%` }}
                />
              </div>

              <div style={{ padding: "8px 24px 16px" }}>
                {steps.map((step, idx) => (
                  <div
                    key={step.id}
                    className="zw-step"
                    style={{
                      animationDelay: `${idx * 40}ms`,
                      opacity: step.status === "skipped" ? 0.4 : 1,
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 28 }}>
                      <span className={`zw-step-num ${step.status}`}>{step.id}</span>
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 2 }}>
                        <span style={{
                          fontSize: 13,
                          fontWeight: 700,
                          color: step.status === "done" ? "var(--lime)"
                            : step.status === "running" ? "#fff"
                            : step.status === "error" ? "var(--red)"
                            : "var(--muted-color)",
                          letterSpacing: 0.5,
                        }}>
                          {step.label}
                        </span>
                        <StepIcon status={step.status} />
                      </div>
                      <p style={{ fontSize: 12, color: "var(--muted2-color)", margin: 0, lineHeight: 1.4 }}>
                        {step.description}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Result ── */}
          {result && (
            <div ref={resultRef}>
              {result.status === "success" ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>

                  {/* Success header */}
                  <div className="zw-success">
                    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
                      <span style={{
                        fontFamily: "'Bebas Neue', sans-serif",
                        fontSize: 20,
                        letterSpacing: 2,
                        color: "var(--lime)",
                      }}>{resultMode === "public" ? "SENT TO EMAIL" : "SAVED TO DRIVE"}</span>
                      <span style={{ color: "var(--lime)", fontSize: 16 }}>✓</span>
                      {resultMode === "public" && (
                        <span className="zw-badge-purple" style={{ display: "inline-flex" }}>SHARE MODE</span>
                      )}
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                        <span className="zw-label" style={{ margin: 0, minWidth: 90 }}>FILENAME</span>
                        <span style={{ fontSize: 14, color: "var(--text-color)", fontFamily: "monospace" }}>
                          {result.filename}
                        </span>
                      </div>
                      {result.creator && (
                        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                          <span className="zw-label" style={{ margin: 0, minWidth: 90 }}>CREATOR</span>
                          <span style={{ fontSize: 14, color: "var(--text-color)" }}>{result.creator}</span>
                        </div>
                      )}
                      {result.category && resultMode === "private" && (
                        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                          <span className="zw-label" style={{ margin: 0, minWidth: 90 }}>CATEGORY</span>
                          <span style={{ fontSize: 14, color: "var(--lime)" }}>{result.category}</span>
                        </div>
                      )}
                      {result.drive_url && resultMode === "private" && (
                        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                          <span className="zw-label" style={{ margin: 0, minWidth: 90 }}>DRIVE</span>
                          <a
                            href={result.drive_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{
                              fontSize: 14,
                              color: "var(--lime)",
                              textDecoration: "none",
                              borderBottom: "1px solid rgba(197,241,53,0.3)",
                            }}
                          >
                            Open in Google Drive →
                          </a>
                        </div>
                      )}
                      {resultMode === "public" && email && (
                        <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                          <span className="zw-label" style={{ margin: 0, minWidth: 90 }}>EMAIL</span>
                          <span style={{ fontSize: 13, color: "var(--text-color)" }}>{email}</span>
                        </div>
                      )}
                      {result.message && (
                        <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                          <span className="zw-label" style={{ margin: 0, minWidth: 90 }}>STATUS</span>
                          <span style={{ fontSize: 13, color: "var(--muted-color)" }}>{result.message}</span>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Markdown preview */}
                  {result.preview && (
                    <div>
                      <div style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        marginBottom: 10,
                      }}>
                        <span className="zw-label" style={{ margin: 0 }}>MARKDOWN PREVIEW</span>
                        <span style={{ fontSize: 11, color: "var(--muted2-color)" }}>READ-ONLY</span>
                      </div>
                      <div className="zw-markdown-preview">
                        <Streamdown>{result.preview}</Streamdown>
                      </div>
                    </div>
                  )}

                  {/* Process another */}
                  <button
                    className="zw-btn-secondary"
                    onClick={() => {
                      setResult(null);
                      resetSteps(mode);
                      setLink("");
                      if (mode === "public") {
                        setEmail("");
                        setName("");
                      }
                    }}
                  >
                    PROCESS ANOTHER →
                  </button>

                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                  <div className="zw-error">
                    <div style={{ fontWeight: 700, marginBottom: 6, fontSize: 13, letterSpacing: 1, textTransform: "uppercase" }}>
                      Error {result.code ? `[${result.code}]` : ""}
                    </div>
                    <div style={{ fontSize: 14, lineHeight: 1.5 }}>{result.error}</div>
                  </div>
                  <button
                    className="zw-btn-secondary"
                    onClick={() => {
                      setResult(null);
                      resetSteps(mode);
                    }}
                  >
                    TRY AGAIN →
                  </button>
                </div>
              )}
            </div>
          )}

          {/* ── Info cards ── */}
          {!isRunning && !result && (
            <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 8 }}>
              <div className="zw-card2" style={{ padding: "16px 20px" }}>
                <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
                  <div style={{ flex: 1, minWidth: 160 }}>
                    <div className="zw-badge-purple" style={{ marginBottom: 10, display: "inline-flex" }}>COST</div>
                    <div style={{ fontSize: 13, color: "var(--muted-color)", lineHeight: 1.6 }}>
                      ~$0.15–0.25 per video<br />
                      <span style={{ color: "var(--muted2-color)", fontSize: 12 }}>Whisper + Claude Haiku</span>
                    </div>
                  </div>
                  <div style={{ flex: 1, minWidth: 160 }}>
                    <div className="zw-badge-purple" style={{ marginBottom: 10, display: "inline-flex" }}>SPEED</div>
                    <div style={{ fontSize: 13, color: "var(--muted-color)", lineHeight: 1.6 }}>
                      &lt;6 min for 30-min video<br />
                      <span style={{ color: "var(--muted2-color)", fontSize: 12 }}>Audio extract → delivery</span>
                    </div>
                  </div>
                  <div style={{ flex: 1, minWidth: 160 }}>
                    <div className="zw-badge-purple" style={{ marginBottom: 10, display: "inline-flex" }}>OUTPUT</div>
                    <div style={{ fontSize: 13, color: "var(--muted-color)", lineHeight: 1.6 }}>
                      Clean .md file<br />
                      <span style={{ color: "var(--muted2-color)", fontSize: 12 }}>Drive save or email delivery</span>
                    </div>
                  </div>
                </div>
              </div>

              <div className="zw-callout" style={{ fontSize: 13 }}>
                <strong style={{ color: "var(--lime)" }}>No creator or category required.</strong>{" "}
                Creator auto-detected from video metadata, category auto-picked by the backend.
              </div>
            </div>
          )}

        </div>
      </main>

      {/* ── Footer ── */}
      <footer style={{
        borderTop: "1px solid var(--border-color)",
        padding: "20px 24px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        background: "var(--dark)",
      }}>
        <div className="zw-wrap" style={{ width: "100%", padding: 0, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontFamily: "'Bebas Neue', sans-serif", fontSize: 16, letterSpacing: 3, color: "var(--lime)" }}>
            ZIROWORK
          </span>
          <span style={{ fontSize: 11, color: "var(--muted2-color)", letterSpacing: 1 }}>
            BRAIN AGENT v2.1 — DUAL-MODE INTELLIGENCE PROCESSOR
          </span>
        </div>
      </footer>

    </div>
  );
}
