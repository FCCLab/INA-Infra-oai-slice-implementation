// theme.jsx — accent palettes + canvas color resolver (generic, reusable)
// Exposes (global + window): ACCENTS, buildColors
// Select via rapp.config.jsx theme.accent.
// Defaults: rApp → "Energy green", xApp → "Signal blue" (new-rapp --kind).

const ACCENTS = {
  "Energy green": { accent: "#16E6A0", accent2: "#00C2D4", light: { accent: "#2d8f5f", accent2: "#5cb85c" } },
  "Telco teal": { accent: "#15D6C6", accent2: "#2E8BFF" },
  "Signal blue": { accent: "#3E9BFF", accent2: "#7C5CFF" },
  "Volt": { accent: "#9BE635", accent2: "#15D6C6" },
};

const HIGHLIGHT_GOLD = "#e8b923";

function buildColors(dark, accentKey) {
  const base = ACCENTS[accentKey] || ACCENTS["Energy green"];
  const a = !dark && base.light ? base.light : base;
  return {
    accent: a.accent, accent2: a.accent2,
    accentGlow: dark ? a.accent + "88" : a.accent + "55",
    gold: HIGHLIGHT_GOLD,
    bad: dark ? "#FF5A6E" : "#E11D48",
    grid: dark ? "rgba(255,255,255,0.06)" : "rgba(45,90,60,0.08)",
    axis: dark ? "rgba(226,232,240,0.42)" : "rgba(90,114,96,0.65)",
    baseline: dark ? "rgba(148,163,184,0.7)" : "rgba(100,116,139,0.8)",
    nowLine: dark ? "rgba(226,232,240,0.35)" : "rgba(51,65,85,0.4)",
    track: dark ? "rgba(255,255,255,0.07)" : "rgba(45,90,60,0.1)",
    fillTop: a.accent + "33", fillBot: a.accent + "00",
    text: dark ? "#eef2f9" : "#1a2e1a",
  };
}

Object.assign(window, { ACCENTS, HIGHLIGHT_GOLD, buildColors });
