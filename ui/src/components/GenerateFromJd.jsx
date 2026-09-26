import { useEffect, useRef, useState } from "react";
import { scoreClass } from "./ScoreBadge.jsx";

function filenameFromDisposition(header, fallback) {
  if (!header) return fallback;
  const utfMatch = header.match(/filename\*\s*=\s*UTF-8''([^;]+)/i);
  if (utfMatch) {
    try {
      return decodeURIComponent(utfMatch[1].trim());
    } catch {
      return utfMatch[1].trim();
    }
  }
  const match = header.match(/filename\s*=\s*"([^"]+)"/i) || header.match(/filename\s*=\s*([^;]+)/i);
  if (!match) return fallback;
  return match[1].trim().replace(/^["']|["']$/g, "") || fallback;
}

function errorMessage(data, fallback) {
  const detail = data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg;
  return fallback;
}

export default function GenerateFromJd({ aiConfigured }) {
  const [title, setTitle] = useState("");
  const [company, setCompany] = useState("");
  const [location, setLocation] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [resume, setResume] = useState(null);
  const [cover, setCover] = useState(null);
  const resumeUrlRef = useRef(null);
  const coverUrlRef = useRef(null);

  useEffect(() => {
    return () => {
      if (resumeUrlRef.current) URL.revokeObjectURL(resumeUrlRef.current);
      if (coverUrlRef.current) URL.revokeObjectURL(coverUrlRef.current);
    };
  }, []);

  const ready =
    Boolean(title.trim() && company.trim() && description.trim()) &&
    aiConfigured &&
    !busy;

  const generate = async (kind) => {
    if (!ready) return;
    setBusy(kind);
    setError(null);
    try {
      const res = await fetch(
        kind === "resume" ? "/api/generate/resume" : "/api/generate/cover-letter",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: title.trim(),
            company: company.trim(),
            location: location.trim(),
            description: description.trim(),
          }),
        }
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(
          errorMessage(
            data,
            kind === "resume"
              ? "Resume generation failed"
              : "Cover letter generation failed"
          )
        );
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const fallback = kind === "resume" ? "resume.pdf" : "cover-letter.pdf";
      const name = filenameFromDisposition(
        res.headers.get("Content-Disposition"),
        fallback
      );
      if (kind === "resume") {
        if (resumeUrlRef.current) URL.revokeObjectURL(resumeUrlRef.current);
        resumeUrlRef.current = url;
        const atsHeader = res.headers.get("X-ATS-Score");
        const atsScore = atsHeader != null && atsHeader !== "" ? Number(atsHeader) : null;
        setResume({
          url,
          name,
          atsScore: Number.isFinite(atsScore) ? atsScore : null,
        });
      } else {
        if (coverUrlRef.current) URL.revokeObjectURL(coverUrlRef.current);
        coverUrlRef.current = url;
        setCover({ url, name });
      }
    } catch (err) {
      setError(err.message || "Generation failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="generate-page">
      <div className="generate-panel">
        <div className="generate-intro">
          <h2>Generate from a job description</h2>
          <p>
            Paste a listing and generate an ATS-friendly resume or cover letter
            separately. Files are tailored from your master resume and are not
            saved to the job list.
          </p>
        </div>

        <div className="generate-fields">
          <label className="generate-field">
            <span>Job title</span>
            <input
              className="search-input"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Senior Software Engineer"
              autoComplete="off"
            />
          </label>
          <label className="generate-field">
            <span>Company</span>
            <input
              className="search-input"
              type="text"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
              placeholder="Acme Corp"
              autoComplete="off"
            />
          </label>
          <label className="generate-field">
            <span>Location (optional)</span>
            <input
              className="search-input"
              type="text"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder="Remote, US"
              autoComplete="off"
            />
          </label>
        </div>

        <label className="generate-field generate-field-full">
          <span>Job description</span>
          <textarea
            className="generate-textarea"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Paste the full job description here…"
            rows={14}
          />
        </label>

        {error && <div className="error-banner">{error}</div>}

        {!aiConfigured ? (
          <p className="generate-hint">
            AI is not configured. Add <code>GEMINI_API_KEY</code> or{" "}
            <code>CLAUDE_API_KEY</code> to <code>.env</code> and restart the API
            server.
          </p>
        ) : (
          <div className="generate-actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => generate("resume")}
              disabled={!ready}
            >
              {busy === "resume" && <span className="spinner" />}
              {busy === "resume" ? "Generating resume…" : "Generate resume"}
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => generate("cover")}
              disabled={!ready}
            >
              {busy === "cover" && <span className="spinner" />}
              {busy === "cover"
                ? "Generating cover letter…"
                : "Generate cover letter"}
            </button>
          </div>
        )}

        {(resume || cover) && (
          <div className="generate-results">
            {resume && (
              <>
                {resume.atsScore != null && (
                  <span
                    className={`badge-score ${scoreClass(resume.atsScore)}`}
                    title="Resume ATS keyword coverage"
                  >
                    ATS {resume.atsScore}
                  </span>
                )}
                <a
                  className="ai-download-btn"
                  href={resume.url}
                  download={resume.name}
                >
                  Download resume
                </a>
              </>
            )}
            {cover && (
              <a
                className="ai-download-btn"
                href={cover.url}
                download={cover.name}
              >
                Download cover letter
              </a>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
