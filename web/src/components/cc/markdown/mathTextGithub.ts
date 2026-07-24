/**
 * 基于 `micromark-extension-math@3.1.0/lib/math-text.js` 的受控 fork。
 * 上游允许空白前的 `$` 闭合，导致普通价格文本被吞入 KaTeX，且明确不修：
 * https://github.com/micromark/micromark-extension-math/issues/6
 * 本文件只用 `lastWasSpace` 拒绝这类闭合；升级依赖时必须与上游重新比对。
 */
import type {
  Code,
  Construct,
  Event,
  Previous,
  Resolver,
  State,
  Token,
  Tokenizer,
} from "micromark-util-types";
import { markdownLineEnding } from "micromark-util-character";
import type { Options } from "micromark-extension-math";

export function mathTextGithub(options?: Options | null): Construct {
  const options_ = options ?? {};
  let single = options_.singleDollarTextMath;
  if (single === null || single === undefined) {
    single = true;
  }
  const tokenizeMathText: Tokenizer = function (effects, ok, nok) {
    let sizeOpen = 0;
    let size: number;
    let token: Token;
    let lastWasSpace = false;
    return start;

    function start(code: Code): State | undefined {
      effects.enter("mathText");
      effects.enter("mathTextSequence");
      return sequenceOpen(code);
    }

    function sequenceOpen(code: Code): State | undefined {
      if (code === 36) {
        effects.consume(code);
        sizeOpen++;
        return sequenceOpen;
      }

      if (sizeOpen < 2 && !single) {
        return nok(code);
      }
      effects.exit("mathTextSequence");
      return between(code);
    }

    function between(code: Code): State | undefined {
      if (code === null) {
        return nok(code);
      }
      if (code === 36) {
        if (lastWasSpace) {
          return nok(code);
        }
        token = effects.enter("mathTextSequence");
        size = 0;
        return sequenceClose(code);
      }

      if (code === 32) {
        effects.enter("space");
        effects.consume(code);
        effects.exit("space");
        lastWasSpace = true;
        return between;
      }
      if (markdownLineEnding(code)) {
        effects.enter("lineEnding");
        effects.consume(code);
        effects.exit("lineEnding");
        lastWasSpace = true;
        return between;
      }

      lastWasSpace = false;
      effects.enter("mathTextData");
      return data(code);
    }

    function data(code: Code): State | undefined {
      if (code === null || code === 32 || code === 36 || markdownLineEnding(code)) {
        effects.exit("mathTextData");
        return between(code);
      }
      effects.consume(code);
      return data;
    }

    function sequenceClose(code: Code): State | undefined {
      if (code === 36) {
        effects.consume(code);
        size++;
        return sequenceClose;
      }

      if (size === sizeOpen) {
        effects.exit("mathTextSequence");
        effects.exit("mathText");
        return ok(code);
      }

      token.type = "mathTextData";
      return data(code);
    }
  };

  return {
    tokenize: tokenizeMathText,
    resolve: resolveMathText,
    previous,
    name: "mathText",
  };
}

const resolveMathText: Resolver = function (events: Event[]): Event[] {
  let tailExitIndex = events.length - 4;
  let headEnterIndex = 3;
  let index: number;
  let enter: number | undefined;

  if (
    (events[headEnterIndex][1].type === "lineEnding" ||
      events[headEnterIndex][1].type === "space") &&
    (events[tailExitIndex][1].type === "lineEnding" ||
      events[tailExitIndex][1].type === "space")
  ) {
    index = headEnterIndex;

    while (++index < tailExitIndex) {
      if (events[index][1].type === "mathTextData") {
        events[tailExitIndex][1].type = "mathTextPadding";
        events[headEnterIndex][1].type = "mathTextPadding";
        headEnterIndex += 2;
        tailExitIndex -= 2;
        break;
      }
    }
  }

  index = headEnterIndex - 1;
  tailExitIndex++;
  while (++index <= tailExitIndex) {
    if (enter === undefined) {
      if (index !== tailExitIndex && events[index][1].type !== "lineEnding") {
        enter = index;
      }
    } else if (index === tailExitIndex || events[index][1].type === "lineEnding") {
      events[enter][1].type = "mathTextData";
      if (index !== enter + 2) {
        events[enter][1].end = events[index - 1][1].end;
        events.splice(enter + 2, index - enter - 2);
        tailExitIndex -= index - enter - 2;
        index = enter + 2;
      }
      enter = undefined;
    }
  }
  return events;
};

const previous: Previous = function (code: Code): boolean {
  return (
    code !== 36 ||
    this.events[this.events.length - 1][1].type === "characterEscape"
  );
};
