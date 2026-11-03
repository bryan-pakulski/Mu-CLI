// Compact readouts for a turn's or a session's footprint: time, tokens and cost.

export function formatSeconds(seconds: number): string {
  if (!(seconds > 0)) return '';
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function formatTokenCount(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

export function formatUsd(v: number): string {
  return v >= 1 ? `$${v.toFixed(2)}` : `$${v.toFixed(v >= 0.01 ? 3 : 4)}`;
}
