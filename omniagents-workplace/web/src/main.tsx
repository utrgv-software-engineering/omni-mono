import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter";
import "./index.css";
import { applyTheme } from "@omniagents/ui/themes";
import Root from "./Auth.tsx";

const dark = window.matchMedia("(prefers-color-scheme: dark)");
const follow = () => applyTheme(dark.matches ? "omni" : "teams-light");
follow();
dark.addEventListener("change", follow);

// iOS zooms into a focused field whose text is under 16px. With
// maximum-scale=1 it doesn't, and still lets people pinch-zoom (iOS ignores
// the limit for gestures); elsewhere it would block pinch-zoom, so iOS only.
const ios =
  /iPad|iPhone|iPod/.test(navigator.userAgent) ||
  (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const viewport = document.querySelector<HTMLMetaElement>('meta[name="viewport"]');
if (ios && viewport) viewport.content += ", maximum-scale=1";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
