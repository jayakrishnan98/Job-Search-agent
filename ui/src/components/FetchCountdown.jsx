import { useEffect, useState } from "react";

function formatCountdown(totalSeconds) {
  if (totalSeconds == null) return "—";
  const mins = Math.floor(totalSeconds / 60);
  const secs = totalSeconds % 60;
  return `${mins}:${String(secs).padStart(2, "0")}`;
}

export default function FetchCountdown({ nextFetchAt, pollIntervalMinutes, running }) {
  const [secondsLeft, setSecondsLeft] = useState(null);

  useEffect(() => {
    function tick() {
      if (running) {
        setSecondsLeft(null);
        return;
      }
      if (nextFetchAt) {
        const diff = Math.max(
          0,
          Math.floor((new Date(nextFetchAt).getTime() - Date.now()) / 1000)
        );
        setSecondsLeft(diff);
        return;
      }
      if (pollIntervalMinutes) {
        setSecondsLeft(pollIntervalMinutes * 60);
      }
    }

    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [nextFetchAt, pollIntervalMinutes, running]);

  if (running) {
    return (
      <span className="fetch-countdown">
        Next fetch: <strong className="fetch-countdown-live">fetching…</strong>
      </span>
    );
  }

  return (
    <span className="fetch-countdown">
      Next fetch: <strong className="fetch-countdown-live">{formatCountdown(secondsLeft)}</strong>
    </span>
  );
}
