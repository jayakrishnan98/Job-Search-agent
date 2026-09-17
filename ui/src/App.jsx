import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import JobCard from "./components/JobCard.jsx";
import JobTable from "./components/JobTable.jsx";
import FetchCountdown from "./components/FetchCountdown.jsx";
import GenerateFromJd from "./components/GenerateFromJd.jsx";
import {
  POSTED_DAYS_OPTIONS,
  isPostedWithinDays,
  locationMatches,
  parseDate,
  uniqueLocationOptions,
} from "./utils/jobUtils.js";
import "./App.css";

const NewJobCelebration = lazy(() => import("./components/NewJobCelebration.jsx"));

const POLL_MS = 30000;
const META_POLL_MS = 60000;
const SEARCH_DEBOUNCE_MS = 250;

function jobsFingerprint(jobs) {
  return jobs
    .map(
      (job) =>
        `${job.job_id}:${job.is_new ? 1 : 0}:${job.is_applied ? 1 : 0}:${job.ai_score ?? ""}:${job.has_resume ? 1 : 0}:${job.has_cover_letter ? 1 : 0}`
    )
    .join("|");
}

function uniqueSorted(values) {
  return [...new Set(values.filter(Boolean))].sort();
}

export default function App() {
  const [jobs, setJobs] = useState([]);
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState(null);

  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [companyFilter, setCompanyFilter] = useState("all");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [locationFilter, setLocationFilter] = useState("all");
  const [postedDaysFilter, setPostedDaysFilter] = useState("all");
  const [sortOrder, setSortOrder] = useState("highest");
  const [viewMode, setViewMode] = useState("cards");
  const [showNewOnly, setShowNewOnly] = useState(false);
  const [section, setSection] = useState("openings");
  const [emailMeta, setEmailMeta] = useState(null);
  const [celebrate, setCelebrate] = useState(false);
  const [celebrateCount, setCelebrateCount] = useState(0);
  const [appPulse, setAppPulse] = useState(false);

  const prevNewJobIdsRef = useRef(null);
  const updatedAtRef = useRef(null);
  const scoredCountRef = useRef(null);
  const jobsFingerprintRef = useRef("");
  const jobsRef = useRef([]);

  useEffect(() => {
    const id = setTimeout(() => setDebouncedSearch(search), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [search]);

  const detectNewJobs = useCallback((nextJobs) => {
    const newJobIds = new Set(
      nextJobs
        .filter((job) => job.is_new && !job.is_applied)
        .map((job) => job.job_id)
    );
    if (prevNewJobIdsRef.current !== null) {
      let added = 0;
      for (const id of newJobIds) {
        if (!prevNewJobIdsRef.current.has(id)) added += 1;
      }
      if (added > 0) {
        setCelebrateCount(added);
        setCelebrate(true);
        setAppPulse(true);
      }
    }
    prevNewJobIdsRef.current = newJobIds;
  }, []);

  const applyJobsPayload = useCallback(
    (data) => {
      const nextJobs = data.jobs || [];
      const fingerprint = jobsFingerprint(nextJobs);
      const changed =
        fingerprint !== jobsFingerprintRef.current ||
        data.updated_at !== updatedAtRef.current;

      if (!changed) {
        return false;
      }

      detectNewJobs(nextJobs);
      jobsFingerprintRef.current = fingerprint;
      updatedAtRef.current = data.updated_at ?? null;
      scoredCountRef.current = data.scored_count ?? 0;
      jobsRef.current = nextJobs;
      setJobs(nextJobs);
      setMeta(data);
      return true;
    },
    [detectNewJobs]
  );

  const loadMeta = useCallback(async () => {
    try {
      const res = await fetch("/api/meta");
      if (res.ok) {
        setEmailMeta(await res.json());
      }
    } catch {
      // Non-critical for the main dashboard.
    }
  }, []);

  const loadFullJobs = useCallback(async () => {
    const res = await fetch("/api/jobs?applied=all");
    if (!res.ok) throw new Error("Failed to load jobs");
    const data = await res.json();
    applyJobsPayload(data);
    setError(null);
    return data;
  }, [applyJobsPayload]);

  const pollForChanges = useCallback(async () => {
    try {
      const res = await fetch("/api/jobs/status");
      if (!res.ok) return;
      const status = await res.json();
      const scoredChanged = status.scored_count !== scoredCountRef.current;

      if (status.updated_at !== updatedAtRef.current || scoredChanged) {
        await loadFullJobs();
        setMeta((prev) => ({
          ...(prev || {}),
          scoring: status.scoring,
          scored_count: status.scored_count,
          unscored_count: status.unscored_count,
        }));
        return;
      }

      setMeta((prev) => {
        if (
          !prev ||
          (prev.new_count === status.new_count &&
            prev.total === status.total &&
            prev.applied_count === status.applied_count &&
            prev.scored_count === status.scored_count &&
            prev.unscored_count === status.unscored_count &&
            prev.scoring?.running === status.scoring?.running &&
            prev.scoring?.remaining === status.scoring?.remaining)
        ) {
          return prev;
        }
        return {
          ...prev,
          new_count: status.new_count,
          total: status.total,
          applied_count: status.applied_count,
          scored_count: status.scored_count,
          unscored_count: status.unscored_count,
          scoring: status.scoring,
        };
      });
    } catch (err) {
      setError(err.message);
    }
  }, [loadFullJobs]);

  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      try {
        await Promise.all([loadFullJobs(), loadMeta()]);
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    bootstrap();
    return () => {
      cancelled = true;
    };
  }, [loadFullJobs, loadMeta]);

  const scoring = meta?.scoring || emailMeta?.scoring;
  const scoringRunning = Boolean(scoring?.running);

  useEffect(() => {
    const pollMs = scoringRunning ? 5000 : POLL_MS;
    const pollId = setInterval(() => {
      if (!document.hidden) {
        pollForChanges();
      }
    }, pollMs);

    const metaId = setInterval(() => {
      if (!document.hidden) {
        loadMeta();
      }
    }, META_POLL_MS);

    const onVisibility = () => {
      if (!document.hidden) {
        pollForChanges();
        loadMeta();
      }
    };

    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      clearInterval(pollId);
      clearInterval(metaId);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [pollForChanges, loadMeta, scoringRunning]);

  useEffect(() => {
    if (!appPulse) return undefined;
    const id = setTimeout(() => setAppPulse(false), 5000);
    return () => clearTimeout(id);
  }, [appPulse]);

  const handleCelebrateDone = useCallback(() => {
    setCelebrate(false);
  }, []);

  const companies = useMemo(
    () => meta?.companies ?? [],
    [meta?.companies]
  );

  const sources = useMemo(
    () => meta?.sources ?? [],
    [meta?.sources]
  );

  const locations = useMemo(() => uniqueLocationOptions(jobs), [jobs]);

  const filteredJobs = useMemo(() => {
    const q = debouncedSearch.toLowerCase().trim();
    const postedDays =
      postedDaysFilter === "all" ? 0 : Number(postedDaysFilter);

    let result = jobs.filter((job) => {
      const applied = Boolean(job.is_applied);
      if (section === "applied" ? !applied : applied) {
        return false;
      }
      if (section === "openings" && showNewOnly && !job.is_new) {
        return false;
      }
      if (companyFilter !== "all" && job.company !== companyFilter) {
        return false;
      }
      if (sourceFilter !== "all" && job.source !== sourceFilter) {
        return false;
      }
      if (
        locationFilter !== "all" &&
        !locationMatches(job.location, locationFilter)
      ) {
        return false;
      }
      if (postedDays && !isPostedWithinDays(job.posted_date, postedDays)) {
        return false;
      }
      if (!q) return true;
      const title = (job.title || "").toLowerCase();
      const company = (job.company || "").toLowerCase();
      return title.includes(q) || company.includes(q);
    });

    result = [...result].sort((a, b) => {
      if (sortOrder === "highest") {
        const aHas = a.ai_score != null;
        const bHas = b.ai_score != null;
        if (aHas !== bHas) {
          return aHas ? -1 : 1;
        }
        if (aHas && a.ai_score !== b.ai_score) {
          return b.ai_score - a.ai_score;
        }
        const da = parseDate(a.posted_date);
        const db = parseDate(b.posted_date);
        return db - da;
      }
      const aNew = Boolean(a.is_new);
      const bNew = Boolean(b.is_new);
      if (aNew !== bNew) {
        return aNew ? -1 : 1;
      }
      const da = parseDate(a.posted_date);
      const db = parseDate(b.posted_date);
      return sortOrder === "newest" ? db - da : da - db;
    });

    return result;
  }, [
    jobs,
    debouncedSearch,
    companyFilter,
    sourceFilter,
    locationFilter,
    postedDaysFilter,
    sortOrder,
    showNewOnly,
    section,
  ]);

  const openingJobs = useMemo(
    () => jobs.filter((job) => !job.is_applied),
    [jobs]
  );
  const appliedJobs = useMemo(
    () => jobs.filter((job) => job.is_applied),
    [jobs]
  );
  const newCount = meta?.new_count ?? openingJobs.filter((job) => job.is_new).length;
  const sectionTotal = section === "applied" ? appliedJobs.length : openingJobs.length;

  const handleFetch = useCallback(async () => {
    setFetching(true);
    setError(null);
    try {
      const res = await fetch("/api/fetch/sync", { method: "POST" });
      const data = await res.json();
      if (data.status === "skipped") {
        const mins = Math.ceil((data.next_fetch_in_seconds || 0) / 60);
        setError(`Fetch runs every 5 minutes. Next fetch in ~${mins} min.`);
        await loadMeta();
        return;
      }
      if (data.status === "error") {
        throw new Error(data.message || "Fetch failed");
      }
      await Promise.all([loadFullJobs(), loadMeta()]);
    } catch (err) {
      setError(err.message);
    } finally {
      setFetching(false);
    }
  }, [loadFullJobs, loadMeta]);

  const handleMarkRead = useCallback(async () => {
    await fetch("/api/jobs/mark-read", { method: "POST" });
    setJobs((prev) => {
      const next = prev.map((job) => ({ ...job, is_new: false }));
      jobsRef.current = next;
      return next;
    });
    setMeta((prev) => (prev ? { ...prev, new_count: 0 } : prev));
    prevNewJobIdsRef.current = new Set();
    await loadMeta();
  }, [loadMeta]);

  const handleSetApplied = useCallback(async (jobId, applied) => {
    const previous = jobsRef.current.find((job) => job.job_id === jobId);
    if (!previous || Boolean(previous.is_applied) === applied) {
      return;
    }

    const optimistic = {
      ...previous,
      is_applied: applied,
      applied_at: applied ? new Date().toISOString() : "",
      is_new: applied ? false : previous.is_new,
    };

    const applyLocal = (nextJob) => {
      const next = jobsRef.current.map((job) =>
        job.job_id === jobId ? nextJob : job
      );
      jobsRef.current = next;
      jobsFingerprintRef.current = jobsFingerprint(next);
      setJobs(next);
    };

    applyLocal(optimistic);
    setMeta((prev) => {
      if (!prev) return prev;
      const delta = applied ? 1 : -1;
      const wasNew = Boolean(previous.is_new) && applied;
      return {
        ...prev,
        total: Math.max(0, (prev.total ?? 0) - delta),
        applied_count: Math.max(0, (prev.applied_count ?? 0) + delta),
        new_count: wasNew
          ? Math.max(0, (prev.new_count ?? 0) - 1)
          : prev.new_count,
      };
    });

    try {
      const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/applied`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ applied }),
      });
      if (!res.ok) {
        throw new Error("Failed to update applied status");
      }
      const data = await res.json();
      if (data.job) {
        applyLocal({ ...optimistic, ...data.job });
      }
    } catch (err) {
      applyLocal(previous);
      setMeta((prev) => {
        if (!prev) return prev;
        const delta = applied ? 1 : -1;
        return {
          ...prev,
          total: Math.max(0, (prev.total ?? 0) + delta),
          applied_count: Math.max(0, (prev.applied_count ?? 0) - delta),
          new_count:
            Boolean(previous.is_new) && applied
              ? (prev.new_count ?? 0) + 1
              : prev.new_count,
        };
      });
      setError(err.message);
    }
  }, []);

  const handleRemoveJob = useCallback(async (jobId) => {
    const previous = jobsRef.current.find((job) => job.job_id === jobId);
    if (!previous) {
      return;
    }

    const remaining = jobsRef.current.filter((job) => job.job_id !== jobId);
    jobsRef.current = remaining;
    jobsFingerprintRef.current = jobsFingerprint(remaining);
    setJobs(remaining);
    prevNewJobIdsRef.current?.delete(jobId);
    setCompanyFilter((prev) =>
      prev !== "all" && !remaining.some((job) => job.company === prev) ? "all" : prev
    );
    setSourceFilter((prev) =>
      prev !== "all" && !remaining.some((job) => job.source === prev) ? "all" : prev
    );
    setLocationFilter((prev) =>
      prev !== "all" &&
      !remaining.some((job) => locationMatches(job.location, prev))
        ? "all"
        : prev
    );

    const applied = Boolean(previous.is_applied);
    const wasNew = Boolean(previous.is_new) && !applied;
    setMeta((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        total: applied ? prev.total : Math.max(0, (prev.total ?? 0) - 1),
        applied_count: applied
          ? Math.max(0, (prev.applied_count ?? 0) - 1)
          : prev.applied_count,
        new_count: wasNew ? Math.max(0, (prev.new_count ?? 0) - 1) : prev.new_count,
        companies: uniqueSorted(remaining.map((job) => job.company)),
        sources: uniqueSorted(remaining.map((job) => job.source)),
      };
    });

    try {
      const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, {
        method: "DELETE",
      });
      if (!res.ok) {
        throw new Error("Failed to remove job");
      }
      const data = await res.json().catch(() => ({}));
      const extraIds = new Set(
        (data.removed_ids || []).filter((id) => id && id !== jobId)
      );
      if (extraIds.size) {
        const extras = jobsRef.current.filter((job) => extraIds.has(job.job_id));
        const next = jobsRef.current.filter((job) => !extraIds.has(job.job_id));
        jobsRef.current = next;
        jobsFingerprintRef.current = jobsFingerprint(next);
        setJobs(next);
        for (const extra of extras) {
          prevNewJobIdsRef.current?.delete(extra.job_id);
        }
        setCompanyFilter((prev) =>
          prev !== "all" && !next.some((job) => job.company === prev) ? "all" : prev
        );
        setSourceFilter((prev) =>
          prev !== "all" && !next.some((job) => job.source === prev) ? "all" : prev
        );
        setLocationFilter((prev) =>
          prev !== "all" &&
          !next.some((job) => locationMatches(job.location, prev))
            ? "all"
            : prev
        );
        setMeta((prev) => {
          if (!prev) return prev;
          let total = prev.total ?? 0;
          let appliedCount = prev.applied_count ?? 0;
          let newCount = prev.new_count ?? 0;
          for (const extra of extras) {
            if (extra.is_applied) {
              appliedCount = Math.max(0, appliedCount - 1);
            } else {
              total = Math.max(0, total - 1);
              if (extra.is_new) {
                newCount = Math.max(0, newCount - 1);
              }
            }
          }
          return {
            ...prev,
            total,
            applied_count: appliedCount,
            new_count: newCount,
            companies: uniqueSorted(next.map((job) => job.company)),
            sources: uniqueSorted(next.map((job) => job.source)),
          };
        });
      }
    } catch (err) {
      const restored = jobsRef.current.some((job) => job.job_id === jobId)
        ? jobsRef.current
        : [...jobsRef.current, previous];
      jobsRef.current = restored;
      jobsFingerprintRef.current = jobsFingerprint(restored);
      setJobs(restored);
      if (wasNew) {
        prevNewJobIdsRef.current?.add(jobId);
      }
      setMeta((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          total: applied ? prev.total : (prev.total ?? 0) + 1,
          applied_count: applied
            ? (prev.applied_count ?? 0) + 1
            : prev.applied_count,
          new_count: wasNew ? (prev.new_count ?? 0) + 1 : prev.new_count,
          companies: uniqueSorted(restored.map((job) => job.company)),
          sources: uniqueSorted(restored.map((job) => job.source)),
        };
      });
      setError(err.message);
    }
  }, []);

  const patchJob = useCallback((nextJob) => {
    const next = jobsRef.current.map((job) =>
      job.job_id === nextJob.job_id ? { ...job, ...nextJob } : job
    );
    jobsRef.current = next;
    jobsFingerprintRef.current = jobsFingerprint(next);
    setJobs(next);
  }, []);

  const handleScoreJob = useCallback(
    async (jobId) => {
      const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/score`, {
        method: "POST",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const message = data.detail || "Scoring failed";
        const text = typeof message === "string" ? message : "Scoring failed";
        setError(text);
        throw new Error(text);
      }
      if (data.job) {
        patchJob(data.job);
      }
    },
    [patchJob]
  );

  const handleGenerateResume = useCallback(
    async (jobId) => {
      const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/resume`, {
        method: "POST",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const message = data.detail || "Resume generation failed";
        const text = typeof message === "string" ? message : "Resume generation failed";
        setError(text);
        throw new Error(text);
      }
      if (data.job) {
        patchJob(data.job);
      }
    },
    [patchJob]
  );

  const handleGenerateCoverLetter = useCallback(
    async (jobId) => {
      const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/cover-letter`, {
        method: "POST",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const message = data.detail || "Cover letter generation failed";
        const text =
          typeof message === "string" ? message : "Cover letter generation failed";
        setError(text);
        throw new Error(text);
      }
      if (data.job) {
        patchJob(data.job);
      }
    },
    [patchJob]
  );

  const formatUpdated = useCallback((iso) => {
    if (!iso) return "Never";
    return new Date(iso).toLocaleString("en-IN");
  }, []);

  return (
    <div className={`app${appPulse ? " app-celebrate-pulse" : ""}`}>
      {celebrate && (
        <Suspense fallback={null}>
          <NewJobCelebration count={celebrateCount} onDone={handleCelebrateDone} />
        </Suspense>
      )}
      <header className="header">
        <div className="header-top">
          <div>
            <h1>Job Agent</h1>
            <p className="subtitle">
              {section === "generate"
                ? "Paste a job description to generate an ATS-friendly resume or cover letter"
                : "Jobs from career sites and LinkedIn across your shortlisted companies"}
            </p>
          </div>
          <div className="header-actions">
            <div className="section-tabs" role="tablist" aria-label="App sections">
              <button
                type="button"
                role="tab"
                className={`section-tab${section === "openings" ? " is-active" : ""}`}
                aria-selected={section === "openings"}
                onClick={() => setSection("openings")}
              >
                Openings
                <span className="section-tab-count">{openingJobs.length}</span>
              </button>
              <button
                type="button"
                role="tab"
                className={`section-tab${section === "applied" ? " is-active" : ""}`}
                aria-selected={section === "applied"}
                onClick={() => setSection("applied")}
              >
                Applied
                <span className="section-tab-count">{appliedJobs.length}</span>
              </button>
              <button
                type="button"
                role="tab"
                className={`section-tab${section === "generate" ? " is-active" : ""}`}
                aria-selected={section === "generate"}
                onClick={() => setSection("generate")}
              >
                Generate
              </button>
            </div>
            {section === "openings" && newCount > 0 && (
              <button className="btn" onClick={handleMarkRead}>
                Mark all read
              </button>
            )}
            {section !== "generate" && (
              <button
                className="btn btn-primary"
                onClick={handleFetch}
                disabled={fetching}
              >
                {fetching && <span className="spinner" />}
                {fetching ? "Fetching…" : "Fetch jobs"}
              </button>
            )}
          </div>
        </div>

        {section !== "generate" && (
        <div className="controls">
          <input
            className="search-input"
            type="search"
            placeholder="Search by role or company…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <select
            className="select"
            value={companyFilter}
            onChange={(e) => setCompanyFilter(e.target.value)}
          >
            <option value="all">All companies</option>
            {companies.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <select
            className="select"
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
          >
            <option value="all">All sources</option>
            {sources.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <select
            className="select"
            value={locationFilter}
            onChange={(e) => setLocationFilter(e.target.value)}
            aria-label="Filter by location"
          >
            <option value="all">All locations</option>
            {locations.map((location) => (
              <option key={location} value={location}>
                {location}
              </option>
            ))}
          </select>
          <select
            className="select"
            value={postedDaysFilter}
            onChange={(e) => setPostedDaysFilter(e.target.value)}
            aria-label="Filter by posted date"
          >
            {POSTED_DAYS_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <select
            className="select"
            value={sortOrder}
            onChange={(e) => setSortOrder(e.target.value)}
          >
            <option value="highest">Highest match</option>
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
          </select>
          {section === "openings" && (
            <button
              type="button"
              className={`new-only-toggle${showNewOnly ? " is-active" : ""}${newCount > 0 ? " has-new" : ""}`}
              onClick={() => setShowNewOnly((prev) => !prev)}
              aria-pressed={showNewOnly}
              title={showNewOnly ? "Show all jobs" : "Show only new jobs"}
            >
              <span className="new-only-dot" aria-hidden="true" />
              <span className="new-only-label">
                {showNewOnly ? "All jobs" : "New only"}
              </span>
              {newCount > 0 && (
                <span className="new-only-count">{newCount}</span>
              )}
            </button>
          )}
        </div>
        )}
      </header>

      {section === "generate" && (
        <GenerateFromJd aiConfigured={Boolean(emailMeta?.ai?.configured)} />
      )}

      {section !== "generate" && error && <div className="error-banner">{error}</div>}

      {section !== "generate" && (emailMeta?.email_config_issue || emailMeta?.email_status?.ok === false) && (
        <div className="error-banner">
          Email alerts are not working
          {": "}
          {emailMeta.email_config_issue ||
            emailMeta.email_status?.error ||
            "Check your email settings in .env"}
          {" "}
          Add <code>NOTIFY_EMAIL</code> and <code>GMAIL_APP_PASSWORD</code> (or Maileroo
          settings) to <code>.env</code>, then restart the API server.
        </div>
      )}

      {section !== "generate" && emailMeta && emailMeta.ai && emailMeta.ai.configured === false && (
        <div className="error-banner">
          AI scoring is off. Add <code>GEMINI_API_KEY</code> to <code>.env</code> and
          restart the API server to score jobs and generate tailored resumes and cover
          letters.
        </div>
      )}

      {section !== "generate" && (
      <div className="stats-bar">
        <span>
          Showing <strong>{filteredJobs.length}</strong> of{" "}
          <strong>{sectionTotal}</strong>{" "}
          {section === "applied" ? "applied jobs" : "openings"}
        </span>
        <span>
          Last updated: <strong>{formatUpdated(meta?.updated_at)}</strong>
        </span>
        {scoringRunning && (
          <span>
            Scoring <strong>{meta?.scored_count ?? scoring?.scored ?? 0}</strong>
            {" / "}
            <strong>
              {(meta?.scored_count ?? 0) + (meta?.unscored_count ?? scoring?.remaining ?? 0)}
            </strong>
          </span>
        )}
        {!scoringRunning && (meta?.unscored_count ?? 0) > 0 && emailMeta?.ai?.configured && (
          <span>
            Unscored: <strong>{meta.unscored_count}</strong>
          </span>
        )}
        <FetchCountdown
          nextFetchAt={emailMeta?.fetch?.next_fetch_at}
          pollIntervalMinutes={emailMeta?.poll_interval_minutes}
          running={emailMeta?.fetch?.running || fetching}
        />
        <div className="view-toggle" role="group" aria-label="View mode">
          <button
            type="button"
            className={`view-toggle-btn${viewMode === "cards" ? " is-active" : ""}`}
            onClick={() => setViewMode("cards")}
            aria-pressed={viewMode === "cards"}
          >
            Cards
          </button>
          <button
            type="button"
            className={`view-toggle-btn${viewMode === "table" ? " is-active" : ""}`}
            onClick={() => setViewMode("table")}
            aria-pressed={viewMode === "table"}
          >
            Table
          </button>
        </div>
      </div>
      )}

      {section !== "generate" && (loading ? (
        <div className="loading">Loading jobs…</div>
      ) : filteredJobs.length === 0 ? (
        <div className="empty">
          {section === "applied"
            ? appliedJobs.length === 0
              ? "No applied jobs yet. Mark a listing as applied from Openings and it will show up here."
              : "No applied jobs match your search or filter."
            : openingJobs.length === 0
              ? jobs.length === 0
                ? "No jobs yet. Click “Fetch jobs” to start."
                : "All current jobs are in Applied. Open that section to review them."
              : showNewOnly
                ? "No new jobs right now. Turn off “New only” in the filters above to see everything."
                : "No jobs match your search or filter."}
        </div>
      ) : viewMode === "table" ? (
        <JobTable
          jobs={filteredJobs}
          onSetApplied={handleSetApplied}
          onRemove={handleRemoveJob}
          onScore={handleScoreJob}
          onGenerate={handleGenerateResume}
          onGenerateCover={handleGenerateCoverLetter}
          aiConfigured={Boolean(emailMeta?.ai?.configured)}
          showAppliedDate={section === "applied"}
        />
      ) : (
        <div className="job-grid">
          {filteredJobs.map((job) => (
            <JobCard
              key={job.job_id}
              job={job}
              onSetApplied={handleSetApplied}
              onRemove={handleRemoveJob}
              onScore={handleScoreJob}
              onGenerate={handleGenerateResume}
              onGenerateCover={handleGenerateCoverLetter}
              aiConfigured={Boolean(emailMeta?.ai?.configured)}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
