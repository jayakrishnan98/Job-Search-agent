import { memo } from "react";
import { getLinkLabel, getShortLinkLabel } from "../utils/jobUtils.js";

function JobApplyButton({ job, compact = false, short = false }) {
  const source = job.source || "linkedin";
  const linkLabel = short ? getShortLinkLabel() : getLinkLabel(source);

  if (!job.job_url) {
    return <span className="job-apply-btn job-apply-btn-disabled">No link</span>;
  }

  return (
    <a
      className={`job-apply-btn${compact ? " job-apply-btn-compact" : ""}`}
      href={job.job_url}
      target="_blank"
      rel="noopener noreferrer"
    >
      {linkLabel}
    </a>
  );
}

export default memo(JobApplyButton);
