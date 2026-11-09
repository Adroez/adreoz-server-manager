/* Frontend tests for Phase 6: Java Runtime compatibility logic. */

import { describe, it } from "node:test";
import assert from "node:assert/strict";

// Mirrors backend/app/services/compat.py for cross-checking behavior.
const MINECRAFT_JAVA_REQUIREMENTS = {
  "1.17": 16,
  "1.17.1": 16,
  "1.18": 17,
  "1.18.1": 17,
  "1.18.2": 17,
  "1.19": 17,
  "1.19.1": 17,
  "1.19.2": 17,
  "1.19.3": 17,
  "1.19.4": 17,
  "1.20": 17,
  "1.20.1": 17,
  "1.20.2": 17,
  "1.20.3": 17,
  "1.20.4": 17,
  "1.20.5": 17,
  "1.20.6": 17,
  "1.21": 21,
  "1.21.1": 21,
  "1.21.2": 21,
  "1.21.3": 21,
  "1.21.4": 21,
};
const DEFAULT_JAVA_MAJOR = 17;

function requiredJavaMajor(minecraftVersion) {
  if (minecraftVersion in MINECRAFT_JAVA_REQUIREMENTS) {
    return MINECRAFT_JAVA_REQUIREMENTS[minecraftVersion];
  }
  return DEFAULT_JAVA_MAJOR;
}

function checkCompatibility(minecraftVersion, runtimeJavaMajor) {
  const required = requiredJavaMajor(minecraftVersion);
  if (runtimeJavaMajor === null || runtimeJavaMajor === undefined) {
    return {
      compatible: false,
      minecraftVersion,
      requiredJavaMajor: required,
      runtimeJavaMajor: null,
      message: `Java runtime version could not be determined. Minecraft ${minecraftVersion} requires Java ${required}.`,
    };
  }
  if (runtimeJavaMajor < required) {
    return {
      compatible: false,
      minecraftVersion,
      requiredJavaMajor: required,
      runtimeJavaMajor,
      message: `Java ${runtimeJavaMajor} is incompatible with Minecraft ${minecraftVersion}, which requires Java ${required} or newer.`,
    };
  }
  return {
    compatible: true,
    minecraftVersion,
    requiredJavaMajor: required,
    runtimeJavaMajor,
    message: `Java ${runtimeJavaMajor} is compatible with Minecraft ${minecraftVersion} (requires Java ${required}).`,
  };
}

describe("Phase 6 - Java compatibility (frontend mirror)", () => {
  it("recognises Minecraft 1.20.1 requires Java 17", () => {
    const result = checkCompatibility("1.20.1", 17);
    assert.equal(result.compatible, true);
    assert.equal(result.requiredJavaMajor, 17);
  });

  it("accepts newer Java for Minecraft 1.20.1", () => {
    const result = checkCompatibility("1.20.1", 25);
    assert.equal(result.compatible, true);
  });

  it("rejects Java 11 for Minecraft 1.20.1", () => {
    const result = checkCompatibility("1.20.1", 11);
    assert.equal(result.compatible, false);
    assert.equal(result.requiredJavaMajor, 17);
    assert.equal(result.runtimeJavaMajor, 11);
  });

  it("rejects unknown runtime version", () => {
    const result = checkCompatibility("1.20.1", null);
    assert.equal(result.compatible, false);
    assert.equal(result.runtimeJavaMajor, null);
  });

  it("defaults unknown Minecraft versions to Java 17", () => {
    const result = checkCompatibility("1.99.9", 17);
    assert.equal(result.compatible, true);
    assert.equal(result.requiredJavaMajor, 17);
  });

  it("maps Java 21 for Minecraft 1.21", () => {
    assert.equal(requiredJavaMajor("1.21"), 21);
  });
});
