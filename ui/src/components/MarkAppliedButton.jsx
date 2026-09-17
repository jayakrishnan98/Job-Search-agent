import { memo, useState } from "react";

function MarkAppliedButton({ job, onToggle, compact = false }) {
  const [saving, setSaving] = useState(false);
  const applied = Boolean(job.is_applied);

  const handleClick = async () => {
    if (saving) return;
    setSaving(true);
    try {
      await onToggle(job.job_id, !applied);
    } finally {
      setSaving(false);
    }
  };

  return (
    <button
      type="button"
      className={`mark-applied-btn${applied ? " is-applied" : ""}${compact ? " is-compact" : ""}`}
      onClick={handleClick}
      disabled={saving}
    >
      {saving ? "Saving…" : applied ? "Move back" : "Mark applied"}
    </button>
  );
}

export default memo(MarkAppliedButton);
