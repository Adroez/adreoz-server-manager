/* Frontend tests for Phase 6: Minecraft version parsing and download logic. */

import { describe, it } from "node:test";
import assert from "node:assert/strict";

function javaForVersion(versionId) {
  const major = parseInt(versionId, 10);
  if (major === 1) {
    const minor = parseInt(versionId.split(".")[1] || "0", 10);
    if (minor >= 21) return "21";
    if (minor >= 18) return "17";
    return "16";
  }
  return "17";
}

function formatBytes(bytes) {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

describe("Phase 6 - Minecraft version (frontend logic)", () => {
  it("maps Java 17 for Minecraft 1.20.1", () => {
    assert.equal(javaForVersion("1.20.1"), "17");
  });

  it("maps Java 21 for Minecraft 1.21", () => {
    assert.equal(javaForVersion("1.21"), "21");
  });

  it("maps Java 16 for Minecraft 1.17", () => {
    assert.equal(javaForVersion("1.17.1"), "16");
  });

  it("formats download sizes", () => {
    assert.equal(formatBytes(0), "0 B");
    assert.equal(formatBytes(1024), "1 KB");
    assert.equal(formatBytes(1024 * 1024), "1 MB");
    assert.equal(formatBytes(1024 * 1024 * 1024), "1 GB");
  });

  it("validates version id parsing", () => {
    assert.equal(javaForVersion("1.20.6"), "17");
    assert.equal(javaForVersion("1.21.4"), "21");
  });
});
