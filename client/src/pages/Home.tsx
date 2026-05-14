/* ============================================================
   ZiroWork Brain Agent — Home Page
   Design: ZiroWork design system
   - Bebas Neue (display) + DM Sans (body)
   - Lime #C5F135 primary, Purple #8B5CF6 secondary
   - Dark mode only, zero border-radius, no shadows, no gradients
   ============================================================ */

import { useState, useEffect, useRef } from "react";
import { Streamdown } from "streamdown";

// ── Types ─────────────────────────────────────────────────────────────────────
type StepStatus = "idle" | "running" | "done" | "error" | "skipped";

interface PipelineStep {
  id: number;
  label: string;
  description: string;
  status: StepStatus;
}

interface ProcessResult {
  status: "success" | "error";
  filename?: string;
  drive_url?: string;
  preview?: string;
  message?: string;
  error?: string;
  code?: string;
}

const INITIAL_STEPS: PipelineStep[] = [
  { id: 1, label: "Extract Audio",       description: "yt-dlp pulls audio from Instagram link",           status: "idle" },
  { id: 2, label: "Transcribe",          description: "OpenAI Whisper converts speech to text",           status: "idle" },
  { id: 3, label: "Process with Claude", description: "Claude cleans, structures, and expands transcript", status: "idle" },
  { id: 4, label: "Format Markdown",     description: "YAML front matter + structured sections",          status: "idle" },
  { id: 5, label: "Save to Drive",       description: "Write to ZiroWork-Brain/Raw Videos/",              status: "idle" },
  { id: 6, label: "Cleanup",             description: "Delete temp audio files",                          status: "idle" },
];

// ── Backend URL ───────────────────────────────────────────────────────────────
const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || "http://localhost:8000";

// ── Step Icon ─────────────────────────────────────────────────────────────────
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

// ── Select wrapper ────────────────────────────────────────────────────────────
function ZWSelect({
  label, value, onChange, options, placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
  placeholder: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <label className="zw-label">{label}</label>
      <div style={{ position: "relative" }}>
        <select
          className="zw-select"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">{placeholder}</option>
          {options.map((o) => (
            <option key={o} value={o}>{o}</option>
          ))}
        </select>
        <span style={{
          position: "absolute", right: 14, top: "50%", transform: "translateY(-50%)",
          color: "var(--muted-color)", pointerEvents: "none", fontSize: 12,
        }}>▼</span>
      </div>
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────
export default function Home() {
  const [link, setLink]         = useState("");
  const [creator, setCreator]   = useState("");
  const [category, setCategory] = useState("");
  const [steps, setSteps]       = useState<PipelineStep[]>(INITIAL_STEPS);
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult]     = useState<ProcessResult | null>(null);
  const [creators, setCreators] = useState<string[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [backendStatus, setBackendStatus] = useState<"unknown" | "online" | "offline">("unknown");
  const resultRef = useRef<HTMLDivElement>(null);

  // Load config from backend
  useEffect(() => {
    fetch(`${BACKEND_URL}/api/config`)
      .then((r) => r.json())
      .then((data) => {
        setCreators(data.approved_creators || []);
        setCategories(data.content_categories || []);
        setBackendStatus("online");
      })
      .catch(() => {
        // Fallback to defaults if backend not running
        setCreators(["Andrew Huberman", "Simon Willison", "Andrej Karpathy"]);
        setCategories(["Agent Design", "LLM Optimization", "Product Strategy", "AI Safety & Ethics", "Technical Architecture", "Business & Growth"]);
        setBackendStatus("offline");
      });
  }, []);

  // Scroll to result when done
  useEffect(() => {
    if (result && resultRef.current) {
      resultRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [result]);

  function resetSteps() {
    setSteps(INITIAL_STEPS.map((s) => ({ ...s, status: "idle" })));
  }

  function setStepStatus(id: number, status: StepStatus) {
    setSteps((prev) => prev.map((s) => s.id === id ? { ...s, status } : s));
  }

  function markAllAfterError(fromId: number) {
    setSteps((prev) => prev.map((s) => s.id > fromId ? { ...s, status: "skipped" } : s));
  }

  // Simulate step-by-step progress while waiting for API
  async function simulateProgress(abortSignal: AbortSignal): Promise<void> {
    const delays = [1200, 8000, 12000, 800, 1500, 400];
    for (let i = 0; i < INITIAL_STEPS.length; i++) {
      if (abortSignal.aborted) return;
      setStepStatus(i + 1, "running");
      await new Promise((res) => setTimeout(res, delays[i]));
      if (abortSignal.aborted) return;
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!link || !creator || !category) return;

    setIsRunning(true);
    setResult(null);
    resetSteps();

    const abortController = new AbortController();

    // Start simulated progress in parallel
    const progressPromise = simulateProgress(abortController.signal);

    try {
      const response = await fetch(`${BACKEND_URL}/api/process-video`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          instagram_link: link,
          creator,
          category,
        }),
        signal: AbortSignal.timeout(360_000), // 6 min timeout
      });

      abortController.abort(); // Stop simulated progress

      const data: ProcessResult = await response.json();

      if (data.status === "success") {
        // Mark all steps done
        setSteps(INITIAL_STEPS.map((s) => ({ ...s, status: "done" })));
      } else {
        // Mark first step as error, rest as skipped
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

  const completedSteps = steps.filter((s) => s.status === "done").length;
  const progress = isRunning
    ? (steps.findIndex((s) => s.status === "running") + 1) / INITIAL_STEPS.length * 100
    : result?.status === "success" ? 100 : 0;

  const canSubmit = link.trim() && creator && category && !isRunning;

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
              Backend not detected at {BACKEND_URL} — start it with: <code style={{ color: "var(--purple-glow)", background: "rgba(139,92,246,0.1)", padding: "2px 6px" }}>cd backend && python main.py</code>
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
              maxWidth: 480,
            }}>
              Paste an Instagram link. Get a clean markdown knowledge file in Google Drive — ready for Obsidian.
            </p>
          </div>

          {/* ── Pipeline overview ── */}
          <div className="zw-callout" style={{ marginBottom: 32, fontSize: 13 }}>
            <span style={{ color: "var(--lime)", fontWeight: 700 }}>PIPELINE:</span>{" "}
            Instagram Link → Audio Extract → Whisper Transcribe → Claude Process → Markdown → Google Drive
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

                {/* Creator + Category row */}
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
                  <ZWSelect
                    label="Creator"
                    value={creator}
                    onChange={setCreator}
                    options={creators}
                    placeholder="Select creator..."
                  />
                  <ZWSelect
                    label="Category"
                    value={category}
                    onChange={setCategory}
                    options={categories}
                    placeholder="Select category..."
                  />
                </div>

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
                  ) : (
                    "PROCESS VIDEO →"
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
                  {completedSteps}/{INITIAL_STEPS.length} STEPS
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
                    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
                      <span style={{
                        fontFamily: "'Bebas Neue', sans-serif",
                        fontSize: 20,
                        letterSpacing: 2,
                        color: "var(--lime)",
                      }}>SAVED TO DRIVE</span>
                      <span style={{ color: "var(--lime)", fontSize: 16 }}>✓</span>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                        <span className="zw-label" style={{ margin: 0, minWidth: 80 }}>FILENAME</span>
                        <span style={{ fontSize: 14, color: "var(--text-color)", fontFamily: "monospace" }}>
                          {result.filename}
                        </span>
                      </div>
                      {result.drive_url && (
                        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                          <span className="zw-label" style={{ margin: 0, minWidth: 80 }}>DRIVE</span>
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
                      {result.message && (
                        <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                          <span className="zw-label" style={{ margin: 0, minWidth: 80 }}>STATUS</span>
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
                      resetSteps();
                      setLink("");
                      setCreator("");
                      setCategory("");
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
                      resetSteps();
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
                <div style={{ display: "flex", gap: 20 }}>
                  <div style={{ flex: 1 }}>
                    <div className="zw-badge-purple" style={{ marginBottom: 10, display: "inline-flex" }}>COST</div>
                    <div style={{ fontSize: 13, color: "var(--muted-color)", lineHeight: 1.6 }}>
                      ~$0.10–0.17 per video<br />
                      <span style={{ color: "var(--muted2-color)", fontSize: 12 }}>Whisper + Claude combined</span>
                    </div>
                  </div>
                  <div style={{ flex: 1 }}>
                    <div className="zw-badge-purple" style={{ marginBottom: 10, display: "inline-flex" }}>SPEED</div>
                    <div style={{ fontSize: 13, color: "var(--muted-color)", lineHeight: 1.6 }}>
                      &lt;5 min for 30-min video<br />
                      <span style={{ color: "var(--muted2-color)", fontSize: 12 }}>Audio extract → Drive save</span>
                    </div>
                  </div>
                  <div style={{ flex: 1 }}>
                    <div className="zw-badge-purple" style={{ marginBottom: 10, display: "inline-flex" }}>OUTPUT</div>
                    <div style={{ fontSize: 13, color: "var(--muted-color)", lineHeight: 1.6 }}>
                      Clean .md file<br />
                      <span style={{ color: "var(--muted2-color)", fontSize: 12 }}>YAML front matter + Obsidian links</span>
                    </div>
                  </div>
                </div>
              </div>

              <div className="zw-callout" style={{ fontSize: 13 }}>
                <strong style={{ color: "var(--lime)" }}>No video storage.</strong>{" "}
                Audio is extracted, transcribed, and deleted. Only the markdown file is saved.
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
            BRAIN AGENT v1.0 — INTELLIGENCE PROCESSOR
          </span>
        </div>
      </footer>

    </div>
  );
}
