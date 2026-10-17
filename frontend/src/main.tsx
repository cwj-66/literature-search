import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { cssVars } from "./theme";
import "./styles/base.css";
import "./styles/views.css";

for (const [name, value] of Object.entries(cssVars)) document.documentElement.style.setProperty(name, value);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
