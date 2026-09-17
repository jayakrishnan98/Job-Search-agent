function scoreClass(score) {
  if (score >= 80) return "is-strong";
  if (score >= 65) return "is-good";
  if (score >= 40) return "is-weak";
  return "is-none";
}

export default function ScoreBadge({ job }) {
  if (job.ai_score == null) return null;
  return <span className={`badge-score ${scoreClass(job.ai_score)}`}>{job.ai_score}</span>;
}

export { scoreClass };
