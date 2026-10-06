"use client";

import { useEffect, useState } from "react";

// Shows "A new version is ready" when the live site is newer than this page,
// checked every 5 minutes and whenever the tab comes back to the foreground.
// A tab that was in the background reloads by itself when you return to it.
const MINE = process.env.NEXT_PUBLIC_BUILD_ID || "dev";

export function UpdateBanner() {
  const [newer, setNewer] = useState(false);

  useEffect(() => {
    if (MINE === "dev") return;
    let stale = false;
    const check = async () => {
      try {
        const r = await fetch(`/version.json?t=${Date.now()}`, { cache: "no-store" });
        const { build } = await r.json();
        if (build && build !== MINE) {
          stale = true;
          setNewer(true);
        }
      } catch { /* offline: try again later */ }
    };
    const onShow = () => {
      if (document.hidden) return;
      if (stale) window.location.reload();     // came back to an outdated tab: just load the new one
      else check();
    };
    check();
    const t = setInterval(check, 5 * 60 * 1000);
    document.addEventListener("visibilitychange", onShow);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", onShow); };
  }, []);

  if (!newer) return null;
  return (
    <div style={{ position: "fixed", left: 16, right: 16, bottom: "calc(16px + env(safe-area-inset-bottom, 0px))", zIndex: 200,
                  background: "#064e3b", border: "1px solid #10b981", borderRadius: 10, padding: "10px 14px",
                  display: "flex", gap: 10, alignItems: "center", justifyContent: "space-between", flexWrap: "wrap",
                  boxShadow: "0 10px 25px rgba(0,0,0,0.5)", fontSize: 13 }}>
      <span>✨ A new version of Scrapper is ready.</span>
      <button className="primary" onClick={() => window.location.reload()}>Reload</button>
    </div>
  );
}
