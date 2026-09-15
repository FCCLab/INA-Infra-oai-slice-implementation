// PoliciesTab.jsx — Active & Saved A1 Policies + Activation History
// Exposes (window): PoliciesTab

const { useState, useEffect, useCallback } = React;

function PoliciesTab({ state, colors, config, actions }) {
  const [xappPolicies, setXappPolicies] = useState([]);
  const [savedPolicies, setSavedPolicies] = useState([]);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState(null);

  const fetchAll = useCallback(async () => {
    try {
      const [resSla, resSaved, resHist] = await Promise.all([
        fetch("/api/v1/xapp/slice-sla").then((r) => r.json()).catch(() => ({ policies: [] })),
        fetch("/api/v1/policies").then((r) => r.json()).catch(() => ({ policies: [] })),
        fetch("/api/v1/history").then((r) => r.json()).catch(() => ({ history: [] })),
      ]);
      setXappPolicies(resSla.policies || []);
      setSavedPolicies(resSaved.policies || []);
      setHistory(resHist.history || []);
    } catch (e) {
      setToast({ type: "err", text: "Failed to refresh policies: " + e.message });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const timer = setInterval(fetchAll, 3000);
    return () => clearInterval(timer);
  }, [fetchAll]);

  const handleApplySaved = async (id, policy) => {
    setToast({ type: "info", text: `Applying policy ${id}...` });
    try {
      const res = await fetch(`/api/v1/policies/${id}/activate`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Activation failed");
      setToast({ type: "ok", text: `Activated ${id} successfully!` });
      fetchAll();
    } catch (e) {
      setToast({ type: "err", text: e.message });
    }
  };

  const handleDeleteSaved = async (id) => {
    if (!confirm(`Delete policy ${id} from rApp storage?`)) return;
    try {
      const res = await fetch(`/api/v1/policies/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Delete failed");
      setToast({ type: "ok", text: `Deleted ${id}` });
      fetchAll();
    } catch (e) {
      setToast({ type: "err", text: e.message });
    }
  };

  return (
    <div className="tier" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {toast && (
        <div className={`toast ${toast.type}`}>
          <span>{toast.text}</span>
          <button type="button" className="btn btn-sm btn-secondary" onClick={() => setToast(null)}>Dismiss</button>
        </div>
      )}

      {/* Active Policies on xApp */}
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
          <SectionLabel>Active A1 Slice SLA Policies on Near-RT xApp</SectionLabel>
          <button type="button" className="btn btn-sm btn-secondary" onClick={fetchAll}>Refresh Live</button>
        </div>

        {xappPolicies.length === 0 ? (
          <p style={{ color: "var(--text-dim)", fontSize: "13px" }}>No active A1 policies recorded on Near-RT xApp.</p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            {xappPolicies.map((p, idx) => {
              const scope = p.policy?.scope?.sliceId || {};
              const objs = p.policy?.sliceSlaObjectives || {};
              const plmn = scope.plmnId ? `${scope.plmnId.mcc}/${scope.plmnId.mnc}` : "—";
              return (
                <div key={p.key || idx} className="sla-card">
                  <div className="sla-card-head">
                    <div className="sla-card-title">
                      <span>Slice: SST {scope.sst ?? "—"} / SD {scope.sd ?? "—"}</span>
                      <span className="pill ok">PLMN {plmn}</span>
                      <span className="pill ok">A1 Target Active</span>
                    </div>
                    <span style={{ fontSize: "11px", color: "var(--text-faint)" }}>
                      Received: {p.received_at ? new Date(p.received_at * 1000).toLocaleTimeString() : "active"}
                    </span>
                  </div>
                  <div className="sla-metrics">
                    {objs.guaDlThptPerSlice != null && (
                      <div className="sla-metric-box">
                        <div className="sla-metric-lbl">Gua DL (kbps)</div>
                        <div className="sla-metric-val">{Number(objs.guaDlThptPerSlice).toLocaleString()}</div>
                      </div>
                    )}
                    {objs.maxDlThptPerSlice != null && (
                      <div className="sla-metric-box">
                        <div className="sla-metric-lbl">Max DL (kbps)</div>
                        <div className="sla-metric-val">{Number(objs.maxDlThptPerSlice).toLocaleString()}</div>
                      </div>
                    )}
                    {objs.guaUlThptPerSlice != null && (
                      <div className="sla-metric-box">
                        <div className="sla-metric-lbl">Gua UL (kbps)</div>
                        <div className="sla-metric-val">{Number(objs.guaUlThptPerSlice).toLocaleString()}</div>
                      </div>
                    )}
                    {objs.maxUlThptPerSlice != null && (
                      <div className="sla-metric-box">
                        <div className="sla-metric-lbl">Max UL (kbps)</div>
                        <div className="sla-metric-val">{Number(objs.maxUlThptPerSlice).toLocaleString()}</div>
                      </div>
                    )}
                    {objs.maxNumberOfUes != null && (
                      <div className="sla-metric-box">
                        <div className="sla-metric-lbl">Max UEs</div>
                        <div className="sla-metric-val">{objs.maxNumberOfUes}</div>
                      </div>
                    )}
                    {objs.maxDlPacketDelayPerUe != null && (
                      <div className="sla-metric-box">
                        <div className="sla-metric-lbl">Max Delay (ms)</div>
                        <div className="sla-metric-val">{objs.maxDlPacketDelayPerUe}</div>
                      </div>
                    )}
                  </div>
                  <details>
                    <summary style={{ fontSize: "11px", color: "var(--accent)", cursor: "pointer" }}>View Full Policy JSON</summary>
                    <pre className="json-box" style={{ marginTop: "6px" }}>{JSON.stringify(p.policy, null, 2)}</pre>
                  </details>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      {/* Saved Policies on rApp */}
      <Card>
        <SectionLabel>Saved Policies Library (Non-RT rApp Storage)</SectionLabel>
        <div className="table-wrap" style={{ marginTop: "10px" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Policy ID</th>
                <th>Name / Label</th>
                <th>Target Slice</th>
                <th style={{ textAlign: "right" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {savedPolicies.length === 0 ? (
                <tr>
                  <td colSpan="4" style={{ textAlign: "center", color: "var(--text-dim)", padding: "16px" }}>
                    No saved policies in repository. Compose and click "Save on rApp" to store policies.
                  </td>
                </tr>
              ) : (
                savedPolicies.map((sp) => {
                  const s = sp.policy?.scope?.sliceId || {};
                  return (
                    <tr key={sp.id}>
                      <td className="mono" style={{ color: "var(--accent)" }}>{sp.id}</td>
                      <td>{sp.name || "Custom SLA Policy"}</td>
                      <td>SST {s.sst ?? "—"} / SD {s.sd ?? "—"}</td>
                      <td style={{ textAlign: "right" }}>
                        <button
                          type="button"
                          className="btn btn-sm btn-primary"
                          style={{ marginRight: "6px" }}
                          onClick={() => handleApplySaved(sp.id, sp.policy)}
                        >
                          Apply
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-danger"
                          onClick={() => handleDeleteSaved(sp.id)}
                        >
                          Delete
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

      {/* Activation History Log */}
      <Card>
        <SectionLabel>Recent A1 Policy Activation History</SectionLabel>
        <div className="table-wrap" style={{ marginTop: "10px" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Timestamp (UTC)</th>
                <th>Source</th>
                <th>Target Slice</th>
                <th>PMS Status</th>
                <th>xApp Status</th>
              </tr>
            </thead>
            <tbody>
              {history.length === 0 ? (
                <tr>
                  <td colSpan="5" style={{ textAlign: "center", color: "var(--text-dim)", padding: "16px" }}>
                    No recent activation history.
                  </td>
                </tr>
              ) : (
                history.slice(0, 10).map((h, i) => {
                  const s = h.policy?.scope?.sliceId || {};
                  return (
                    <tr key={i}>
                      <td className="mono" style={{ whiteSpace: "nowrap" }}>{h.at || "—"}</td>
                      <td><span className="pill">{h.source || "adhoc"}</span></td>
                      <td>SST {s.sst ?? "—"} / SD {s.sd ?? "—"}</td>
                      <td>
                        {h.pms_error ? (
                          <span className="pill bad" title={h.pms_error}>PMS Error</span>
                        ) : (
                          <span className="pill ok">200 OK (PMS)</span>
                        )}
                      </td>
                      <td>
                        <span className="pill ok">Stored on xApp</span>
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

Object.assign(window, { PoliciesTab });
