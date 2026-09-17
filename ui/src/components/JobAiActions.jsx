import { memo, useState } from "react";

function JobAiActions({
  job,
  aiConfigured,
  onScore,
  onGenerate,
  onGenerateCover,
  compact = false,
}) {
  const [busy, setBusy] = useState(null);

  const run = async (kind, action) => {
    if (busy) return;
    setBusy(kind);
    try {
      await action(job.job_id);
    } finally {
      setBusy(null);
    }
  };

  if (
    !aiConfigured &&
    job.ai_score == null &&
    !job.has_resume &&
    !job.has_cover_letter
  ) {
    return null;
  }

  return (
    <div className={`job-ai-actions${compact ? " is-compact" : ""}`}>
      {aiConfigured && (
        <button
          type="button"
          className={`ai-action-btn${compact ? " is-compact" : ""}`}
          onClick={() => run("score", onScore)}
          disabled={Boolean(busy)}
        >
          {busy === "score" ? "Scoring…" : job.ai_score == null ? "Score" : "Re-score"}
        </button>
      )}
      {aiConfigured && (
        <button
          type="button"
          className={`ai-action-btn ai-action-btn-primary${compact ? " is-compact" : ""}`}
          onClick={() => run("resume", onGenerate)}
          disabled={Boolean(busy)}
        >
          {busy === "resume"
            ? "Generating…"
            : job.has_resume
              ? "Regenerate resume"
              : "Generate resume"}
        </button>
      )}
      {job.has_resume && (
        <a
          className={`ai-download-btn${compact ? " is-compact" : ""}`}
          href={`/api/jobs/${encodeURIComponent(job.job_id)}/resume`}
        >
          Download resume
        </a>
      )}
      {aiConfigured && (
        <button
          type="button"
          className={`ai-action-btn${compact ? " is-compact" : ""}`}
          onClick={() => run("cover", onGenerateCover)}
          disabled={Boolean(busy)}
        >
          {busy === "cover"
            ? "Generating…"
            : job.has_cover_letter
              ? "Regenerate cover letter"
              : "Generate cover letter"}
        </button>
      )}
      {job.has_cover_letter && (
        <a
          className={`ai-download-btn${compact ? " is-compact" : ""}`}
          href={`/api/jobs/${encodeURIComponent(job.job_id)}/cover-letter`}
        >
          Download cover letter
        </a>
      )}
    </div>
  );
}

export default memo(JobAiActions);
