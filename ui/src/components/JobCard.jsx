import { memo } from "react";
import { formatDate, getSourceLabel } from "../utils/jobUtils.js";
import JobApplyButton from "./JobApplyButton.jsx";

function JobCard({ job }) {
  const source = job.source || "linkedin";
  const sourceLabel = getSourceLabel(source);

  return (
    <article className={`job-card${job.is_new ? " is-new" : ""}`}>
      <div className="card-header">
        <h2 className="card-title">{job.title}</h2>
        <div className="card-badges">
          {job.is_new && <span className="badge-new">New</span>}
          <span className={`badge-source badge-${source}`}>{sourceLabel}</span>
        </div>
      </div>
      <div className="card-company">{job.company}</div>
      <div className="card-meta">
        <span>📍 {job.location || "—"}</span>
        <span>📅 Posted {formatDate(job.posted_date)}</span>
      </div>
      <div className="card-footer">
        <JobApplyButton job={job} />
      </div>
    </article>
  );
}

export default memo(JobCard);
