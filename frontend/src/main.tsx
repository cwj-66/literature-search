import { initializeSession } from "./session";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { cssVars } from "./theme";
import { AppErrorBoundary } from "./components/AppErrorBoundary";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/pages.css";
import "./styles/scholar-workspace.css";
import "./styles/readability.css";

for (const [name, value] of Object.entries(cssVars)) document.documentElement.style.setProperty(name, value);

async function start() {
  await initializeSession();
  const { default: App } = await import("./App");
  createRoot(document.getElementById("root")!).render(<StrictMode><AppErrorBoundary><App /></AppErrorBoundary></StrictMode>);
}
void start().catch(error => {
  const root = document.getElementById("root")!;
  root.replaceChildren();
  const panel = document.createElement("main");
  panel.className = "app-startup";
  const text = document.createElement("p");
  text.setAttribute("role", "alert");
  text.textContent = error instanceof Error ? error.message : "加载失败，请重新加载。";
  const retry = document.createElement("button");
  retry.type = "button";
  retry.textContent = "重新加载";
  retry.onclick = () => window.location.reload();
  panel.append(text, retry);
  root.append(panel);
});
