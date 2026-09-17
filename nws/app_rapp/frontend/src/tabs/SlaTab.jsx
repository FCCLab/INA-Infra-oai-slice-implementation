// SlaTab.jsx — A1 Slice SLA Editor & Enforcer (NeuroRAN rApp)
// Exposes (window): SlaTab

const { useState, useEffect, useCallback } = React;

const PRESET_EXAMPLES = [
  {
    id: "embb-high-throughput",
    name: "eMBB High Throughput",
    badge: "5G eMBB",
    description: "Sustained DL throughput SLA for enhanced mobile broadband streaming & downloading.",
    objectives: {
      guaDlThptPerSlice: 100000,
      maxDlThptPerSlice: 300000,
      guaUlThptPerSlice: 50000,
      maxUlThptPerSlice: 150000,
      dlSlicePriority: 10,
      ulSlicePriority: 15,
    },
  },
  {
    id: "urllc-low-latency",
    name: "URLLC Low Latency",
    badge: "Ultra-Reliable",
    description: "Strict bounded latency <= 10ms with 99% reliability assurance for mission critical services.",
    objectives: {
      maxDlPacketDelayPerUe: 10,
      maxUlPacketDelayPerUe: 10,
      maxDlPdcpSduPacketLossRatePerUe: 0.01,
      maxUlRlcSduPacketLossRatePerUe: 0.01,
      maxDlJitterPerUe: 2.5,
      maxUlJitterPerUe: 2.5,
    },
    relDl: { packetSize: 200, userPlaneLatency: 10, successProbability: 0.99 },
    relUl: { packetSize: 200, userPlaneLatency: 10, successProbability: 0.99 },
  },
  {
    id: "miot-massive-ues",
    name: "MIoT Massive Connections",
    badge: "Massive IoT",
    description: "SLA capacity for thousands of concurrent IoT sensors and PDU sessions.",
    objectives: {
      maxNumberOfUes: 500,
      maxNumberOfPduSessions: 2000,
    },
  },
];

function sliceSlaPolicyId(sd) {
  const hex = String(sd || "").trim().replace(/^0x/i, "");
  if (!/^[0-9A-Fa-f]{1,6}$/.test(hex)) return "";
  const n = parseInt(hex, 16);
  if (!Number.isFinite(n) || n <= 0) return "";
  return `slice-sla-${n}`;
}

function numOrEmpty(v) {
  return v == null || v === "" ? "" : String(v);
}

function SlaTab({ state, colors, config, actions }) {
  // Scope State — Open5GS S-NSSAI: SST 1 / SD 000001..000005
  const [sst, setSst] = useState("1");
  const [sd, setSd] = useState("000001");
  const [mcc, setMcc] = useState("001");
  const [mnc, setMnc] = useState("01");
  const [policyId, setPolicyId] = useState(() => sliceSlaPolicyId("000001"));
  const [policyIdDirty, setPolicyIdDirty] = useState(false);

  // Objectives State
  const [guaDl, setGuaDl] = useState("50000");
  const [maxDl, setMaxDl] = useState("100000");
  const [guaUl, setGuaUl] = useState("");
  const [maxUl, setMaxUl] = useState("");
  const [maxUes, setMaxUes] = useState("");
  const [maxPdu, setMaxPdu] = useState("");
  const [dlDelay, setDlDelay] = useState("");
  const [ulDelay, setUlDelay] = useState("");
  const [dlLoss, setDlLoss] = useState("");
  const [ulLoss, setUlLoss] = useState("");
  const [dlPri, setDlPri] = useState("");
  const [ulPri, setUlPri] = useState("");
  const [cellNcis, setCellNcis] = useState("");

  // Tracking
  const [activeExamples, setActiveExamples] = useState(new Set());
  const [jsonText, setJsonText] = useState("");
  const [jsonDirty, setJsonDirty] = useState(false);
  const [toast, setToast] = useState(null);
  const [isApplying, setIsApplying] = useState(false);
  const [isValidating, setIsValidating] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [editingId, setEditingId] = useState(null);

  // Active A1 policies (first-page live list)
  const [activePolicies, setActivePolicies] = useState([]);
  const [activeMeta, setActiveMeta] = useState({ ric_id: "", policytype_id: "", error: null, ok: true });
  const [deletingId, setDeletingId] = useState(null);

  const fetchActive = useCallback(async () => {
    try {
      const res = await fetch("/api/v1/a1/policies");
      const data = await res.json();
      setActivePolicies(data.policies || []);
      setActiveMeta({
        ric_id: data.ric_id || "",
        policytype_id: data.policytype_id || "",
        error: data.error || null,
        ok: data.ok !== false,
      });
    } catch (e) {
      setActiveMeta((m) => ({ ...m, ok: false, error: e.message }));
    }
  }, []);

  useEffect(() => {
    fetchActive();
    const timer = setInterval(fetchActive, 4000);
    return () => clearInterval(timer);
  }, [fetchActive]);

  // Construct Policy Object from Form
  const buildPolicy = useCallback(() => {
    const scope = {
      sliceId: {
        sst: Number(sst) || 1,
        plmnId: { mcc: String(mcc).trim() || "001", mnc: String(mnc).trim() || "01" },
      },
    };
    if (sd && sd.trim()) scope.sliceId.sd = sd.trim();

    const obj = {};
    if (guaDl !== "") obj.guaDlThptPerSlice = Number(guaDl);
    if (maxDl !== "") obj.maxDlThptPerSlice = Number(maxDl);
    if (guaUl !== "") obj.guaUlThptPerSlice = Number(guaUl);
    if (maxUl !== "") obj.maxUlThptPerSlice = Number(maxUl);
    if (maxUes !== "") obj.maxNumberOfUes = Number(maxUes);
    if (maxPdu !== "") obj.maxNumberOfPduSessions = Number(maxPdu);
    if (dlDelay !== "") obj.maxDlPacketDelayPerUe = Number(dlDelay);
    if (ulDelay !== "") obj.maxUlPacketDelayPerUe = Number(ulDelay);
    if (dlLoss !== "") obj.maxDlPdcpSduPacketLossRatePerUe = Number(dlLoss);
    if (ulLoss !== "") obj.maxUlRlcSduPacketLossRatePerUe = Number(ulLoss);
    if (dlPri !== "") obj.dlSlicePriority = Number(dlPri);
    if (ulPri !== "") obj.ulSlicePriority = Number(ulPri);

    const policy = { scope, sliceSlaObjectives: obj };
    if (cellNcis && cellNcis.trim()) {
      const ncis = cellNcis.split(",").map((s) => s.trim()).filter(Boolean);
      if (ncis.length) {
        policy.sliceSlaResources = {
          cellIdList: ncis.map((n) => ({
            plmnId: { ...scope.sliceId.plmnId },
            cId: { ncI: Number(n) },
          })),
        };
      }
    }
    return policy;
  }, [sst, sd, mcc, mnc, guaDl, maxDl, guaUl, maxUl, maxUes, maxPdu, dlDelay, ulDelay, dlLoss, ulLoss, dlPri, ulPri, cellNcis]);

  const resolvedPolicyId = (policyId || "").trim() || sliceSlaPolicyId(sd);

  const wrapPayload = (policy) => {
    const payload = { policy };
    if (resolvedPolicyId) payload.id = resolvedPolicyId;
    payload.name = `SST ${sst} SD ${sd || "—"}`;
    return payload;
  };

  const loadPolicyIntoForm = useCallback((rec) => {
    const policy = rec.policy || rec;
    const scope = policy?.scope?.sliceId || {};
    const objs = policy?.sliceSlaObjectives || {};
    const plmn = scope.plmnId || {};
    const pid = rec.policy_id || rec.id || sliceSlaPolicyId(scope.sd) || "";

    setPolicyId(pid);
    setPolicyIdDirty(true);
    setEditingId(pid || null);
    setSst(numOrEmpty(scope.sst != null ? scope.sst : "1"));
    setSd(numOrEmpty(scope.sd || ""));
    setMcc(numOrEmpty(plmn.mcc || "001"));
    setMnc(numOrEmpty(plmn.mnc || "01"));

    setGuaDl(numOrEmpty(objs.guaDlThptPerSlice));
    setMaxDl(numOrEmpty(objs.maxDlThptPerSlice));
    setGuaUl(numOrEmpty(objs.guaUlThptPerSlice));
    setMaxUl(numOrEmpty(objs.maxUlThptPerSlice));
    setMaxUes(numOrEmpty(objs.maxNumberOfUes));
    setMaxPdu(numOrEmpty(objs.maxNumberOfPduSessions));
    setDlDelay(numOrEmpty(objs.maxDlPacketDelayPerUe));
    setUlDelay(numOrEmpty(objs.maxUlPacketDelayPerUe));
    setDlLoss(numOrEmpty(objs.maxDlPdcpSduPacketLossRatePerUe));
    setUlLoss(numOrEmpty(objs.maxUlRlcSduPacketLossRatePerUe));
    setDlPri(numOrEmpty(objs.dlSlicePriority));
    setUlPri(numOrEmpty(objs.ulSlicePriority));

    const cells = policy?.sliceSlaResources?.cellIdList || [];
    setCellNcis(cells.map((c) => c?.cId?.ncI).filter((n) => n != null).join(","));

    setActiveExamples(new Set());
    setJsonDirty(false);
    setJsonText(JSON.stringify(policy, null, 2));
  }, []);

  // Live Auto-Sync to JSON textarea
  useEffect(() => {
    if (!jsonDirty) {
      const p = buildPolicy();
      setJsonText(JSON.stringify(p, null, 2));
    }
  }, [buildPolicy, jsonDirty]);

  // Toggle Preset Example
  const handleToggleExample = (ex) => {
    const isLoaded = activeExamples.has(ex.id);
    const nextSet = new Set(activeExamples);
    if (isLoaded) {
      nextSet.delete(ex.id);
      if (ex.id === "embb-high-throughput") {
        setGuaDl(""); setMaxDl(""); setGuaUl(""); setMaxUl(""); setDlPri(""); setUlPri("");
      } else if (ex.id === "urllc-low-latency") {
        setDlDelay(""); setUlDelay(""); setDlLoss(""); setUlLoss("");
      } else if (ex.id === "miot-massive-ues") {
        setMaxUes(""); setMaxPdu("");
      }
      setToast({ type: "info", text: `Unloaded preset: ${ex.name}` });
    } else {
      nextSet.add(ex.id);
      if (ex.id === "embb-high-throughput") {
        setGuaDl("100000"); setMaxDl("300000"); setGuaUl("50000"); setMaxUl("150000"); setDlPri("10"); setUlPri("15");
      } else if (ex.id === "urllc-low-latency") {
        setDlDelay("10"); setUlDelay("10"); setDlLoss("0.01"); setUlLoss("0.01");
      } else if (ex.id === "miot-massive-ues") {
        setMaxUes("500"); setMaxPdu("2000");
      }
      setToast({ type: "ok", text: `Loaded preset: ${ex.name}` });
    }
    setActiveExamples(nextSet);
    setJsonDirty(false);
  };

  const handleApply = async () => {
    setIsApplying(true);
    const isUpdate = !!(editingId && resolvedPolicyId === editingId);
    setToast({
      type: "info",
      text: isUpdate
        ? `Updating A1 policy ${resolvedPolicyId} via A1 PMS...`
        : "Applying A1 Slice SLA policy via A1 PMS to Near-RT RIC xApp...",
    });
    try {
      let policy;
      try {
        policy = jsonDirty ? JSON.parse(jsonText) : buildPolicy();
      } catch (err) {
        policy = buildPolicy();
      }

      const res = await fetch("/api/v1/activate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(wrapPayload(policy)),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to activate policy");
      if (data.pms_error) throw new Error(`A1 PMS update failed: ${data.pms_error}`);

      const nStored = data.xapp?.stored?.length ?? 1;
      const appliedId = data.policy_id || resolvedPolicyId || "unknown";
      setEditingId(appliedId);
      setToast({
        type: "ok",
        text: `Policy ${appliedId} ${isUpdate ? "updated" : "applied"} via A1 PMS → Near-RT xApp has ${nStored} active slice(s).`,
      });
      fetchActive();
      if (actions?.refreshAll) actions.refreshAll();
    } catch (e) {
      setToast({ type: "err", text: `Apply failed: ${e.message}` });
    } finally {
      setIsApplying(false);
    }
  };

  const handleValidate = async () => {
    setIsValidating(true);
    try {
      const policy = jsonDirty ? JSON.parse(jsonText) : buildPolicy();
      const res = await fetch("/api/v1/policies/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(policy),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Validation failed");
      setToast({ type: "ok", text: "A1 Policy is valid." });
    } catch (e) {
      setToast({ type: "err", text: `Validation failed: ${e.message}` });
    } finally {
      setIsValidating(false);
    }
  };

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const policy = jsonDirty ? JSON.parse(jsonText) : buildPolicy();
      const res = await fetch("/api/v1/policies", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(wrapPayload(policy)),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to save policy");
      setToast({ type: "ok", text: `Policy saved to rApp repository with ID: ${data.id}` });
      if (actions?.refreshAll) actions.refreshAll();
    } catch (e) {
      setToast({ type: "err", text: `Save error: ${e.message}` });
    } finally {
      setIsSaving(false);
    }
  };

  const handleEditActive = (rec) => {
    loadPolicyIntoForm(rec);
    setToast({ type: "info", text: `Loaded ${rec.policy_id || "policy"} into editor — change fields and click Update.` });
    const el = document.getElementById("sla-editor");
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
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
      if (editingId === id) setEditingId(null);
      const note = data.xapp_error ? ` (xApp cache: ${data.xapp_error})` : "";
      setToast({ type: "ok", text: `Deleted ${id} from A1-PMS${note}` });
      fetchActive();
    } catch (e) {
      setToast({ type: "err", text: e.message });
    } finally {
      setDeletingId(null);
    }
  };

  const isUpdateMode = !!(editingId && resolvedPolicyId === editingId);

  return (
    <div className="tier" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {toast && (
        <div className={`toast ${toast.type}`}>
          <span>{toast.text}</span>
          <button type="button" className="btn btn-sm btn-secondary" onClick={() => setToast(null)}>Dismiss</button>
        </div>
      )}

      {/* Active policies — first page */}
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", gap: "12px", flexWrap: "wrap" }}>
          <div>
            <SectionLabel>Active A1 Policies</SectionLabel>
            <div style={{ fontSize: "11px", color: "var(--text-faint)", marginTop: "4px" }}>
              RIC {activeMeta.ric_id || "—"} · type {activeMeta.policytype_id || "—"} · {activePolicies.length} instance{activePolicies.length === 1 ? "" : "s"}
            </div>
          </div>
          <button type="button" className="btn btn-sm btn-secondary" onClick={fetchActive}>Refresh</button>
        </div>

        {activeMeta.error && (
          <p style={{ color: "var(--bad)", fontSize: "13px", marginBottom: "8px" }}>A1-PMS error: {activeMeta.error}</p>
        )}

        {activePolicies.length === 0 && !activeMeta.error ? (
          <p style={{ color: "var(--text-dim)", fontSize: "13px" }}>
            No active A1 policies. Compose a Slice SLA below and click Apply.
          </p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Policy ID</th>
                  <th>Slice</th>
                  <th>Gua / Max DL</th>
                  <th style={{ textAlign: "right" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {activePolicies.map((p) => {
                  const scope = p.policy?.scope?.sliceId || {};
                  const objs = p.policy?.sliceSlaObjectives || {};
                  const selected = editingId === p.policy_id;
                  return (
                    <tr key={p.policy_id} style={selected ? { background: "color-mix(in srgb, var(--accent) 8%, transparent)" } : undefined}>
                      <td className="mono" style={{ color: "var(--accent)" }}>{p.policy_id}</td>
                      <td>SST {scope.sst ?? "—"} / SD {scope.sd ?? "—"}</td>
                      <td className="mono">
                        {objs.guaDlThptPerSlice != null ? Number(objs.guaDlThptPerSlice).toLocaleString() : "—"}
                        {" / "}
                        {objs.maxDlThptPerSlice != null ? Number(objs.maxDlThptPerSlice).toLocaleString() : "—"}
                      </td>
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        <button
                          type="button"
                          className="btn btn-sm btn-primary"
                          style={{ marginRight: "6px" }}
                          onClick={() => handleEditActive(p)}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-danger"
                          disabled={deletingId === p.policy_id}
                          onClick={() => handleDeleteActive(p.policy_id)}
                        >
                          {deletingId === p.policy_id ? "Deleting…" : "Delete"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Two-Column Editor Layout */}
      <div id="sla-editor" className="row-2" style={{ display: "grid", gridTemplateColumns: "1.2fr 0.8fr", gap: "16px" }}>
        <Card>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
            <SectionLabel>Slice Scope & Objectives Specification</SectionLabel>
            {isUpdateMode && <span className="pill warn">Editing {editingId}</span>}
          </div>

          <div style={{ marginTop: "12px", display: "flex", flexDirection: "column", gap: "14px" }}>
            <div style={{ background: "var(--surface-2)", padding: "12px", borderRadius: "6px", border: "1px solid var(--border)" }}>
              <div style={{ fontSize: "11px", fontWeight: "600", textTransform: "uppercase", color: "var(--text-dim)", marginBottom: "8px" }}>
                Target Slice Identifier (Scope)
              </div>
              <label className="field" style={{ marginBottom: "10px" }}>
                Policy ID
                <input
                  className="mono"
                  type="text"
                  value={policyId}
                  placeholder={sliceSlaPolicyId(sd) || "slice-sla-1"}
                  onChange={(e) => {
                    setPolicyId(e.target.value);
                    setPolicyIdDirty(true);
                  }}
                />
                <span className="field-hint">A1 instance id. Open5GS SD 00000N maps to slice-sla-N.</span>
              </label>
              <div className="grid-4">
                <label className="field">
                  SST (0-255)
                  <input type="number" min="0" max="255" value={sst} onChange={(e) => { setSst(e.target.value); setJsonDirty(false); }} />
                </label>
                <label className="field">
                  SD (6-hex)
                  <input
                    className="mono"
                    type="text"
                    maxLength="6"
                    value={sd}
                    placeholder="000001"
                    onChange={(e) => {
                      const next = e.target.value;
                      setSd(next);
                      setJsonDirty(false);
                      if (!policyIdDirty) setPolicyId(sliceSlaPolicyId(next));
                    }}
                  />
                </label>
                <label className="field">
                  PLMN MCC
                  <input className="mono" type="text" maxLength="3" value={mcc} onChange={(e) => { setMcc(e.target.value); setJsonDirty(false); }} />
                </label>
                <label className="field">
                  PLMN MNC
                  <input className="mono" type="text" maxLength="3" value={mnc} onChange={(e) => { setMnc(e.target.value); setJsonDirty(false); }} />
                </label>
              </div>
            </div>

            <div>
              <div style={{ fontSize: "11px", fontWeight: "600", textTransform: "uppercase", color: "var(--text-dim)", marginBottom: "8px" }}>
                Throughput SLAs (kbps)
              </div>
              <div className="grid-2">
                <label className="field">
                  Guaranteed DL Throughput
                  <input className="mono" type="number" step="1000" placeholder="e.g. 50000" value={guaDl} onChange={(e) => { setGuaDl(e.target.value); setJsonDirty(false); }} />
                  <span className="field-hint">Min dedicated PRB target</span>
                </label>
                <label className="field">
                  Maximum DL Throughput
                  <input className="mono" type="number" step="1000" placeholder="e.g. 100000" value={maxDl} onChange={(e) => { setMaxDl(e.target.value); setJsonDirty(false); }} />
                  <span className="field-hint">Max PRB ceiling</span>
                </label>
                <label className="field">
                  Guaranteed UL Throughput
                  <input className="mono" type="number" step="1000" placeholder="optional" value={guaUl} onChange={(e) => { setGuaUl(e.target.value); setJsonDirty(false); }} />
                </label>
                <label className="field">
                  Maximum UL Throughput
                  <input className="mono" type="number" step="1000" placeholder="optional" value={maxUl} onChange={(e) => { setMaxUl(e.target.value); setJsonDirty(false); }} />
                </label>
              </div>
            </div>

            <div>
              <div style={{ fontSize: "11px", fontWeight: "600", textTransform: "uppercase", color: "var(--text-dim)", marginBottom: "8px" }}>
                Session Limits & QoS Objectives
              </div>
              <div className="grid-4">
                <label className="field">
                  Max UEs
                  <input className="mono" type="number" placeholder="100" value={maxUes} onChange={(e) => { setMaxUes(e.target.value); setJsonDirty(false); }} />
                </label>
                <label className="field">
                  Max PDU Sessions
                  <input className="mono" type="number" placeholder="800" value={maxPdu} onChange={(e) => { setMaxPdu(e.target.value); setJsonDirty(false); }} />
                </label>
                <label className="field">
                  Max DL Delay (ms)
                  <input className="mono" type="number" step="0.5" placeholder="10" value={dlDelay} onChange={(e) => { setDlDelay(e.target.value); setJsonDirty(false); }} />
                </label>
                <label className="field">
                  DL Loss Rate
                  <input className="mono" type="number" step="0.001" placeholder="0.01" value={dlLoss} onChange={(e) => { setDlLoss(e.target.value); setJsonDirty(false); }} />
                </label>
              </div>
            </div>

            <div>
              <label className="field">
                NR Cell Identities (CSV ncI, optional)
                <input className="mono" type="text" placeholder="101,102" value={cellNcis} onChange={(e) => { setCellNcis(e.target.value); setJsonDirty(false); }} />
              </label>
            </div>

            <div className="toolbar" style={{ marginTop: "8px", paddingTop: "12px", borderTop: "1px solid var(--border)" }}>
              <button
                type="button"
                className="btn btn-primary"
                style={{ minWidth: "130px" }}
                disabled={isApplying}
                onClick={handleApply}
              >
                {isApplying ? (isUpdateMode ? "Updating..." : "Applying...") : (isUpdateMode ? "Update Policy" : "Apply Policy")}
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                disabled={isValidating}
                onClick={handleValidate}
              >
                {isValidating ? "Validating..." : "Validate & Preview"}
              </button>
              <button
                type="button"
                className="btn btn-secondary"
                disabled={isSaving}
                onClick={handleSave}
              >
                {isSaving ? "Saving..." : "Save on rApp"}
              </button>
              {editingId && (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => {
                    setEditingId(null);
                    setToast({ type: "info", text: "Cleared edit mode — Apply will create/update by Policy ID." });
                  }}
                >
                  Clear Edit
                </button>
              )}
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                style={{ marginLeft: "auto" }}
                onClick={() => {
                  setJsonDirty(false);
                  setJsonText(JSON.stringify(buildPolicy(), null, 2));
                  setToast({ type: "info", text: "Synced form state to JSON editor." });
                }}
              >
                Form → JSON
              </button>
            </div>
          </div>
        </Card>

        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <Card style={{ flex: 1, display: "flex", flexDirection: "column" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
              <SectionLabel>A1 Policy Payload (JSON Source of Truth)</SectionLabel>
              {jsonDirty && <span className="pill warn">Manually Modified</span>}
            </div>
            <textarea
              className="mono json-box"
              style={{ flex: 1, minHeight: "280px", resize: "vertical" }}
              value={jsonText}
              onChange={(e) => {
                setJsonText(e.target.value);
                setJsonDirty(true);
              }}
            />
          </Card>
        </div>
      </div>

      {/* Presets Strip — bottom */}
      <Card>
        <SectionLabel>O-RAN Standard SLA Presets (ORAN_SliceSLATarget_3.0.0)</SectionLabel>
        <div className="grid-3" style={{ marginTop: "10px" }}>
          {PRESET_EXAMPLES.map((ex) => {
            const isLoaded = activeExamples.has(ex.id);
            return (
              <div
                key={ex.id}
                style={{
                  border: isLoaded ? "1px solid var(--accent)" : "1px solid var(--border)",
                  borderRadius: "8px",
                  padding: "12px",
                  background: isLoaded ? "color-mix(in srgb, var(--accent) 5%, var(--surface))" : "var(--surface)",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "space-between",
                  gap: "10px",
                }}
              >
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                    <strong style={{ fontSize: "13px", color: "var(--text)" }}>{ex.name}</strong>
                    <span className="pill ok">{ex.badge}</span>
                  </div>
                  <p style={{ fontSize: "11px", color: "var(--text-dim)", lineHeight: "1.4" }}>{ex.description}</p>
                </div>
                <button
                  type="button"
                  className={`btn btn-sm ${isLoaded ? "btn-secondary" : "btn-primary"}`}
                  onClick={() => handleToggleExample(ex)}
                >
                  {isLoaded ? "Unload Preset" : "Add Preset Fields"}
                </button>
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}

Object.assign(window, { SlaTab });
