/** 从 CSS 值语法中提取会绕过 Trowel 设计 token 的具体颜色。 */

import colorNames from "color-name";
import valueParser from "postcss-value-parser";

const HEX_COLOR = /^#[\da-f]{3,8}$/iu;
const NAMED_COLORS = new Set(Object.keys(colorNames));
const DIRECT_COLOR_FUNCTIONS = new Set([
  "color",
  "device-cmyk",
  "hsl",
  "hsla",
  "hwb",
  "lab",
  "lch",
  "oklab",
  "oklch",
  "rgb",
  "rgba",
]);

/** 判断颜色函数是否使用 `from` 语法从另一个颜色派生。 */
function isRelativeColorFunction(node) {
  const firstValue = node.nodes.find(
    (child) => child.type !== "space" && child.type !== "comment",
  );
  return firstValue?.type === "word" && firstValue.value.toLowerCase() === "from";
}

/**
 * 返回值中的具体颜色，保留 token 派生函数并跳过 URL 内容。
 *
 * `color-mix()` 本身不是裸颜色；若其参数包含具体颜色，遍历仍会单独返回该参数。
 */
export function findColorLiterals(value, { includeNamedColors = false } = {}) {
  const colors = [];
  valueParser(value).walk((node) => {
    if (node.type === "function" && node.value.toLowerCase() === "url") {
      return false;
    }
    if (
      node.type === "function" &&
      DIRECT_COLOR_FUNCTIONS.has(node.value.toLowerCase())
    ) {
      if (isRelativeColorFunction(node)) return undefined;
      colors.push(valueParser.stringify(node).toLowerCase());
      return false;
    }
    if (node.type !== "word") return undefined;

    const word = node.value.toLowerCase();
    if (HEX_COLOR.test(word) || (includeNamedColors && NAMED_COLORS.has(word))) {
      colors.push(word);
    }
    return undefined;
  });
  return colors;
}
