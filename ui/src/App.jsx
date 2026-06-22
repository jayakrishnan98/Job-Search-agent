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
import "./App.css";

const NewJobCelebration = lazy(() => import("./components/NewJobCelebration.jsx"));

const POLL_MS = 30000;
const META_POLL_MS = 60000;
const SEARCH_DEBOUNCE_MS = 250;

function parseDate(value) {
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? 0 : d.getTime();
}

function jobsFingerprint(jobs) {
  return jobs.map((job) => `${job.job_id}:${job.is_new ? 1 : 0}`).join("|");
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
  const [sortOrder, setSortOrder] = useState("newest");
  const [viewMode, setViewMode] = useState("cards");
  const [emailMeta, setEmailMeta] = useState(null);
  const [celebrate, setCelebrate] = useState(false);
  const [celebrateCount, setCelebrateCount] = useState(0);
  const [appPulse, setAppPulse] = useState(false);

  const prevNewJobIdsRef = useRef(null);
  const updatedAtRef = useRef(null);
  const jobsFingerprintRef = useRef("");

  useEffect(() => {
    const id = setTimeout(() => setDebouncedSearch(search), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [search]);

  const detectNewJobs = useCallback((nextJobs) => {
    const newJobIds = new Set(
      nextJobs.filter((job) => job.is_new).map((job) => job.job_id)
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
    const res = await fetch("/api/jobs");
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

      if (status.updated_at !== updatedAtRef.current) {
        await loadFullJobs();
        return;
      }

      setMeta((prev) => {
        if (!prev || prev.new_count === status.new_count) {
          return prev;
        }
        return { ...prev, new_count: status.new_count, total: status.total };
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

  useEffect(() => {
    const pollId = setInterval(() => {
      if (!document.hidden) {
        pollForChanges();
      }
    }, POLL_MS);

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
  }, [pollForChanges, loadMeta]);

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

  const filteredJobs = useMemo(() => {
    const q = debouncedSearch.toLowerCase().trim();

    let result = jobs.filter((job) => {
      if (companyFilter !== "all" && job.company !== companyFilter) {
        return false;
      }
      if (sourceFilter !== "all" && job.source !== sourceFilter) {
        return false;
      }
      if (!q) return true;
      const title = (job.title || "").toLowerCase();
      const company = (job.company || "").toLowerCase();
      return title.includes(q) || company.includes(q);
    });

    result = [...result].sort((a, b) => {
      const da = parseDate(a.posted_date);
      const db = parseDate(b.posted_date);
      return sortOrder === "newest" ? db - da : da - db;
    });

    return result;
  }, [jobs, debouncedSearch, companyFilter, sourceFilter, sortOrder]);

  const newCount = meta?.new_count ?? 0;

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
    setJobs((prev) => prev.map((job) => ({ ...job, is_new: false })));
    setMeta((prev) => (prev ? { ...prev, new_count: 0 } : prev));
    prevNewJobIdsRef.current = new Set();
    await loadMeta();
  }, [loadMeta]);

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
              Jobs from career sites and LinkedIn across your shortlisted companies
            </p>
          </div>
          <div className="header-actions">
            {newCount > 0 && (
              <button className="btn" onClick={handleMarkRead}>
                Mark all read
              </button>
            )}
            <button
              className="btn btn-primary"
              onClick={handleFetch}
              disabled={fetching}
            >
              {fetching && <span className="spinner" />}
              {fetching ? "Fetching…" : "Fetch jobs"}
            </button>
          </div>
        </div>

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
            value={sortOrder}
            onChange={(e) => setSortOrder(e.target.value)}
          >
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
          </select>
        </div>
      </header>

      {error && <div className="error-banner">{error}</div>}

      {(emailMeta?.email_config_issue || emailMeta?.email_status?.ok === false) && (
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

      <div className="stats-bar">
        <span>
          Showing <strong>{filteredJobs.length}</strong> of{" "}
          <strong>{jobs.length}</strong> jobs
        </span>
        {newCount > 0 && (
          <span className="new-count new-count-blink">
            <strong>{newCount}</strong> new
          </span>
        )}
        <span>
          Last updated: <strong>{formatUpdated(meta?.updated_at)}</strong>
        </span>
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

      {loading ? (
        <div className="loading">Loading jobs…</div>
      ) : filteredJobs.length === 0 ? (
        <div className="empty">
          {jobs.length === 0
            ? "No jobs yet. Click “Fetch jobs” to start."
            : "No jobs match your search or filter."}
        </div>
      ) : viewMode === "table" ? (
        <JobTable jobs={filteredJobs} />
      ) : (
        <div className="job-grid">
          {filteredJobs.map((job) => (
            <JobCard key={job.job_id} job={job} />
          ))}
        </div>
      )}
    </div>
  );
}
