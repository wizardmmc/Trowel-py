/** 在 Vite 开发服务器中挂载 Agent 统计独立预览页。 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "lxgw-wenkai-webfont/style.css";
import "../../styles/index.css";
import { AgentStatisticsPreview } from "./AgentStatisticsPreview";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AgentStatisticsPreview />
  </StrictMode>,
);
