/* Regression tests for API error message extraction.
 *
 * Pydantic prefixes messages from custom field validators with
 * "Value error, ", which leaked into user-facing toasts (for example when a
 * JVM argument was rejected). The client strips that prefix.
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";

const { extractMessage } = await import("../core/api.js");

describe("api - error message extraction", () => {
  it("strips the pydantic Value error prefix", () => {
    const message = extractMessage(
      {
        detail: [
          {
            msg: "Value error, JVM argument '-javaagent:/tmp/x.jar' is not allowed for security reasons.",
          },
        ],
      },
      422
    );
    assert.equal(
      message,
      "JVM argument '-javaagent:/tmp/x.jar' is not allowed for security reasons."
    );
  });

  it("keeps a plain string detail unchanged", () => {
    assert.equal(
      extractMessage({ detail: "Server not found." }, 404),
      "Server not found."
    );
  });

  it("joins multiple validation messages", () => {
    const message = extractMessage(
      { detail: [{ msg: "Value error, first" }, { msg: "second" }] },
      422
    );
    assert.equal(message, "first second");
  });

  it("falls back to a generic message when no detail is present", () => {
    assert.equal(extractMessage(null, 500), "The server encountered an unexpected error.");
  });
});
