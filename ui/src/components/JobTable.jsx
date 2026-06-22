import { memo } from "react";
import { formatDate, getSourceLabel } from "../utils/jobUtils.js";
import JobApplyButton from "./JobApplyButton.jsx";

const JobTableRow = memo(function JobTableRow({ job }) {
  const source = job.source || "linkedin";
  const sourceLabel = getSourceLabel(source);

  return (
    <tr className={job.is_new ? "is-new" : undefined}>
      <td className="cell-role">
        <div className="table-role-header">
          <span className="table-title">{job.title}</span>
          <div className="table-badges">
            {job.is_new && <span className="badge-new">New</span>}
            <span className={`badge-source badge-${source}`}>{sourceLabel}</span>
          </div>
        </div>
      </td>
      <td className="cell-company">{job.company}</td>
      <td className="cell-meta">📍 {job.location || "—"}</td>
      <td className="cell-meta">📅 Posted {formatDate(job.posted_date)}</td>
      <td className="cell-actions">
        <JobApplyButton job={job} compact short />
      </td>
    </tr>
  );
});

function JobTable({ jobs }) {
  return (
    <div className="job-table-wrap">
      <table className="job-table">
        <thead>
          <tr>
            <th>Role</th>
            <th>Company</th>
            <th>Location</th>
            <th>Posted</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <JobTableRow key={job.job_id} job={job} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default memo(JobTable);
