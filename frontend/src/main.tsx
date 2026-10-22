import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { initializeDemo } from "./demo";
import { cssVars } from "./theme";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/pages.css";
import "./styles/scholar-workspace.css";
import "./styles/readability.css";

for (const [name, value] of Object.entries(cssVars)) document.documentElement.style.setProperty(name, value);

async function start() {
  await initializeDemo();
  const { default: App } = await import("./App");
  createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
}
void start().catch(error => {
  const root = document.getElementById("root")!;
  root.textContent = error instanceof Error ? error.message : "加载失败，请刷新重试";
});
