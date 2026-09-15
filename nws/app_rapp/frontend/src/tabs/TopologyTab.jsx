// TopologyTab.jsx — Platform Architecture & Reachable API Endpoints
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
        // Fallback to origin
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
    { method: "GET", path: "/api/v1/policies", desc: "List all saved slice SLA policies in rApp store" },
    { method: "POST", path: "/api/v1/activate", desc: "Activate A1 policy ad-hoc: forwards to A1 PMS and notifies xApp" },
    { method: "POST", path: "/api/v1/policies/preview", desc: "Validate A1 JSON schema and calculate expected E2 PRB %" },
    { method: "GET", path: "/api/v1/a1/slice-sla", desc: "Query live SLA policies currently stored on Near-RT xApp" },
    { method: "GET", path: "/api/v1/history", desc: "Retrieve policy activation audit history" },
    { method: "GET", path: "/docs", desc: "Interactive OpenAPI / Swagger documentation" },
    { method: "GET", path: "/health", desc: "Health check endpoint (Kubernetes liveness probe)" },
  ];

  return (
    <div className="tier" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {/* Component Topology Cards */}
      <Card>
        <SectionLabel>O-RAN Architecture & Service Connectivity</SectionLabel>
        <div className="grid-3" style={{ marginTop: "12px" }}>
          <div className="sla-card" style={{ margin: 0 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
              <strong style={{ color: "var(--text)" }}>A1 Policy Management (PMS)</strong>
              <span className="pill ok">Active</span>
            </div>
            <div className="mono" style={{ fontSize: "11px", color: "var(--text-dim)", wordBreak: "break-all" }}>
              http://policymanagementservice.nonrtric:8081/a1-policy-management/v1
            </div>
            <p style={{ marginTop: "8px", fontSize: "11px", color: "var(--text-faint)" }}>
              Standard O-RAN A1 controller running in namespace <code>nonrtric</code>.
            </p>
          </div>

          <div className="sla-card" style={{ margin: 0 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
              <strong style={{ color: "var(--text)" }}>Near-RT RIC Target</strong>
              <span className="pill ok">Connected</span>
            </div>
            <div className="mono" style={{ fontSize: "11px", color: "var(--text-dim)" }}>
              RIC ID: nearrt-ric | Type: 20008
            </div>
            <p style={{ marginTop: "8px", fontSize: "11px", color: "var(--text-faint)" }}>
              A1 Mediator in namespace <code>ricplt</code> terminates A1-P interface and synchronizes policies to xApps.
            </p>
          </div>

          <div className="sla-card" style={{ margin: 0 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
              <strong style={{ color: "var(--text)" }}>Near-RT Network Slicing xApp</strong>
              <span className="pill ok">Synced</span>
            </div>
            <div className="mono" style={{ fontSize: "11px", color: "var(--text-dim)" }}>
              http://nws-xapp.ricxapp:18080
            </div>
            <p style={{ marginTop: "8px", fontSize: "11px", color: "var(--text-faint)" }}>
              Controls E2 Node PRB quotas based on received SLA targets.
            </p>
          </div>
        </div>
      </Card>

      {/* API Endpoints */}
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
          <SectionLabel>Non-RT rApp REST API Catalog</SectionLabel>
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
                      <span className={`pill ${ep.method === "GET" ? "ok" : "warn"}`}>{ep.method}</span>
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
