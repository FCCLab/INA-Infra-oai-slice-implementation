// components/SectionLabel.jsx — panel heading with accent bar + optional kicker
// Exposes (global + window): SectionLabel

function SectionLabel({ children, kicker }) {
  return (
    <div className="section-label">
      <span className="section-bar" />
      <span className="section-text">{children}</span>
      {kicker && <span className="section-kicker">{kicker}</span>}
    </div>
  );
}

Object.assign(window, { SectionLabel });
