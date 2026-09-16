// PoliciesTab.jsx — Active A1-PMS policies + Saved library + Activation History
// Exposes (window): PoliciesTab

const { useState, useEffect, useCallback } = React;

function enforcePill(status) {
  const st = (status && (status.enforceStatus || status.status?.enforceStatus)) || "";
  const reason = (status && (status.enforceReason || status.status?.enforceReason)) || "";
  if (st === "ENFORCED") return { cls: "ok", text: "ENFORCED" };
  if (st === "NOT_ENFORCED") return { cls: "warn", text: reason ? `NOT_ENFORCED (${reason})` : "NOT_ENFORCED" };
  if (st) return { cls: "bad", text: st };
  return { cls: "", text: "status unknown" };
}

function slaMetricBoxes(objs) {
  if (!objs) return null;
  const rows = [
    ["Gua DL (kbps)", objs.guaDlThptPerSlice],
    ["Max DL (kbps)", objs.maxDlThptPerSlice],
    ["Gua UL (kbps)", objs.guaUlThptPerSlice],
    ["Max UL (kbps)", objs.maxUlThptPerSlice],
    ["Max UEs", objs.maxNumberOfUes],
    ["Max Delay (ms)", objs.maxDlPacketDelayPerUe],
  ];
  const shown = rows.filter(([, v]) => v != null);
  if (!shown.length) return null;
  return (
    <div className="sla-metrics">
      {shown.map(([lbl, v]) => (
        <div key={lbl} className="sla-metric-box">
          <div className="sla-metric-lbl">{lbl}</div>
          <div className="sla-metric-val">{typeof v === "number" ? Number(v).toLocaleString() : v}</div>
        </div>
      ))}
    </div>
  );
}

function PoliciesTab({ state, colors, config, actions }) {
  const [activePolicies, setActivePolicies] = useState([]);
  const [activeMeta, setActiveMeta] = useState({ ric_id: "", policytype_id: "", error: null, ok: true });
  const [savedPolicies, setSavedPolicies] = useState([]);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState(null);
  const [toast, setToast] = useState(null);

  const fetchAll = useCallback(async () => {
    try {
      const [resActive, resSaved, resHist] = await Promise.all([
        fetch("/api/v1/a1/policies").then((r) => r.json()).catch(() => ({ policies: [], ok: false, error: "fetch failed" })),
        fetch("/api/v1/policies").then((r) => r.json()).catch(() => ({ policies: [] })),
        fetch("/api/v1/history").then((r) => r.json()).catch(() => ({ history: [] })),
      ]);
      setActivePolicies(resActive.policies || []);
      setActiveMeta({
        ric_id: resActive.ric_id || "",
        policytype_id: resActive.policytype_id || "",
        error: resActive.error || null,
        ok: resActive.ok !== false,
      });
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

  const handleApplySaved = async (id) => {
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

  const handleDeleteActive = async (id) => {
    if (!id) return;
    if (!confirm(`Delete A1 policy ${id} from A1-PMS and Near-RT RIC?`)) return;
    setDeletingId(id);
    setToast({ type: "info", text: `Deleting ${id}...` });
    try {
      const res = await fetch(`/api/v1/a1/policies/${encodeURIComponent(id)}`, { method: "DELETE" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Delete failed");
      setActivePolicies((cur) => cur.filter((p) => p.policy_id !== id));
      const note = data.xapp_error ? ` (xApp cache: ${data.xapp_error})` : "";
      setToast({ type: "ok", text: `Deleted ${id} from A1-PMS${note}` });
      fetchAll();
    } catch (e) {
      setToast({ type: "err", text: e.message });
    } finally {
      setDeletingId(null);
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

      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", gap: "12px", flexWrap: "wrap" }}>
          <div>
            <SectionLabel>Active A1 Policies (A1-PMS)</SectionLabel>
            <div style={{ fontSize: "11px", color: "var(--text-faint)", marginTop: "4px" }}>
              RIC {activeMeta.ric_id || "—"} · type {activeMeta.policytype_id || "—"} · {activePolicies.length} instance{activePolicies.length === 1 ? "" : "s"}
            </div>
          </div>
          <button type="button" className="btn btn-sm btn-secondary" onClick={fetchAll}>Refresh Live</button>
        </div>

        {activeMeta.error && (
          <p style={{ color: "var(--bad)", fontSize: "13px" }}>A1-PMS error: {activeMeta.error}</p>
        )}

        {!loading && activePolicies.length === 0 && !activeMeta.error ? (
          <p style={{ color: "var(--text-dim)", fontSize: "13px" }}>No A1 policies installed on this Near-RT RIC.</p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            {activePolicies.map((p, idx) => {
              const scope = p.policy?.scope?.sliceId || {};
              const objs = p.policy?.sliceSlaObjectives || {};
              const plmn = scope.plmnId ? `${scope.plmnId.mcc}/${scope.plmnId.mnc}` : "—";
              const enf = enforcePill(p.status);
              return (
                <div key={p.policy_id || idx} className="sla-card">
                  <div className="sla-card-head">
                    <div className="sla-card-title">
                      <span className="mono">{p.policy_id || "—"}</span>
                      <span>SST {scope.sst ?? "—"} / SD {scope.sd ?? "—"}</span>
                      <span className="pill ok">PLMN {plmn}</span>
                      <span className={`pill ${enf.cls}`} title={JSON.stringify(p.status || {})}>{enf.text}</span>
                      <span className={`pill ${p.on_xapp ? "ok" : "warn"}`}>
                        {p.on_xapp ? "on xApp" : "not on xApp"}
                      </span>
                    </div>
                    <button
                      type="button"
                      className="btn btn-sm btn-danger"
                      disabled={deletingId === p.policy_id}
                      onClick={() => handleDeleteActive(p.policy_id)}
                    >
                      {deletingId === p.policy_id ? "Deleting…" : "Delete"}
                    </button>
                  </div>
                  {slaMetricBoxes(objs)}
                  <details>
                    <summary style={{ fontSize: "11px", color: "var(--accent)", cursor: "pointer" }}>View Full Policy JSON</summary>
                    <pre className="json-box" style={{ marginTop: "6px" }}>{JSON.stringify({ policy_id: p.policy_id, status: p.status, policy: p.policy }, null, 2)}</pre>
                  </details>
                </div>
              );
            })}
          </div>
        )}
      </Card>

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
                          onClick={() => handleApplySaved(sp.id)}
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

      <Card>
        <SectionLabel>Recent A1 Policy Activation History</SectionLabel>
        <div className="table-wrap" style={{ marginTop: "10px" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Timestamp (UTC)</th>
                <th>Policy ID</th>
                <th>Source</th>
                <th>Target Slice</th>
                <th>PMS Status</th>
                <th>xApp Status</th>
              </tr>
            </thead>
            <tbody>
              {history.length === 0 ? (
                <tr>
                  <td colSpan="6" style={{ textAlign: "center", color: "var(--text-dim)", padding: "16px" }}>
                    No recent activation history.
                  </td>
                </tr>
              ) : (
                history.slice(0, 10).map((h, i) => {
                  const s = h.policy?.scope?.sliceId || {};
                  return (
                    <tr key={i}>
                      <td className="mono" style={{ whiteSpace: "nowrap" }}>{h.at || "—"}</td>
                      <td className="mono" style={{ color: "var(--accent)" }}>{h.policy_id || "—"}</td>
                      <td><span className="pill">{h.source || "adhoc"}</span></td>
                      <td>SST {s.sst ?? "—"} / SD {s.sd ?? "—"}</td>
                      <td>
                        {h.pms_error ? (
                          <span className="pill bad" title={h.pms_error}>PMS Error</span>
                        ) : (
                          <span className="pill ok">{h.action === "delete" ? "Deleted (PMS)" : "200 OK (PMS)"}</span>
                        )}
                      </td>
                      <td>
                        {h.action === "delete" ? (
                          <span className={`pill ${h.xapp_error ? "warn" : "ok"}`}>
                            {h.xapp_error ? "xApp cache leftover" : "Removed on xApp"}
                          </span>
                        ) : (
                          <span className="pill ok">Stored on xApp</span>
                        )}
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
