import { memo } from "react";
import { formatAppliedDate, formatDate, getSourceLabel } from "../utils/jobUtils.js";
import JobApplyButton from "./JobApplyButton.jsx";
import JobAiActions from "./JobAiActions.jsx";
import MarkAppliedButton from "./MarkAppliedButton.jsx";
import RemoveJobButton from "./RemoveJobButton.jsx";
import ScoreBadge from "./ScoreBadge.jsx";

const JobTableRow = memo(function JobTableRow({
  job,
  onSetApplied,
  onRemove,
  onScore,
  onGenerate,
  onGenerateCover,
  aiConfigured,
  showAppliedDate,
}) {
  const source = job.source || "linkedin";
  const sourceLabel = getSourceLabel(source);
  const applied = Boolean(job.is_applied);

  return (
    <tr className={applied ? "is-applied" : job.is_new ? "is-new" : undefined}>
      <td className="cell-role">
        <div className="table-role-header">
          <span className="table-title">{job.title}</span>
          <div className="table-badges">
            {applied && <span className="badge-applied">Applied</span>}
            {job.is_new && !applied && <span className="badge-new">New</span>}
            <span className={`badge-source badge-${source}`}>{sourceLabel}</span>
          </div>
        </div>
      </td>
      <td className="cell-company">{job.company}</td>
      <td className="cell-score">
        <ScoreBadge job={job} />
      </td>
      <td className="cell-meta">📍 {job.location || "—"}</td>
      <td className="cell-meta">📅 Posted {formatDate(job.posted_date)}</td>
      {showAppliedDate && (
        <td className="cell-meta">✅ {formatAppliedDate(job.applied_at)}</td>
      )}
      <td className="cell-actions">
        <div className="table-actions">
          <JobApplyButton job={job} compact short />
          <MarkAppliedButton job={job} onToggle={onSetApplied} compact />
          <RemoveJobButton job={job} onRemove={onRemove} compact />
          <JobAiActions
            job={job}
            aiConfigured={aiConfigured}
            onScore={onScore}
            onGenerate={onGenerate}
            onGenerateCover={onGenerateCover}
            compact
          />
        </div>
      </td>
    </tr>
  );
});

function JobTable({
  jobs,
  onSetApplied,
  onRemove,
  onScore,
  onGenerate,
  onGenerateCover,
  aiConfigured,
  showAppliedDate = false,
}) {
  return (
    <div className="job-table-wrap">
      <table className="job-table">
        <thead>
          <tr>
            <th>Role</th>
            <th>Company</th>
            <th>Match</th>
            <th>Location</th>
            <th>Posted</th>
            {showAppliedDate && <th>Applied</th>}
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <JobTableRow
              key={job.job_id}
              job={job}
              onSetApplied={onSetApplied}
              onRemove={onRemove}
              onScore={onScore}
              onGenerate={onGenerate}
              onGenerateCover={onGenerateCover}
              aiConfigured={aiConfigured}
              showAppliedDate={showAppliedDate}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default memo(JobTable);
