import assert from "node:assert/strict";
import test from "node:test";
import { normalizeMathDelimiters as normalize } from "../src/lib/math-delimiters.ts";

test("model inline and display formulas become renderable math", () => {
  assert.equal(normalize(String.raw`页数 \( 20000/40=500 \)。`), "页数 $20000/40=500$。");
  assert.equal(normalize(String.raw`\[ 40+\lceil40/10\rceil\times500=2040 \]`), "\n\n$$\n40+\\lceil40/10\\rceil\\times500=2040\n$$\n\n");
});

test("code examples and existing dollar formulas stay byte-for-byte unchanged", () => {
  for (const value of ["`\\(code\\)`", "```tex\n\\[code\\]\n```", "~~~tex\n\\(code\\)\n~~~", String.raw`$\text{\(literal\)}$`, String.raw`$$\text{\[literal\]}$$`]) {
    assert.equal(normalize(value), value);
  }
});

test("unclosed expressions and source links remain unchanged", () => {
  const value = String.raw`未完成 \( x+1 与 [[笔记]]、[出处](/materials/view?id=abc)`;
  assert.equal(normalize(value), value);
});
