// SlaTab.jsx — A1 Slice SLA Editor & Enforcer (NeuroRAN rApp)
// Exposes (window): SlaTab

const { useState, useEffect, useMemo, useCallback } = React;

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

function SlaTab({ state, colors, config, actions }) {
  // Scope State
  const [sst, setSst] = useState("1");
  const [sd, setSd] = useState("000002");
  const [mcc, setMcc] = useState("001");
  const [mnc, setMnc] = useState("01");

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

  // Live Auto-Sync to JSON textarea
  useEffect(() => {
    if (!jsonDirty) {
      const p = buildPolicy();
      setJsonText(JSON.stringify(p, null, 2));
    }
  }, [buildPolicy, jsonDirty]);

  // Estimated PRB allocation
  const estimatedPrb = useMemo(() => {
    const g = Number(guaDl) || 0;
    const m = Number(maxDl) || 0;
    const ded = Math.min(100, Math.max(5, Math.round(g / 2000)));
    const maxP = Math.min(100, Math.max(ded, Math.round(m / 1000)));
    return { dedicated: ded, min: ded, max: maxP };
  }, [guaDl, maxDl]);

  // Toggle Preset Example
  const handleToggleExample = (ex) => {
    const isLoaded = activeExamples.has(ex.id);
    const nextSet = new Set(activeExamples);
    if (isLoaded) {
      nextSet.delete(ex.id);
      // clear fields of this preset
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

  // Actions
  const handleApply = async () => {
    setIsApplying(true);
    setToast({ type: "info", text: "Applying A1 Slice SLA policy via A1 PMS to Near-RT RIC xApp..." });
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
        body: JSON.stringify(policy),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to activate policy");

      const nStored = data.xapp?.stored?.length ?? 1;
      setToast({
        type: "ok",
        text: `Policy applied successfully! Activated via A1 PMS (Type 20008) -> Near-RT xApp has ${nStored} active slice(s).`,
      });
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
      setToast({ type: "ok", text: `A1 Policy is valid! Expected E2 PRB: Dedicated ${data.prb_preview?.dedicated ?? estimatedPrb.dedicated}%, Max ${data.prb_preview?.max ?? estimatedPrb.max}%.` });
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
        body: JSON.stringify(policy),
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

  return (
    <div className="tier" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {toast && (
        <div className={`toast ${toast.type}`}>
          <span>{toast.text}</span>
          <button type="button" className="btn btn-sm btn-secondary" onClick={() => setToast(null)}>Dismiss</button>
        </div>
      )}

      {/* Presets Strip */}
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

      {/* Two-Column Editor Layout */}
      <div className="row-2" style={{ display: "grid", gridTemplateColumns: "1.2fr 0.8fr", gap: "16px" }}>
        {/* Left Column: Form Controls */}
        <Card>
          <SectionLabel>Slice Scope & Objectives Specification</SectionLabel>

          <div style={{ marginTop: "12px", display: "flex", flexDirection: "column", gap: "14px" }}>
            {/* Scope Row */}
            <div style={{ background: "var(--surface-2)", padding: "12px", borderRadius: "6px", border: "1px solid var(--border)" }}>
              <div style={{ fontSize: "11px", fontWeight: "600", textTransform: "uppercase", color: "var(--text-dim)", marginBottom: "8px" }}>
                Target Slice Identifier (Scope)
              </div>
              <div className="grid-4">
                <label className="field">
                  SST (0-255)
                  <input type="number" min="0" max="255" value={sst} onChange={(e) => { setSst(e.target.value); setJsonDirty(false); }} />
                </label>
                <label className="field">
                  SD (6-hex)
                  <input className="mono" type="text" maxLength="6" value={sd} placeholder="000002" onChange={(e) => { setSd(e.target.value); setJsonDirty(false); }} />
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

            {/* Throughput Objectives */}
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

            {/* Capacity & Latency */}
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

            {/* Cell Resources */}
            <div>
              <label className="field">
                NR Cell Identities (CSV ncI, optional)
                <input className="mono" type="text" placeholder="101,102" value={cellNcis} onChange={(e) => { setCellNcis(e.target.value); setJsonDirty(false); }} />
              </label>
            </div>

            {/* Action Toolbar */}
            <div className="toolbar" style={{ marginTop: "8px", paddingTop: "12px", borderTop: "1px solid var(--border)" }}>
              <button
                type="button"
                className="btn btn-primary"
                style={{ minWidth: "130px" }}
                disabled={isApplying}
                onClick={handleApply}
              >
                {isApplying ? "Applying..." : "Apply Policy"}
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

        {/* Right Column: Live JSON & E2 PRB Translation */}
        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <Card>
            <SectionLabel>Expected E2 PRB Allocation (Near-RT Mapper)</SectionLabel>
            <div style={{ marginTop: "10px", display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "10px" }}>
              <div className="sla-metric-box">
                <div className="sla-metric-lbl">Dedicated PRB</div>
                <div className="sla-metric-val" style={{ color: "var(--accent)" }}>{estimatedPrb.dedicated}%</div>
              </div>
              <div className="sla-metric-box">
                <div className="sla-metric-lbl">Min PRB</div>
                <div className="sla-metric-val">{estimatedPrb.min}%</div>
              </div>
              <div className="sla-metric-box">
                <div className="sla-metric-lbl">Max PRB</div>
                <div className="sla-metric-val">{estimatedPrb.max}%</div>
              </div>
            </div>
            <p style={{ marginTop: "8px", fontSize: "11px", color: "var(--text-faint)" }}>
              xApp translates Guaranteed DL ({guaDl || 0} kbps) to {estimatedPrb.dedicated}% dedicated PRBs and sets Max PRB ceiling at {estimatedPrb.max}%.
            </p>
          </Card>

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
    </div>
  );
}

Object.assign(window, { SlaTab });
