// main.jsx — entry point: sets document title from config, mounts <App/> into #root.
// Must load LAST so every component/lib global is defined before first render.

(function () {
  const cfg = window.RAPP_CONFIG;
  if (cfg && cfg.brand && cfg.brand.title) document.title = cfg.brand.title;
  ReactDOM.createRoot(document.getElementById("root")).render(<App />);
})();
