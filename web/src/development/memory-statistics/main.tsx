/** 在 Vite 开发服务器中挂载 Memory 统计独立预览页。 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "lxgw-wenkai-webfont/style.css";
import "../../styles/index.css";
import { MemoryStatisticsPreview } from "./MemoryStatisticsPreview";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MemoryStatisticsPreview />
  </StrictMode>,
);
