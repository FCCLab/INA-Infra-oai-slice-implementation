// PrbTab.jsx — E2 PRB Slice Allocation & Control Board (NeuroRAN xApp)
// Exposes (window): PrbTab

const { useState, useEffect, useMemo, useCallback } = React;

function PrbTab({ state, colors, config }) {
  const [snapshot, setSnapshot] = useState({ slices: [], indications: 0 });
  const [edits, setEdits] = useState({});
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [toast, setToast] = useState(null);
  const [isApplying, setIsApplying] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState(null);

  const fetchSlices = useCallback(async () => {
    try {
      const res = await fetch("/api/v1/slices");
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to fetch slices");
      setSnapshot(data);
      setLastRefreshed(new Date().toLocaleTimeString());
    } catch (e) {
      setToast({ type: "err", text: `Fetch error: ${e.message}` });
    }
  }, []);

  useEffect(() => {
    fetchSlices();
    if (!autoRefresh) return;
    const id = setInterval(fetchSlices, 2000);
    return () => clearInterval(id);
  }, [fetchSlices, autoRefresh]);

  const slices = snapshot.slices || [];

  // Summary Metrics
  const summary = useMemo(() => {
    const totalDed = slices.reduce((acc, s) => acc + (Number(s.dedicated) || 0), 0);
    const totalMin = slices.reduce((acc, s) => acc + (Number(s.min) || 0), 0);
    const sharedPool = Math.max(0, 100 - totalDed);
    return { count: slices.length, totalDed, totalMin, sharedPool };
  }, [slices]);

  const getEdit = (s) => {
    const key = `${s.sst}|${s.sd}|${s.direction}`;
    return edits[key] || {
      dedicated: s.dedicated ?? 0,
      min: s.min ?? 0,
      max: s.max ?? 100,
    };
  };

  const updateEditField = (s, field, val) => {
    const key = `${s.sst}|${s.sd}|${s.direction}`;
    const current = getEdit(s);
    setEdits({
      ...edits,
      [key]: { ...current, [field]: Number(val) || 0 },
    });
  };

  const handleApplyRow = async (s) => {
    const edit = getEdit(s);
    setIsApplying(true);
    setToast({ type: "info", text: `Applying PRB quota to slice ${s.sd}...` });
    try {
      const payload = {
        sst: s.sst,
        sd: s.sd,
        direction: s.direction,
        dedicated: edit.dedicated,
        min: edit.min,
        max: edit.max,
      };
      const res = await fetch("/api/v1/slices", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to update slice PRB");
      setToast({ type: "ok", text: `Updated slice SST ${s.sst} / SD ${s.sd}: Dedicated ${edit.dedicated}%, Min ${edit.min}%, Max ${edit.max}%.` });
      fetchSlices();
    } catch (e) {
      setToast({ type: "err", text: `Update error: ${e.message}` });
    } finally {
      setIsApplying(false);
    }
  };

  const handleApplyAll = async () => {
    setIsApplying(true);
    setToast({ type: "info", text: "Applying all configured PRB quotas to E2 Node..." });
    try {
      const payload = slices.map((s) => {
        const e = getEdit(s);
        return {
          sst: s.sst,
          sd: s.sd,
          direction: s.direction,
          dedicated: e.dedicated,
          min: e.min,
          max: e.max,
        };
      });
      const res = await fetch("/api/v1/slices", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slices: payload }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to apply PRB configuration");
      setToast({ type: "ok", text: `Successfully applied PRB allocation to ${payload.length} slice(s) on E2 Node!` });
      fetchSlices();
    } catch (e) {
      setToast({ type: "err", text: `Apply All error: ${e.message}` });
    } finally {
      setIsApplying(false);
    }
  };

  const handleSyncFromLive = () => {
    const next = {};
    for (const s of slices) {
      const key = `${s.sst}|${s.sd}|${s.direction}`;
      next[key] = {
        dedicated: s.dedicated ?? 0,
        min: s.min ?? 0,
        max: s.max ?? 100,
      };
    }
    setEdits(next);
    setToast({ type: "ok", text: "Synced Set columns from live E2 values." });
  };

  return (
    <div className="tier" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {toast && (
        <div className={`toast ${toast.type}`}>
          <span>{toast.text}</span>
          <button type="button" className="btn btn-sm btn-secondary" onClick={() => setToast(null)}>Dismiss</button>
        </div>
      )}

      {/* KPI Metrics */}
      <div className="grid-4">
        <Card style={{ margin: 0 }}>
          <div style={{ fontSize: "11px", color: "var(--text-faint)", textTransform: "uppercase" }}>Active E2 Slices</div>
          <div style={{ fontSize: "24px", fontWeight: "700", fontFamily: "Space Grotesk", color: "var(--text)", marginTop: "4px" }}>
            {summary.count}
          </div>
          <div style={{ marginTop: "4px" }}>
            <span className="pill ok">Assuring SLAs</span>
          </div>
        </Card>

        <Card style={{ margin: 0 }}>
          <div style={{ fontSize: "11px", color: "var(--text-faint)", textTransform: "uppercase" }}>Dedicated PRB Quota</div>
          <div style={{ fontSize: "24px", fontWeight: "700", fontFamily: "Space Grotesk", color: "var(--accent)", marginTop: "4px" }}>
            {summary.totalDed}%
          </div>
          <div style={{ marginTop: "4px", fontSize: "11px", color: "var(--text-dim)" }}>
            Reserved capacity
          </div>
        </Card>

        <Card style={{ margin: 0 }}>
          <div style={{ fontSize: "11px", color: "var(--text-faint)", textTransform: "uppercase" }}>Shared PRB Pool</div>
          <div style={{ fontSize: "24px", fontWeight: "700", fontFamily: "Space Grotesk", color: "var(--text)", marginTop: "4px" }}>
            {summary.sharedPool}%
          </div>
          <div style={{ marginTop: "4px", fontSize: "11px", color: "var(--text-dim)" }}>
            Available for burst
          </div>
        </Card>

        <Card style={{ margin: 0 }}>
          <div style={{ fontSize: "11px", color: "var(--text-faint)", textTransform: "uppercase" }}>E2 Node Indication</div>
          <div style={{ fontSize: "24px", fontWeight: "700", fontFamily: "Space Grotesk", color: "var(--text)", marginTop: "4px" }}>
            {snapshot.indications || 0}
          </div>
          <div style={{ marginTop: "4px", fontSize: "11px", color: "var(--text-dim)" }}>
            Last sync: {lastRefreshed || "connecting..."}
          </div>
        </Card>
      </div>

      {/* PRB Capacity Distribution Meter */}
      <Card>
        <SectionLabel>PRB Resource Allocation Share (Total: 100%)</SectionLabel>
        <div style={{ marginTop: "12px" }}>
          <div style={{ display: "flex", height: "18px", borderRadius: "6px", overflow: "hidden", border: "1px solid var(--border)" }}>
            {slices.map((s, idx) => {
              const ded = Number(s.dedicated) || 0;
              const hue = (idx * 135) % 360;
              return (
                <div
                  key={idx}
                  style={{
                    width: `${ded}%`,
                    background: `hsl(${hue}, 70%, 50%)`,
                    transition: "width .3s ease",
                  }}
                  title={`Slice ${s.sd}: ${ded}% dedicated`}
                />
              );
            })}
            <div
              style={{
                width: `${summary.sharedPool}%`,
                background: "var(--surface-2)",
                transition: "width .3s ease",
              }}
              title={`Shared Pool: ${summary.sharedPool}%`}
            />
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", gap: "14px", marginTop: "10px", fontSize: "11px" }}>
            {slices.map((s, idx) => {
              const hue = (idx * 135) % 360;
              return (
                <div key={idx} style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                  <span style={{ width: "10px", height: "10px", borderRadius: "2px", background: `hsl(${hue}, 70%, 50%)` }} />
                  <span style={{ color: "var(--text)" }}>Slice {s.sd}: <strong>{s.dedicated}%</strong></span>
                </div>
              );
            })}
            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <span style={{ width: "10px", height: "10px", borderRadius: "2px", background: "var(--surface-2)", border: "1px solid var(--border)" }} />
              <span style={{ color: "var(--text-dim)" }}>Shared Pool: <strong>{summary.sharedPool}%</strong></span>
            </div>
          </div>
        </div>
      </Card>

      {/* Main Allocation Table Card */}
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", flexWrap: "wrap", gap: "8px" }}>
          <SectionLabel>E2 PRB Quota Enforcement (Near-RT Controller)</SectionLabel>
          <div className="toolbar">
            <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--text-dim)", cursor: "pointer", marginRight: "8px" }}>
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
              />
              Auto-refresh (2s)
            </label>
            <button type="button" className="btn btn-sm btn-secondary" onClick={handleSyncFromLive}>
              Sync Set from Live
            </button>
            <button
              type="button"
              className="btn btn-sm btn-primary"
              disabled={isApplying || slices.length === 0}
              onClick={handleApplyAll}
            >
              {isApplying ? "Applying..." : "Apply All to E2"}
            </button>
          </div>
        </div>

        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Slice Identifier</th>
                <th>Direction</th>
                <th style={{ textAlign: "right" }}>Live Dedicated %</th>
                <th style={{ textAlign: "right" }}>Live Min %</th>
                <th style={{ textAlign: "right" }}>Live Max %</th>
                <th style={{ textAlign: "right", width: "100px" }}>Set Dedicated</th>
                <th style={{ textAlign: "right", width: "100px" }}>Set Min</th>
                <th style={{ textAlign: "right", width: "100px" }}>Set Max</th>
                <th style={{ textAlign: "center", width: "90px" }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {slices.length === 0 ? (
                <tr>
                  <td colSpan="9" style={{ textAlign: "center", color: "var(--text-dim)", padding: "16px" }}>
                    No slice entries registered in xApp. Send an A1 policy from rApp or create a slice.
                  </td>
                </tr>
              ) : (
                slices.map((s, idx) => {
                  const e = getEdit(s);
                  const isModified =
                    e.dedicated !== (s.dedicated ?? 0) ||
                    e.min !== (s.min ?? 0) ||
                    e.max !== (s.max ?? 100);

                  return (
                    <tr key={idx} style={{ background: isModified ? "color-mix(in srgb, var(--accent) 3%, transparent)" : undefined }}>
                      <td>
                        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                          <span className="mono" style={{ fontWeight: "600", color: "var(--accent)" }}>
                            SST {s.sst} / SD {s.sd}
                          </span>
                          <span className="pill ok">Active</span>
                        </div>
                      </td>
                      <td>
                        <span className="pill">{String(s.direction || "dl").toUpperCase()}</span>
                      </td>
                      <td className="mono" style={{ textAlign: "right", color: "var(--text)" }}>
                        {s.dedicated != null ? `${Number(s.dedicated).toFixed(1)}%` : "—"}
                      </td>
                      <td className="mono" style={{ textAlign: "right", color: "var(--text)" }}>
                        {s.min != null ? `${Number(s.min).toFixed(1)}%` : "—"}
                      </td>
                      <td className="mono" style={{ textAlign: "right", color: "var(--text)" }}>
                        {s.max != null ? `${Number(s.max).toFixed(1)}%` : "—"}
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <input
                          className="mono"
                          type="number"
                          step="5"
                          min="0"
                          max="100"
                          style={{ width: "70px", textAlign: "right", padding: "4px 6px" }}
                          value={e.dedicated}
                          onChange={(ev) => updateEditField(s, "dedicated", ev.target.value)}
                        />
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <input
                          className="mono"
                          type="number"
                          step="5"
                          min="0"
                          max="100"
                          style={{ width: "70px", textAlign: "right", padding: "4px 6px" }}
                          value={e.min}
                          onChange={(ev) => updateEditField(s, "min", ev.target.value)}
                        />
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <input
                          className="mono"
                          type="number"
                          step="5"
                          min="0"
                          max="100"
                          style={{ width: "70px", textAlign: "right", padding: "4px 6px" }}
                          value={e.max}
                          onChange={(ev) => updateEditField(s, "max", ev.target.value)}
                        />
                      </td>
                      <td style={{ textAlign: "center" }}>
                        <button
                          type="button"
                          className="btn btn-sm btn-primary"
                          disabled={isApplying}
                          onClick={() => handleApplyRow(s)}
                        >
                          Apply
                        </button>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

Object.assign(window, { PrbTab });
