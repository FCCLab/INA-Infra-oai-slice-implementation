// TopologyTab.jsx — Near-RT RIC Platform Architecture & Reachable APIs
// Exposes (window): TopologyTab

const { useState, useEffect } = React;

function TopologyTab({ state, colors, config }) {
  const [hosts, setHosts] = useState([]);
  const [copiedUrl, setCopiedUrl] = useState(null);

  useEffect(() => {
    fetch("/api/v1/hosts")
      .then((r) => r.json())
      .then((d) => setHosts(d.base_urls || []))
      .catch(() => {
        setHosts([window.location.origin]);
      });
  }, []);

  const handleCopy = (url) => {
    navigator.clipboard.writeText(url).then(() => {
      setCopiedUrl(url);
      setTimeout(() => setCopiedUrl(null), 2000);
    });
  };

  const endpoints = [
    { method: "GET", path: "/api/v1/slices", desc: "List active E2 PRB slice allocations and indications" },
    { method: "PATCH", path: "/api/v1/slices", desc: "Update PRB allocation quotas for a specific slice" },
    { method: "PUT", path: "/api/v1/slices", desc: "Batch replace and apply PRB allocation quotas for all slices" },
    { method: "GET", path: "/api/v1/a1/slice-sla", desc: "List received O-RAN A1 Slice SLA Target policies" },
    { method: "POST", path: "/api/v1/a1/slice-sla", desc: "Store / ingest A1 Slice SLA Target policy directly" },
    { method: "GET", path: "/api/v1/hosts", desc: "List reachable base URLs for this xApp service" },
    { method: "GET", path: "/docs", desc: "Interactive OpenAPI / Swagger documentation" },
    { method: "GET", path: "/health", desc: "Kubernetes liveness and readiness probe" },
  ];

  return (
    <div className="tier" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {/* Topology Architecture Cards */}
      <Card>
        <SectionLabel>Near-RT RIC Architecture & Inbound Interfaces</SectionLabel>
        <div className="grid-3" style={{ marginTop: "12px" }}>
          <div className="sla-card" style={{ margin: 0 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
              <strong style={{ color: "var(--text)" }}>A1 Mediator HTTP</strong>
              <span className="pill ok">Active</span>
            </div>
            <div className="mono" style={{ fontSize: "11px", color: "var(--text-dim)", wordBreak: "break-all" }}>
              http://service-ricplt-a1mediator-http.ricplt:10000
            </div>
            <p style={{ marginTop: "8px", fontSize: "11px", color: "var(--text-faint)" }}>
              xApp polls A1 Mediator policy type 20008 (ORAN_SliceSLATarget_3.0.0) every 3 seconds.
            </p>
          </div>

          <div className="sla-card" style={{ margin: 0 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
              <strong style={{ color: "var(--text)" }}>E2 Node (gNB / FlexRIC)</strong>
              <span className="pill ok">Connected</span>
            </div>
            <div className="mono" style={{ fontSize: "11px", color: "var(--text-dim)" }}>
              E2 SM: E2SM-RC / Slice Service Model
            </div>
            <p style={{ marginTop: "8px", fontSize: "11px", color: "var(--text-faint)" }}>
              Controls dedicated, minimum, and maximum PRB % per slice on the radio network.
            </p>
          </div>

          <div className="sla-card" style={{ margin: 0 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
              <strong style={{ color: "var(--text)" }}>xApp Microservice</strong>
              <span className="pill ok">Healthy</span>
            </div>
            <div className="mono" style={{ fontSize: "11px", color: "var(--text-dim)" }}>
              Namespace: ricxapp | Pod: nws-xapp
            </div>
            <p style={{ marginTop: "8px", fontSize: "11px", color: "var(--text-faint)" }}>
              Exposes REST API on port 18080 and NeuroRAN Console on port 18081.
            </p>
          </div>
        </div>
      </Card>

      {/* API Endpoints Table */}
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
          <SectionLabel>Near-RT xApp REST API Catalog</SectionLabel>
          <a href="/docs" target="_blank" rel="noreferrer" className="btn btn-sm btn-primary">
            Open Swagger UI ↗
          </a>
        </div>

        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: "90px" }}>Method</th>
                <th>Endpoint Path</th>
                <th>Description</th>
                <th style={{ textAlign: "right" }}>Copy URL</th>
              </tr>
            </thead>
            <tbody>
              {endpoints.map((ep, idx) => {
                const fullUrl = `${window.location.origin}${ep.path}`;
                const isCopied = copiedUrl === fullUrl;
                return (
                  <tr key={idx}>
                    <td>
                      <span className={`pill ${ep.method === "GET" ? "ok" : ep.method === "PUT" ? "warn" : "ok"}`}>
                        {ep.method}
                      </span>
                    </td>
                    <td className="mono" style={{ color: "var(--text)" }}>{ep.path}</td>
                    <td style={{ color: "var(--text-dim)" }}>{ep.desc}</td>
                    <td style={{ textAlign: "right" }}>
                      <button
                        type="button"
                        className="btn btn-sm btn-secondary"
                        onClick={() => handleCopy(fullUrl)}
                      >
                        {isCopied ? "Copied!" : "Copy"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

Object.assign(window, { TopologyTab });
