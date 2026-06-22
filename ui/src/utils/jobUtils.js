export const SOURCE_LABELS = {
  linkedin: "LinkedIn",
  greenhouse: "Greenhouse",
  lever: "Lever",
  ashby: "Ashby",
  smartrecruiters: "SmartRecruiters",
};

export function formatDate(dateStr) {
  if (!dateStr) return "Unknown";
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return dateStr;
  return d.toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export function getSourceLabel(source) {
  return SOURCE_LABELS[source] || source;
}

export function getLinkLabel(source) {
  return source === "linkedin" ? "View on LinkedIn →" : "View on career site →";
}

export function getShortLinkLabel() {
  return "Open ↗";
}
