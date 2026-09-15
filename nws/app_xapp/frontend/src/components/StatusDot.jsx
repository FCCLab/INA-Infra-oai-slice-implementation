// components/StatusDot.jsx — status indicator that combines colour AND text
// Exposes (global + window): StatusDot
//
// The NeuroRAN standard requires status to be conveyed by colour + text, never
// colour alone. Always pass a label.
//   <StatusDot state="ok" label="live" />   -> green pulse + "live"
//   <StatusDot state="warn" label="cached" />
//   <StatusDot state="bad" label="error" />

const STATUS_DOT_CLASS = { ok: "dot-ok", warn: "dot-warn", bad: "dot-bad" };

function StatusDot({ state = "ok", label, title }) {
  const dotClass = STATUS_DOT_CLASS[state] || STATUS_DOT_CLASS.ok;
  return (
    <span className="status-line" title={title || label}>
      <span className={"status-dot " + dotClass} />
      {label && <span className="mono">{label}</span>}
    </span>
  );
}

Object.assign(window, { StatusDot });
