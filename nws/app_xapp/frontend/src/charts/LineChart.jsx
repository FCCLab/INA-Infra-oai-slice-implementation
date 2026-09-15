// charts/LineChart.jsx — generic time-series area/line chart on canvas
// Exposes (global + window): LineChart
//
// Props:
//   series : [{ x: number, y: number }, ...]  (x is a monotonic value, e.g. hour or epoch)
//   colors : buildColors(dark, accent) result
//   height : px (default 220)
//   yUnit  : optional axis unit label (e.g. "%", "Mbps")
//   yMin   : optional fixed axis floor (default 0)
//   fmtX   : optional (x)=>string tick formatter (default: identity/2-decimals)
//
// This is intentionally simple and dependency-free. Replace or extend per rApp;
// colours are always resolved from `colors` (buildColors) — never hard-coded.

function LineChart({ series, colors, height = 220, yUnit = "", yMin = 0, fmtX }) {
  const ref = useCanvas((ctx, W, H) => {
    const data = Array.isArray(series) ? series.filter((d) => d && Number.isFinite(d.y)) : [];
    if (data.length < 2) return;

    const padL = 48, padR = 18, padT = 18, padB = 28;
    const plotW = W - padL - padR, plotH = H - padT - padB;

    const ys = data.map((d) => d.y);
    const dataMax = Math.max(...ys);
    const minP = Number.isFinite(yMin) ? yMin : Math.min(...ys);
    const maxP = dataMax + Math.max(dataMax * 0.08, 1);
    const range = (maxP - minP) || 1;

    const xMin = data[0].x, xMax = data[data.length - 1].x;
    const xRange = (xMax - xMin) || 1;
    const x = (v) => padL + ((v - xMin) / xRange) * plotW;
    const y = (v) => padT + (1 - (v - minP) / range) * plotH;

    // axes
    ctx.strokeStyle = colors.grid; ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, padT); ctx.lineTo(padL, H - padB); ctx.lineTo(W - padR, H - padB);
    ctx.stroke();

    // y gridlines + labels
    ctx.font = "10px 'IBM Plex Mono', monospace";
    ctx.textBaseline = "middle";
    const yTicks = 4;
    for (let i = 0; i <= yTicks; i++) {
      const p = minP + (range / yTicks) * i;
      const yy = y(p);
      ctx.strokeStyle = colors.grid; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(W - padR, yy); ctx.stroke();
      if (i < yTicks) {
        ctx.fillStyle = colors.axis; ctx.textAlign = "right";
        ctx.fillText(p.toFixed(range < 10 ? 1 : 0), padL - 8, yy);
      }
    }
    if (yUnit) {
      ctx.fillStyle = colors.axis; ctx.textAlign = "left"; ctx.textBaseline = "top";
      ctx.font = "9px 'IBM Plex Sans', sans-serif";
      ctx.fillText(yUnit, 6, padT + 2);
    }

    // x tick labels (first, mid, last)
    ctx.fillStyle = colors.axis; ctx.textAlign = "center"; ctx.textBaseline = "top";
    ctx.font = "10px 'IBM Plex Mono', monospace";
    const fx = typeof fmtX === "function" ? fmtX : (v) => (Math.abs(v) < 100 ? v.toFixed(1) : String(Math.round(v)));
    [xMin, (xMin + xMax) / 2, xMax].forEach((v) => ctx.fillText(fx(v), x(v), H - padB + 8));

    // area fill
    const grad = ctx.createLinearGradient(0, padT, 0, H - padB);
    grad.addColorStop(0, colors.fillTop);
    grad.addColorStop(1, colors.fillBot);
    ctx.beginPath();
    data.forEach((d, i) => { const xx = x(d.x), yy = y(d.y); i ? ctx.lineTo(xx, yy) : ctx.moveTo(xx, yy); });
    ctx.lineTo(x(xMax), y(minP)); ctx.lineTo(x(xMin), y(minP)); ctx.closePath();
    ctx.fillStyle = grad; ctx.fill();

    // line
    ctx.strokeStyle = colors.accent; ctx.lineWidth = 2.5; ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.beginPath();
    data.forEach((d, i) => { const xx = x(d.x), yy = y(d.y); i ? ctx.lineTo(xx, yy) : ctx.moveTo(xx, yy); });
    ctx.stroke();

    // last-point marker
    const last = data[data.length - 1];
    ctx.fillStyle = colors.accent;
    ctx.beginPath(); ctx.arc(x(last.x), y(last.y), 4.5, 0, Math.PI * 2); ctx.fill();
  }, [series, colors, height, yUnit, yMin]);

  return <canvas ref={ref} style={{ width: "100%", height: height + "px", display: "block" }} />;
}

Object.assign(window, { LineChart });
