// lib/format.jsx — time + number formatters (pure, no model state)
// Exposes (global + window): fmtClock, fmtClockShort, fmtSiteNow, fmtNumber, fmtSI

function fmtClock(hourFloat) {
  const h = ((hourFloat % 24) + 24) % 24;
  const hh = Math.floor(h), mm = Math.floor((h - hh) * 60), ss = Math.floor(((h - hh) * 60 - mm) * 60);
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

function fmtClockShort(hourFloat) {
  const h = ((hourFloat % 24) + 24) % 24;
  const hh = Math.floor(h), mm = Math.floor((h - hh) * 60);
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
}

// Local wall-clock time in a given IANA timezone (defaults to Asia/Singapore).
function fmtSiteNow(tz = "Asia/Singapore", date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: tz, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).formatToParts(date);
  const pick = (type) => parts.find((p) => p.type === type)?.value || "00";
  return `${pick("hour")}:${pick("minute")}:${pick("second")}`;
}

function fmtNumber(n, digits = 0) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "-";
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

// SI-ish magnitude formatter: 1500 -> { v: "1.50", u: "k" }
function fmtSI(n, baseUnit = "") {
  const v = Number(n) || 0;
  if (Math.abs(v) >= 1e9) return { v: (v / 1e9).toFixed(2), u: "G" + baseUnit };
  if (Math.abs(v) >= 1e6) return { v: (v / 1e6).toFixed(2), u: "M" + baseUnit };
  if (Math.abs(v) >= 1e3) return { v: (v / 1e3).toFixed(2), u: "k" + baseUnit };
  return { v: Math.round(v).toString(), u: baseUnit };
}

Object.assign(window, { fmtClock, fmtClockShort, fmtSiteNow, fmtNumber, fmtSI });
