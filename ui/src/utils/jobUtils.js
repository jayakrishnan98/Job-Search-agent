export const SOURCE_LABELS = {
  linkedin: "LinkedIn",
  greenhouse: "Greenhouse",
  lever: "Lever",
  ashby: "Ashby",
  smartrecruiters: "SmartRecruiters",
  workday: "Workday",
};

export const POSTED_DAYS_OPTIONS = [
  { value: "all", label: "Any time" },
  { value: "1", label: "Last 1 day" },
  { value: "3", label: "Last 3 days" },
  { value: "7", label: "Last 7 days" },
  { value: "14", label: "Last 14 days" },
  { value: "30", label: "Last 30 days" },
];

const LOCATION_ALIASES = {
  bangalore: "Bengaluru",
  bengaluru: "Bengaluru",
  bombay: "Mumbai",
  mumbai: "Mumbai",
  calcutta: "Kolkata",
  kolkata: "Kolkata",
  gurgaon: "Gurugram",
  gurugram: "Gurugram",
  pune: "Pune",
  hyderabad: "Hyderabad",
  chennai: "Chennai",
  madras: "Chennai",
  remote: "Remote",
  wfh: "Remote",
  "work from home": "Remote",
  "home based": "Remote",
  "home-based": "Remote",
  distributed: "Remote",
};

const REMOTE_RE = /\b(remote|home based|home-based|distributed|wfh|work from home)\b/i;
const HYBRID_RE = /\bhybrid\b/i;
const LOCATION_COUNT_RE = /^\d+\s+locations$/i;
const JUNK_LOCATION_RE = /^[*#]/;
const COUNTRY_ONLY_RE =
  /^(usa|us|uk|uae|india|canada|germany|ireland|france|spain|italy|japan|australia|singapore|netherlands|sweden|denmark|switzerland|poland|israel|china|taiwan|south korea|united states|united kingdom)$/i;
const REGION_ONLY_RE = /^(apac|emea|americas|latam|worldwide|global)$/i;
const LOCATION_PRIORITY = { Remote: 0, Hybrid: 1 };

const MS_PER_DAY = 24 * 60 * 60 * 1000;

function startOfToday() {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return today;
}

function timestampDaysAgo(days) {
  const d = startOfToday();
  d.setDate(d.getDate() - days);
  return d.getTime();
}

function relativeAgeDays(value) {
  const text = String(value).trim();
  if (!text) return null;
  const lower = text.toLowerCase();

  if (/^(today|just posted|just now)$/.test(lower)) return 0;
  if (/hour|minute|second/.test(lower)) return 0;
  if (/^yesterday$/.test(lower)) return 1;

  const plusDays = lower.match(/^(\d+)\+\s*days?\s*ago$/);
  if (plusDays) return Number(plusDays[1]) + 1;

  const daysAgo = lower.match(/(?:^|posted\s+)(\d+)\s*d(?:ays?)?\s*ago$/);
  if (daysAgo) return Number(daysAgo[1]);

  const weeksAgo = lower.match(/(?:^|posted\s+)(\d+)\s*w(?:eeks?)?\s*ago$/);
  if (weeksAgo) return Number(weeksAgo[1]) * 7;

  return null;
}

export function parseDate(value) {
  if (!value) return 0;
  const text = String(value).trim();
  const age = relativeAgeDays(text);
  if (age != null) return timestampDaysAgo(age);

  const dateOnly = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (dateOnly) {
    return new Date(
      Number(dateOnly[1]),
      Number(dateOnly[2]) - 1,
      Number(dateOnly[3])
    ).getTime();
  }
  const d = new Date(text);
  return Number.isNaN(d.getTime()) ? 0 : d.getTime();
}

export function isPostedWithinDays(dateStr, days) {
  if (!days) return true;
  const posted = parseDate(dateStr);
  if (!posted) return false;
  return startOfToday().getTime() - posted <= days * MS_PER_DAY;
}

function locationVariants(selected) {
  const selectedLower = selected.toLowerCase();
  const variants = new Set([selectedLower]);
  for (const [alias, canonical] of Object.entries(LOCATION_ALIASES)) {
    if (canonical.toLowerCase() === selectedLower || alias === selectedLower) {
      variants.add(alias);
      variants.add(canonical.toLowerCase());
    }
  }
  return variants;
}

function normalizePlaceName(part) {
  const cleaned = part
    .replace(/^\*+/, "")
    .replace(/\bhybrid in\s+/i, "")
    .replace(/^(office based|home based|home-based)\s*[-–]\s*/i, "")
    .replace(/\((?:remote|hybrid)\)/gi, "")
    .replace(/\bremote\b/gi, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^[-–,;/]+|[-–,;/]+$/g, "");
  if (!cleaned) return "";
  const first = cleaned.split(/[,|/]/)[0].trim();
  const usCity = first.match(/^US-[A-Z]{2}-(.+)$/i);
  const place = (usCity ? usCity[1] : first).replace(/[-_]/g, " ").replace(/\s+/g, " ").trim();
  const key = place.toLowerCase();
  if (
    !place ||
    /^\d/.test(place) ||
    LOCATION_COUNT_RE.test(place) ||
    COUNTRY_ONLY_RE.test(key) ||
    REGION_ONLY_RE.test(key)
  ) {
    return "";
  }
  for (const [alias, canonical] of Object.entries(LOCATION_ALIASES)) {
    if (canonical === "Remote") continue;
    const names = [...new Set([alias, canonical.toLowerCase()])];
    for (const name of names) {
      if (name.length < 4) continue;
      if (new RegExp(`(^|[^a-z])${name}([^a-z]|$)`, "i").test(key)) {
        return canonical;
      }
    }
  }
  if (REMOTE_RE.test(place) || HYBRID_RE.test(place)) return "";
  return place;
}

export function locationTokens(location) {
  const raw = (location || "").trim();
  if (!raw || LOCATION_COUNT_RE.test(raw) || JUNK_LOCATION_RE.test(raw)) {
    return [];
  }

  const tokens = new Set();
  if (REMOTE_RE.test(raw)) tokens.add("Remote");
  if (HYBRID_RE.test(raw)) tokens.add("Hybrid");

  for (const part of raw.split(";")) {
    const place = normalizePlaceName(part.trim());
    if (place) tokens.add(place);
  }
  return [...tokens];
}

export function uniqueLocationOptions(jobs) {
  const values = new Set();
  for (const job of jobs) {
    for (const token of locationTokens(job.location)) {
      values.add(token);
    }
  }
  return [...values].sort((a, b) => {
    const pa = LOCATION_PRIORITY[a] ?? 2;
    const pb = LOCATION_PRIORITY[b] ?? 2;
    if (pa !== pb) return pa - pb;
    return a.localeCompare(b);
  });
}

export function locationMatches(jobLocation, selected) {
  if (!selected || selected === "all") return true;
  const tokens = locationTokens(jobLocation);
  const variants = locationVariants(selected);
  if (tokens.some((token) => variants.has(token.toLowerCase()))) {
    return true;
  }
  const loc = (jobLocation || "").toLowerCase();
  if (!loc) return false;
  return [...variants].some((variant) => {
    if (variant.length <= 2) {
      return new RegExp(`(^|[^a-z])${variant}([^a-z]|$)`, "i").test(loc);
    }
    return loc.includes(variant);
  });
}

export function formatDate(dateStr) {
  if (!dateStr) return "Unknown";
  const ts = parseDate(dateStr);
  if (!ts) return String(dateStr);
  return new Date(ts).toLocaleDateString("en-IN", {
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

export function formatAppliedDate(dateStr) {
  if (!dateStr) return "Applied";
  const formatted = formatDate(dateStr);
  if (formatted === "Unknown") return "Applied";
  return `Applied ${formatted}`;
}
