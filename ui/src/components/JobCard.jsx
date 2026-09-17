import { memo } from "react";
import { formatAppliedDate, formatDate, getSourceLabel } from "../utils/jobUtils.js";
import JobApplyButton from "./JobApplyButton.jsx";
import JobAiActions from "./JobAiActions.jsx";
import MarkAppliedButton from "./MarkAppliedButton.jsx";
import RemoveJobButton from "./RemoveJobButton.jsx";
import ScoreBadge from "./ScoreBadge.jsx";

function JobCard({ job, onSetApplied, onRemove, onScore, onGenerate, onGenerateCover, aiConfigured }) {
  const source = job.source || "linkedin";
  const sourceLabel = getSourceLabel(source);
  const applied = Boolean(job.is_applied);

  return (
    <article className={`job-card${job.is_new && !applied ? " is-new" : ""}${applied ? " is-applied" : ""}`}>
      <div className="card-header">
        <h2 className="card-title">{job.title}</h2>
        <div className="card-badges">
          {applied && <span className="badge-applied">Applied</span>}
          {job.is_new && !applied && <span className="badge-new">New</span>}
          <ScoreBadge job={job} />
          <span className={`badge-source badge-${source}`}>{sourceLabel}</span>
        </div>
      </div>
      <div className="card-company">{job.company}</div>
      <div className="card-meta">
        <span>📍 {job.location || "—"}</span>
        <span>📅 Posted {formatDate(job.posted_date)}</span>
        {applied && <span>✅ {formatAppliedDate(job.applied_at)}</span>}
      </div>
      <div className="card-footer card-footer-actions">
        <JobApplyButton job={job} />
        <MarkAppliedButton job={job} onToggle={onSetApplied} />
        <RemoveJobButton job={job} onRemove={onRemove} />
      </div>
      <JobAiActions
        job={job}
        aiConfigured={aiConfigured}
        onScore={onScore}
        onGenerate={onGenerate}
        onGenerateCover={onGenerateCover}
      />
    </article>
  );
}

export default memo(JobCard);
