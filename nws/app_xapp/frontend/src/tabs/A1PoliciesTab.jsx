// A1PoliciesTab.jsx — Received O-RAN A1 Slice SLA Target Policies (NeuroRAN xApp)
// Exposes (window): A1PoliciesTab

const { useState, useEffect } = React;

function A1PoliciesTab({ state, colors, config }) {
  const [policies, setPolicies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [lastRefreshed, setLastRefreshed] = useState(null);

  const fetchPolicies = async () => {
    try {
      const res = await fetch("/api/v1/a1/slice-sla");
      const data = await res.json();
      setPolicies(data.policies || []);
      setLastRefreshed(new Date().toLocaleTimeString());
    } catch (e) {
      console.error("Failed to fetch A1 policies:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPolicies();
    const id = setInterval(fetchPolicies, 2000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="tier" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", flexWrap: "wrap", gap: "8px" }}>
          <div>
            <SectionLabel>Received O-RAN A1 Slice SLA Target Policies</SectionLabel>
            <div style={{ fontSize: "11px", color: "var(--text-dim)", marginTop: "2px" }}>
              Inbound interface: O-RAN A1-P (Type 20008: ORAN_SliceSLATarget_3.0.0 via A1 Mediator)
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span style={{ fontSize: "11px", color: "var(--text-faint)" }}>
              Updated: {lastRefreshed || "connecting..."}
            </span>
            <button type="button" className="btn btn-sm btn-secondary" onClick={fetchPolicies}>
              Refresh Now
            </button>
          </div>
        </div>

        {policies.length === 0 ? (
          <div style={{ padding: "30px 10px", textAlign: "center", color: "var(--text-dim)" }}>
            <p style={{ fontSize: "13px" }}>No A1 Slice SLA policies received yet from Non-RT RIC rApp.</p>
            <p style={{ fontSize: "11px", color: "var(--text-faint)", marginTop: "4px" }}>
              Send an SLA policy from the rApp console (port 31091) to see it appear here.
            </p>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            {policies.map((item, idx) => {
              const p = item.policy || {};
              const scope = p.scope?.sliceId || {};
              const objs = p.sliceSlaObjectives || {};
              const recTime = item.received_at ? new Date(item.received_at * 1000).toLocaleString() : "active";
              const plmn = scope.plmnId ? `${scope.plmnId.mcc}/${scope.plmnId.mnc}` : "—";
              const sst = scope.sst != null ? scope.sst : "—";
              const sd = scope.sd || "—";

              return (
                <div key={item.key || idx} className="sla-card">
                  <div className="sla-card-head">
                    <div className="sla-card-title">
                      <span>Slice: SST {sst} / SD {sd}</span>
                      <span className="pill ok">PLMN {plmn}</span>
                      <span className="pill ok">A1 Target Active</span>
                    </div>
                    <span style={{ fontSize: "11px", color: "var(--text-faint)" }}>
                      Received: {recTime}
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
                    {objs.maxNumberOfPduSessions != null && (
                      <div className="sla-metric-box">
                        <div className="sla-metric-lbl">Max PDU</div>
                        <div className="sla-metric-val">{objs.maxNumberOfPduSessions}</div>
                      </div>
                    )}
                    {objs.maxDlPacketDelayPerUe != null && (
                      <div className="sla-metric-box">
                        <div className="sla-metric-lbl">Max Delay (ms)</div>
                        <div className="sla-metric-val">{objs.maxDlPacketDelayPerUe}</div>
                      </div>
                    )}
                    {objs.maxDlPdcpSduPacketLossRatePerUe != null && (
                      <div className="sla-metric-box">
                        <div className="sla-metric-lbl">DL Loss Rate</div>
                        <div className="sla-metric-val">{objs.maxDlPdcpSduPacketLossRatePerUe}</div>
                      </div>
                    )}
                  </div>

                  <details style={{ marginTop: "4px" }}>
                    <summary style={{ fontSize: "11px", color: "var(--accent)", cursor: "pointer" }}>
                      View Full O-RAN A1 Policy Payload
                    </summary>
                    <pre className="json-box" style={{ marginTop: "6px" }}>
                      {JSON.stringify(p, null, 2)}
                    </pre>
                  </details>
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}

Object.assign(window, { A1PoliciesTab });
