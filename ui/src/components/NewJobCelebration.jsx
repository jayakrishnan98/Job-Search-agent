import { useEffect } from "react";

const COLORS = ["#ff6b6b", "#ffd93d", "#6bcb77", "#4d96ff", "#ff922b", "#cc5de8", "#f06595"];

function buildParticles(count) {
  return Array.from({ length: count }, (_, i) => ({
    id: i,
    left: `${8 + Math.random() * 84}%`,
    delay: `${Math.random() * 0.4}s`,
    duration: `${1.8 + Math.random() * 1.2}s`,
    color: COLORS[i % COLORS.length],
    size: 6 + Math.floor(Math.random() * 6),
    drift: `${-40 + Math.random() * 80}px`,
  }));
}

const particles = buildParticles(48);

export default function NewJobCelebration({ count, onDone }) {
  useEffect(() => {
    const id = setTimeout(onDone, 5000);
    return () => clearTimeout(id);
  }, [onDone]);

  const label = count === 1 ? "1 new job!" : `${count} new jobs!`;

  return (
    <div className="celebration-overlay" role="status" aria-live="assertive">
      <div className="celebration-flash" />
      <div className="celebration-burst">
        <span className="celebration-emoji">🎉</span>
        <strong>{label}</strong>
      </div>
      <div className="celebration-confetti" aria-hidden="true">
        {particles.map((p) => (
          <span
            key={p.id}
            className="confetti-piece"
            style={{
              left: p.left,
              animationDelay: p.delay,
              animationDuration: p.duration,
              backgroundColor: p.color,
              width: p.size,
              height: p.size,
              "--drift": p.drift,
            }}
          />
        ))}
      </div>
    </div>
  );
}
