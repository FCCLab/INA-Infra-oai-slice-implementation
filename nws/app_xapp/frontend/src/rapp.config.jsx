// rapp.config.jsx — NeuroRAN Network Slicing xApp Manifest
// Exposes (window): RAPP_CONFIG, ICONS

const ICONS = {
  grid: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden="true">
      <rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" />
    </svg>
  ),
  table: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden="true">
      <rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 9h18M3 14h18M9 4v16" />
    </svg>
  ),
  gear: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82-.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  ),
};

const RAPP_CONFIG = {
  brand: {
    product: "NeuroRAN",
    shortName: "NWS-xApp",
    title: "NeuroRAN Network Slicing xApp",
    logo: "logo/NeuroRAN.png",
    logoPng: "logo/NeuroRAN.png",
  },

  site: {
    label: "Near-RT RIC",
    id: "nearrt-ric",
    clockTz: "Asia/Singapore",
  },

  theme: {
    accent: "Signal blue",
    dark: true,
    density: "comfy",
  },

  tabs: [
    { id: "prb",      label: "E2 PRB Allocations",  icon: ICONS.table, component: "PrbTab" },
    { id: "a1",       label: "Received A1 Policies", icon: ICONS.gear,  component: "A1PoliciesTab" },
    { id: "topology", label: "Topology & APIs",     icon: ICONS.grid,  component: "TopologyTab" },
  ],
};

Object.assign(window, { RAPP_CONFIG, ICONS });
