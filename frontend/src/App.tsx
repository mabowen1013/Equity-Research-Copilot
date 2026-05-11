import { useEffect, useState } from "react";

import { fetchHealthStatus } from "./api/health";
import "./styles.css";

export function App() {
  const [apiStatus, setApiStatus] = useState("checking");

  useEffect(() => {
    let isMounted = true;

    fetchHealthStatus()
      .then((health) => {
        if (isMounted) {
          setApiStatus(health.status);
        }
      })
      .catch(() => {
        if (isMounted) {
          setApiStatus("unavailable");
        }
      });

    return () => {
      isMounted = false;
    };
  }, []);

  return (
    <main className="app-shell">
      <section className="workspace-panel" aria-labelledby="app-title">
        <p className="eyebrow">Equity Research Copilot</p>
        <h1 id="app-title">Research workspace</h1>
        <div className="status-row" aria-live="polite">
          <span className={`status-dot status-dot--${apiStatus}`} />
          <span>Backend health: {apiStatus}</span>
        </div>
      </section>
    </main>
  );
}
