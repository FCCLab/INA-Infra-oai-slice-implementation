// charts/useCanvas.jsx — shared hi-DPI canvas hook with ResizeObserver repaint
// Exposes (global + window): useCanvas

function useCanvas(draw, deps, sizeElRef) {
  const ref = React.useRef(null);
  React.useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const paint = () => {
      const box = sizeElRef?.current || cv;
      const w = box.clientWidth;
      const h = box.clientHeight;
      if (w < 1 || h < 1) return;
      if (sizeElRef?.current && cv !== box) {
        cv.style.width = w + "px";
        cv.style.height = h + "px";
      }
      const dpr = window.devicePixelRatio || 1;
      cv.width = Math.max(1, Math.round(w * dpr));
      cv.height = Math.max(1, Math.round(h * dpr));
      const ctx = cv.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      draw(ctx, w, h);
    };
    paint();
    const ro = new ResizeObserver(paint);
    ro.observe(sizeElRef?.current || cv);
    return () => ro.disconnect();
  }, deps);
  return ref;
}

Object.assign(window, { useCanvas });
