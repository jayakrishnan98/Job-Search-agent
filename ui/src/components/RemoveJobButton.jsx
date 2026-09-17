import { memo, useState } from "react";

function RemoveJobButton({ job, onRemove, compact = false }) {
  const [saving, setSaving] = useState(false);

  const handleClick = async () => {
    if (saving) return;
    const title = job.title || "this job";
    const company = job.company ? ` at ${job.company}` : "";
    const confirmed = window.confirm(
      `Remove “${title}”${company} from the list?\n\nIt will stay hidden even if it still appears on the next fetch.`
    );
    if (!confirmed) return;

    setSaving(true);
    try {
      await onRemove(job.job_id);
    } finally {
      setSaving(false);
    }
  };

  return (
    <button
      type="button"
      className={`remove-job-btn${compact ? " is-compact" : ""}`}
      onClick={handleClick}
      disabled={saving}
    >
      {saving ? "Removing…" : "Remove"}
    </button>
  );
}

export default memo(RemoveJobButton);
