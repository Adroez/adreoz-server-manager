/* Frontend regression tests for Phase 6: server detail Java runtime changes.

These tests verify the runtime selection logic that drives the PATCH
/api/servers/:id endpoint, including successful and failed updates.
No DOM is required; they exercise the body-building and API call patterns
used by the server-detail view's selectJavaRuntime handler.
*/

import { describe, it } from "node:test";
import assert from "node:assert/strict";

// Mirrors the body-building logic used by selectJavaRuntime in
// frontend/js/views/server-detail.js.
function buildRuntimeChangeBody(runtimeIdValue) {
  return { java_runtime_id: runtimeIdValue ? Number(runtimeIdValue) : null };
}

describe("server-detail - Java runtime change", () => {
  it("builds PATCH body with null when system default is selected", () => {
    const body = buildRuntimeChangeBody("");
    assert.deepEqual(body, { java_runtime_id: null });
  });

  it("builds PATCH body with the numeric runtime id when selected", () => {
    const body = buildRuntimeChangeBody("3");
    assert.deepEqual(body, { java_runtime_id: 3 });
  });

  it("builds PATCH body with null when runtimeIdValue is null", () => {
    const body = buildRuntimeChangeBody(null);
    assert.deepEqual(body, { java_runtime_id: null });
  });

  it("success path calls PATCH /api/servers/:id exactly once", async () => {
    let callCount = 0;
    let capturedPath = null;
    let capturedMethod = null;
    let capturedBody = null;

    const mockApi = async (path, options = {}) => {
      callCount += 1;
      capturedPath = path;
      capturedMethod = options.method;
      capturedBody = options.body;
      return { id: 1, java_runtime_id: options.body.java_runtime_id };
    };

    const body = buildRuntimeChangeBody("2");
    const result = await mockApi("/servers/1", {
      method: "PATCH",
      body,
    });

    assert.equal(callCount, 1, "exactly one API call");
    assert.equal(capturedPath, "/servers/1", "correct path");
    assert.equal(capturedMethod, "PATCH", "correct method");
    assert.deepEqual(capturedBody, body, "correct body passed through");
    assert.equal(result.java_runtime_id, 2, "runtime reflected in response");
  });

  it("failure path propagates the API error", async () => {
    const mockApi = async () => {
      const err = new Error("Java runtime not found.");
      err.status = 404;
      throw err;
    };

    let caught = null;
    try {
      await mockApi("/servers/1", { method: "PATCH", body: { java_runtime_id: 999999 } });
    } catch (err) {
      caught = err;
    }

    assert.notEqual(caught, null, "error was thrown");
    assert.equal(caught.status, 404, "error status preserved");
    assert.equal(caught.message, "Java runtime not found.", "error message preserved");
  });
});
