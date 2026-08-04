import '@testing-library/jest-dom/vitest'

// jsdom 尚未实现 Radix Select 在真实浏览器中使用的 Pointer Capture API。
if (typeof Element !== 'undefined') {
  for (const method of [
    'hasPointerCapture',
    'setPointerCapture',
    'releasePointerCapture',
  ] as const) {
    if (method in Element.prototype) continue
    Object.defineProperty(Element.prototype, method, {
      configurable: true,
      value: method === 'hasPointerCapture' ? () => false : () => undefined,
    })
  }
}

// Radix 聚焦选项时会滚动列表；测试环境只需保留无副作用接口。
if (typeof HTMLElement !== 'undefined' && !HTMLElement.prototype.scrollIntoView) {
  HTMLElement.prototype.scrollIntoView = () => undefined
}
