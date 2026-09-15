// components/Card.jsx — base surface container
// Exposes (global + window): Card

function Card({ children, style, className, glow, onClick, title }) {
  return (
    <div
      className={"card" + (glow ? " card-glow" : "") + (className ? " " + className : "")}
      style={style}
      onClick={onClick}
      title={title}
    >
      {children}
    </div>
  );
}

Object.assign(window, { Card });
