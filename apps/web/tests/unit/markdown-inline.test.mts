import assert from "node:assert/strict";
import { test } from "node:test";

import { parseLink, tokenize } from "../../lib/markdown-inline.ts";

test("http links become links; anchors and other schemes stay text", () => {
  assert.deepEqual(parseLink("[Fetch API](https://developer.mozilla.org/fetch)"), {
    text: "Fetch API",
    href: "https://developer.mozilla.org/fetch",
  });
  assert.deepEqual(parseLink("[modern browsers](#browser-support)"), { text: "modern browsers", href: null });
  assert.deepEqual(parseLink("[x](javascript:alert(1))"), null);
  assert.deepEqual(parseLink("[x](javascript:alert)"), { text: "x", href: null });
  assert.deepEqual(parseLink("[x](data:text/html,hi)"), { text: "x", href: null });
  assert.equal(parseLink("plain text"), null);
});

test("evidence references, code and bold are separate tokens", () => {
  assert.deepEqual(tokenize("Uses `fetch` and **retries** (evidence:ev_dded8e76c630)."), [
    "Uses ",
    "`fetch`",
    " and ",
    "**retries**",
    " (",
    "evidence:ev_dded8e76c630",
    ").",
  ]);
});
