/** 挂载 Trowel 主应用并加载全局样式。 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import 'lxgw-wenkai-webfont/style.css'
import 'katex/dist/katex.min.css'
import './styles/index.css'
import App from './App.tsx'
import { initializePlatform } from './platform'

async function bootstrapRenderer() {
  await initializePlatform()
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
  requestAnimationFrame(() => {
    window.__TROWEL_RENDERER_READY__ = true
  })
}

void bootstrapRenderer().catch(() => {
  const root = document.getElementById('root')
  if (root) root.textContent = 'Trowel 无法连接后台服务，请重新启动应用。'
})
