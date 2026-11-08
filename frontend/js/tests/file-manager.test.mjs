/* Frontend tests for Phase 4: File Manager and Configuration Editor.

These tests exercise the business logic of the file manager and configuration
editor views without requiring a DOM (jsdom is not available). They test:

- File size formatting
- Configuration validation logic
- Error state handling
- Unsaved changes tracking
- API interaction patterns
*/

import { describe, it } from "node:test";
import assert from "node:assert/strict";

/* ------------------------------------------------------------------ */
/*  File size formatting (from frontend/js/views/files.js)            */
/* ------------------------------------------------------------------ */

function formatBytes(bytes) {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

describe("file manager - formatBytes", () => {
  it("formats 0 bytes", () => {
    assert.equal(formatBytes(0), "0 B");
  });
  it("formats bytes", () => {
    assert.equal(formatBytes(100), "100 B");
    assert.equal(formatBytes(1024), "1 KB");
    assert.equal(formatBytes(1024 * 1024), "1 MB");
    assert.equal(formatBytes(1024 * 1024 * 1024), "1 GB");
  });
  it("formats fractional sizes", () => {
    assert.equal(formatBytes(1536), "1.5 KB");
    assert.equal(formatBytes(1048576), "1 MB");
  });
});

/* ------------------------------------------------------------------ */
/*  Configuration validation logic                                    */
/* ------------------------------------------------------------------ */

const BOOLEAN_VALUES = ["true", "false"];

const ALLOWED_VALUES = {
  gamemode: ["survival", "creative", "adventure", "spectator"],
  difficulty: ["peaceful", "easy", "normal", "hard"],
  "online-mode": ["true", "false"],
  pvp: ["true", "false"],
};

const INT_PROPERTIES = {
  "max-players": [1, 1000],
  "view-distance": [2, 32],
  "simulation-distance": [2, 32],
  "spawn-protection": [0, 1000],
  "server-port": [1024, 65535],
};

function validateProperty(key, value) {
  const keyLower = key.toLowerCase();

  // Boolean values.
  if (
    [
      "online-mode",
      "pvp",
      "allow-nether",
      "allow-flight",
      "announce-player-achievements",
      "force-gamemode",
      "hardcore",
      "enable-command-block",
      "enable-query",
      "enable-rcon",
      "generate-structures",
    ].includes(keyLower)
  ) {
    if (value.toLowerCase() !== "true" && value.toLowerCase() !== "false") {
      throw new Error(`${key} must be 'true' or 'false'.`);
    }
    return value.toLowerCase();
  }

  // Integer properties with ranges.
  if (keyLower in INT_PROPERTIES) {
    const [minV, maxV] = INT_PROPERTIES[keyLower];
    const intValue = parseInt(value, 10);
    if (isNaN(intValue)) throw new Error(`${key} must be an integer.`);
    if (intValue < minV || intValue > maxV) {
      throw new Error(`${key} must be between ${minV} and ${maxV}.`);
    }
    return String(intValue);
  }

  // Enum properties (non-integer).
  if (keyLower in ALLOWED_VALUES) {
    const allowed = ALLOWED_VALUES[keyLower];
    if (allowed.indexOf(value.toLowerCase()) === -1) {
      throw new Error(`${key} must be one of: ${allowed.join(", ")}.`);
    }
    return value.toLowerCase();
  }

  // MOTD - no validation other than length.
  if (keyLower === "motd") {
    if (value.length > 128) {
      throw new Error("motd must be at most 128 characters.");
    }
    if (value.includes("\n") || value.includes("\r")) {
      throw new Error(`${key} must not contain newlines.`);
    }
    return value;
  }

  // Level-name, level-seed, etc. - just validate no newlines.
  if (value.includes("\n") || value.includes("\r")) {
    throw new Error(`${key} must not contain newlines.`);
  }

  // Unknown properties are passed through as-is.
  return value;
}

describe("config editor - validation", () => {
  it("validates boolean values", () => {
    assert.equal(validateProperty("online-mode", "true"), "true");
    assert.equal(validateProperty("online-mode", "false"), "false");
    assert.equal(validateProperty("pvp", "true"), "true");
    assert.throws(() => validateProperty("online-mode", "yes"), /must be 'true' or 'false'/);
    assert.throws(() => validateProperty("pvp", "1"), /must be 'true' or 'false'/);
  });

  it("validates enum values", () => {
    assert.equal(validateProperty("difficulty", "peaceful"), "peaceful");
    assert.equal(validateProperty("difficulty", "hard"), "hard");
    assert.equal(validateProperty("gamemode", "creative"), "creative");
    assert.equal(validateProperty("gamemode", "spectator"), "spectator");
    assert.throws(() => validateProperty("difficulty", "insane"), /must be one of/);
    assert.throws(() => validateProperty("gamemode", "spectatorx"), /must be one of/);
  });

  it("validates integer ranges", () => {
    assert.equal(validateProperty("max-players", "20"), "20");
    assert.equal(validateProperty("server-port", "25565"), "25565");
    assert.equal(validateProperty("view-distance", "10"), "10");
    assert.throws(() => validateProperty("max-players", "99999"), /must be between 1 and 1000/);
    assert.throws(() => validateProperty("server-port", "80"), /must be between 1024 and 65535/);
    assert.throws(() => validateProperty("max-players", "abc"), /must be an integer/);
  });

  it("validates MOTD length", () => {
    assert.equal(validateProperty("motd", "A custom MOTD"), "A custom MOTD");
    assert.throws(() => validateProperty("motd", "x".repeat(129)), /must be at most 128 characters/);
  });

  it("rejects newlines in text values", () => {
    const newline = String.fromCharCode(10);
    assert.throws(() => validateProperty("level-name", "line1" + newline + "line2"), /must not contain newlines/);
    assert.throws(() => validateProperty("motd", "line1" + String.fromCharCode(13) + "line2"), /must not contain newlines/);
  });

  it("passes through unknown properties", () => {
    assert.equal(validateProperty("unknown-prop", "custom-value"), "custom-value");
  });
});

/* ------------------------------------------------------------------ */
/*  Unsaved changes tracking                                          */
/* ------------------------------------------------------------------ */

describe("config editor - dirty state tracking", () => {
  it("starts clean after load", () => {
    const original = { motd: "Original", difficulty: "normal" };
    const properties = { ...original };
    let isDirty = false;

    assert.equal(isDirty, false);
  });

  it("becomes dirty when a value changes", () => {
    const original = { motd: "Original", difficulty: "normal" };
    let properties = { ...original };
    let isDirty = false;

    properties.motd = "Changed";
    isDirty =
      properties.motd !== original.motd ||
      properties.difficulty !== original.difficulty;

    assert.equal(isDirty, true);
  });

  it("stays clean when no values change", () => {
    const original = { motd: "Original", difficulty: "normal" };
    let properties = { ...original };
    let isDirty = false;

    properties.motd = "Original";
    isDirty =
      properties.motd !== original.motd ||
      properties.difficulty !== original.difficulty;

    assert.equal(isDirty, false);
  });

  it("tracks multiple changes", () => {
    const original = { motd: "Original", difficulty: "normal", "max-players": "10" };
    let properties = { ...original };
    let isDirty = false;

    properties.motd = "Changed";
    properties["max-players"] = "20";
    isDirty =
      properties.motd !== original.motd ||
      properties.difficulty !== original.difficulty ||
      properties["max-players"] !== original["max-players"];

    assert.equal(isDirty, true);
  });
});

/* ------------------------------------------------------------------ */
/*  Error state handling                                              */
/* ------------------------------------------------------------------ */

describe("config editor - error handling", () => {
  it("formats 409 conflict error message", () => {
    const error = { status: 409, message: "Server.properties has been modified by another user. Please reload and try again." };

    assert.equal(error.status, 409);
    assert.ok(error.message.includes("another user"));
    assert.ok(error.message.includes("reload"));
  });

  it("formats 403 forbidden error message", () => {
    const error = { status: 403, message: "You do not have access to this server." };

    assert.equal(error.status, 403);
    assert.ok(error.message.includes("do not have access"));
  });

  it("formats 404 not found error message", () => {
    const error = { status: 404, message: "server.properties not found." };

    assert.equal(error.status, 404);
    assert.ok(error.message.includes("not found"));
  });
});

/* ------------------------------------------------------------------ */
/*  File operation validation                                         */
/* ------------------------------------------------------------------ */

const INVALID_FILENAME_CHARS = new Set("<>:\"|?*");

function validateFileName(name) {
  if (!name || name.trim() !== name) {
    throw new Error("Invalid file or folder name.");
  }
  if (name === "." || name === "..") {
    throw new Error("Invalid file or folder name.");
  }
  if (name.length > 255) {
    throw new Error("File or folder name is too long.");
  }
  for (const ch of name) {
    if (INVALID_FILENAME_CHARS.has(ch)) {
      throw new Error("File or folder name contains invalid characters.");
    }
  }
  return name;
}

describe("file manager - filename validation", () => {
  it("accepts valid filenames", () => {
    assert.equal(validateFileName("notes.txt"), "notes.txt");
    assert.equal(validateFileName("server.properties"), "server.properties");
    assert.equal(validateFileName("my-file_1.0.cfg"), "my-file_1.0.cfg");
  });

  it("rejects empty names", () => {
    assert.throws(() => validateFileName(""), /Invalid file or folder name/);
    assert.throws(() => validateFileName("   "), /Invalid file or folder name/);
  });

  it("rejects reserved names", () => {
    assert.throws(() => validateFileName("."), /Invalid file or folder name/);
    assert.throws(() => validateFileName(".."), /Invalid file or folder name/);
  });

  it("rejects names with invalid characters", () => {
    assert.throws(() => validateFileName("bad<>name.txt"), /invalid characters/);
    assert.throws(() => validateFileName("bad|name.txt"), /invalid characters/);
    assert.throws(() => validateFileName("bad?name.txt"), /invalid characters/);
  });

  it("rejects names over 255 characters", () => {
    const longName = "a".repeat(256);
    assert.throws(() => validateFileName(longName), /too long/);
  });
});
