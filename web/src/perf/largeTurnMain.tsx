/** 挂载隔离的大回合性能回放页面。 */

import { createRoot } from "react-dom/client";

import "../styles/index.css";
import { LargeTurnHarness } from "./largeTurnHarness";
import { installLargeTurnFetchMock } from "./largeTurnFetchMock";
import "./largeTurnHarness.css";

installLargeTurnFetchMock();

createRoot(document.getElementById("root")!).render(<LargeTurnHarness />);
