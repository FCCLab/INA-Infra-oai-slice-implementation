// components/KpiStrip.jsx — config-driven KPI cards (items from config.kpis(state))
// Exposes (global + window): KpiStrip, KPI_ICONS
//
// Usage: <KpiStrip items={config.kpis(state)} />
// Each item: { label, kicker?, value, unit?, icon?, bad? }
//   icon: a key from KPI_ICONS, or an inline <svg/> element.
// Column count auto-adapts (2-5) to the number of items via the data-cols attr.

const KPI_ICONS = {
  pct: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M19 5 5 19M6.5 6.5h.01M17.5 17.5h.01" strokeLinecap="round" /></svg>,
  bolt: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round"><path d="M13 2 4 14h6l-1 8 9-12h-6z" /></svg>,
  gauge: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M22 12a10 10 0 1 0-20 0" /><path d="M12 12l4-3" strokeLinecap="round" /></svg>,
  users: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" /></svg>,
  check: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" /><polyline points="22 4 12 14.01 9 11.01" /></svg>,
  grid: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /></svg>,
  clock: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" strokeLinecap="round" /></svg>,
  signal: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M4 20v-4M10 20v-9M16 20v-14M22 20V9" /></svg>,
};

function KpiStrip({ items }) {
  const list = Array.isArray(items) ? items : [];
  const cols = Math.max(2, Math.min(5, list.length || 5));
  return (
    <div className="kpi-strip" data-cols={cols}>
      {list.map((it, i) => {
        const icon = typeof it.icon === "string" ? KPI_ICONS[it.icon] : it.icon;
        return (
          <div key={it.label || i} className={"kpi-card" + (it.bad ? " kpi-bad" : "")}>
            {icon && <span className="kpi-icon">{icon}</span>}
            <div className="kpi-label">
              {it.label}
              {it.kicker && <span className="kpi-kicker">{it.kicker}</span>}
            </div>
            <div className="kpi-value">{it.value}{it.unit && <span className="kpi-unit">{it.unit}</span>}</div>
          </div>
        );
      })}
    </div>
  );
}

Object.assign(window, { KpiStrip, KPI_ICONS });
