/** 在 Vite 开发服务器中挂载使用真实 API 的运行统计预览页。 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "lxgw-wenkai-webfont/style.css";
import "../../styles/index.css";
import { RuntimeStatisticsPreview } from "./RuntimeStatisticsPreview";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RuntimeStatisticsPreview />
  </StrictMode>,
);
