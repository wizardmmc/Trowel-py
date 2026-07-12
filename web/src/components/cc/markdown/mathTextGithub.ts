/**
 * Inline-math tokenizer — fork of `micromark-extension-math@3.1.0`
 * `lib/math-text.js`, with one behavioral patch.
 *
 * Why this fork exists: stock remark-math lets a `$` close inline math even
 * when whitespace precedes it, so `价格 $100 ... $5 优惠` pairs the two `$` and
 * swallows everything between (including Chinese) into a KaTeX span. The
 * upstream maintainer considers this intended and wontfix
 * (https://github.com/micromark/micromark-extension-math/issues/6), recommending
 * `singleDollarTextMath:false` instead. We want to keep inline `$x$` rendering,
 * so we adopt the GitHub/Obsidian rule: a `$` only closes math if the character
 * before it is NOT whitespace.
 *
 * Patch (in `between()`): track `lastWasSpace`; reject a closing `$` when it is
 * true. Everything else is verbatim from upstream so behavior stays aligned.
 *
 * Upgrade note: when bumping remark-math / micromark-extension-math, diff
 * upstream `lib/math-text.js` against this file and re-apply the `lastWasSpace`
 * patch. The block-math tokenizer (`math-flow.js`) is reused as-is via
 * `remarkMathGithub.ts`.
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

/**
 * Build a GitHub-style inline-math `Construct`.
 *
 * Args:
 *   options: micromark-extension-math options; `singleDollarTextMath` honored.
 *
 * Returns:
 *   A micromark text construct for `$...$` that rejects a closing `$` preceded
 *   by whitespace.
 */
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
    // PATCH: was the last consumed char inside the span whitespace? A closing
    // `$` preceded by whitespace is rejected so currency `$100 … $5` doesn't
    // pair into one math span.
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

      // Not enough markers in the sequence.
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
        // PATCH: closing `$` preceded by whitespace is not a math close.
        if (lastWasSpace) {
          return nok(code);
        }
        token = effects.enter("mathTextSequence");
        size = 0;
        return sequenceClose(code);
      }

      // Tabs don't work, and virtual spaces don't make sense.
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

      // Data.
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
      // More.
      if (code === 36) {
        effects.consume(code);
        size++;
        return sequenceClose;
      }

      // Done!
      if (size === sizeOpen) {
        effects.exit("mathTextSequence");
        effects.exit("mathText");
        return ok(code);
      }

      // More or less accents: mark as data.
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

  // If we start and end with an EOL or a space.
  if (
    (events[headEnterIndex][1].type === "lineEnding" ||
      events[headEnterIndex][1].type === "space") &&
    (events[tailExitIndex][1].type === "lineEnding" ||
      events[tailExitIndex][1].type === "space")
  ) {
    index = headEnterIndex;

    // And we have data.
    while (++index < tailExitIndex) {
      if (events[index][1].type === "mathTextData") {
        // Then we have padding.
        events[tailExitIndex][1].type = "mathTextPadding";
        events[headEnterIndex][1].type = "mathTextPadding";
        headEnterIndex += 2;
        tailExitIndex -= 2;
        break;
      }
    }
  }

  // Merge adjacent spaces and data.
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
  // If there is a previous code, there will always be a tail.
  return (
    code !== 36 ||
    this.events[this.events.length - 1][1].type === "characterEscape"
  );
};
