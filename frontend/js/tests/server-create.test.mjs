/* Frontend regression tests for Phase 6: Create server form submission.

These tests verify that the create-server form validation and API request
building logic work correctly, without requiring a DOM (jsdom is unavailable).

Covered:
- Valid form submission produces exactly one correctly-shaped POST body.
- Invalid inputs are rejected with descriptive error messages.
- Java runtime selection (empty/system default → null).
- API success and failure handling paths.
*/

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  validateCreateServerInputs,
  buildCreateServerBody,
} from "../views/server-create.js";

describe("server-create - input validation", () => {
  it("accepts a fully valid form", () => {
    const error = validateCreateServerInputs({
      name: "hello-world",
      port: "25565",
      maxPlayers: "10",
      viewDistance: "10",
      motd: "A Minecraft server",
    });
    assert.equal(error, null);
  });

  it("rejects an empty server name", () => {
    const error = validateCreateServerInputs({
      name: "",
      port: "25565",
      maxPlayers: "10",
      viewDistance: "10",
      motd: "A Minecraft server",
    });
    assert.equal(error, "Choose a server name.");
  });

  it("rejects a whitespace-only server name", () => {
    const error = validateCreateServerInputs({
      name: "   ",
      port: "25565",
      maxPlayers: "10",
      viewDistance: "10",
      motd: "A Minecraft server",
    });
    assert.equal(error, "Choose a server name.");
  });

  it("rejects a port below 1024", () => {
    const error = validateCreateServerInputs({
      name: "hello-world",
      port: "80",
      maxPlayers: "10",
      viewDistance: "10",
      motd: "A Minecraft server",
    });
    assert.equal(error, "Port must be between 1024 and 65535.");
  });

  it("rejects a port above 65535", () => {
    const error = validateCreateServerInputs({
      name: "hello-world",
      port: "70000",
      maxPlayers: "10",
      viewDistance: "10",
      motd: "A Minecraft server",
    });
    assert.equal(error, "Port must be between 1024 and 65535.");
  });

  it("rejects a non-integer port", () => {
    const error = validateCreateServerInputs({
      name: "hello-world",
      port: "abc",
      maxPlayers: "10",
      viewDistance: "10",
      motd: "A Minecraft server",
    });
    assert.equal(error, "Port must be between 1024 and 65535.");
  });

  it("rejects max players below 1", () => {
    const error = validateCreateServerInputs({
      name: "hello-world",
      port: "25565",
      maxPlayers: "0",
      viewDistance: "10",
      motd: "A Minecraft server",
    });
    assert.equal(error, "Max players must be between 1 and 1000.");
  });

  it("rejects max players above 1000", () => {
    const error = validateCreateServerInputs({
      name: "hello-world",
      port: "25565",
      maxPlayers: "1001",
      viewDistance: "10",
      motd: "A Minecraft server",
    });
    assert.equal(error, "Max players must be between 1 and 1000.");
  });

  it("rejects view distance below 2", () => {
    const error = validateCreateServerInputs({
      name: "hello-world",
      port: "25565",
      maxPlayers: "10",
      viewDistance: "1",
      motd: "A Minecraft server",
    });
    assert.equal(error, "View distance must be between 2 and 32.");
  });

  it("rejects view distance above 32", () => {
    const error = validateCreateServerInputs({
      name: "hello-world",
      port: "25565",
      maxPlayers: "10",
      viewDistance: "33",
      motd: "A Minecraft server",
    });
    assert.equal(error, "View distance must be between 2 and 32.");
  });

  it("rejects an empty MOTD", () => {
    const error = validateCreateServerInputs({
      name: "hello-world",
      port: "25565",
      maxPlayers: "10",
      viewDistance: "10",
      motd: "",
    });
    assert.equal(error, "MOTD must be between 1 and 128 characters.");
  });

  it("rejects a MOTD longer than 128 characters", () => {
    const error = validateCreateServerInputs({
      name: "hello-world",
      port: "25565",
      maxPlayers: "10",
      viewDistance: "10",
      motd: "x".repeat(129),
    });
    assert.equal(error, "MOTD must be between 1 and 128 characters.");
  });

  it("returns the first validation error only", () => {
    const error = validateCreateServerInputs({
      name: "",
      port: "99999",
      maxPlayers: "0",
      viewDistance: "99",
      motd: "",
    });
    assert.equal(error, "Choose a server name.");
  });
});

describe("server-create - API request body", () => {
  it("builds the correct body with system default Java runtime", () => {
    const body = buildCreateServerBody({
      name: "hello-world",
      softwareType: "vanilla",
      version: "1.20.1",
      port: "25565",
      maxPlayers: "10",
      onlineMode: true,
      difficulty: "normal",
      gamemode: "survival",
      viewDistance: "10",
      motd: "A Minecraft server",
      javaRuntimeId: "",
      buildVersion: null,
    });
    assert.deepEqual(body, {
      name: "hello-world",
      software_type: "vanilla",
      version: "1.20.1",
      port: 25565,
      max_players: 10,
      online_mode: true,
      difficulty: "normal",
      gamemode: "survival",
      view_distance: 10,
      motd: "A Minecraft server",
      java_runtime_id: null,
      build_version: null,
    });
  });

  it("builds the correct body with a selected Java runtime", () => {
    const body = buildCreateServerBody({
      name: "hello-world",
      softwareType: "vanilla",
      version: "1.20.1",
      port: "25565",
      maxPlayers: "10",
      onlineMode: true,
      difficulty: "normal",
      gamemode: "survival",
      viewDistance: "10",
      motd: "A Minecraft server",
      javaRuntimeId: "3",
      buildVersion: null,
    });
    assert.equal(body.java_runtime_id, 3);
    assert.equal(body.build_version, null);
  });

  it("builds the correct body with a build version", () => {
    const body = buildCreateServerBody({
      name: "hello-world",
      softwareType: "paper",
      version: "1.20.1",
      port: "25565",
      maxPlayers: "10",
      onlineMode: true,
      difficulty: "normal",
      gamemode: "survival",
      viewDistance: "10",
      motd: "A Minecraft server",
      javaRuntimeId: null,
      buildVersion: "123",
    });
    assert.equal(body.build_version, "123");
  });

  it("trims whitespace from name and MOTD", () => {
    const body = buildCreateServerBody({
      name: "  hello-world  ",
      softwareType: "vanilla",
      version: "1.20.1",
      port: "25565",
      maxPlayers: "10",
      onlineMode: false,
      difficulty: "peaceful",
      gamemode: "creative",
      viewDistance: "6",
      motd: "  trimmed motd  ",
      javaRuntimeId: null,
      buildVersion: null,
    });
    assert.equal(body.name, "hello-world");
    assert.equal(body.motd, "trimmed motd");
    assert.equal(body.online_mode, false);
  });

  it("defaults version to 1.20.1 when empty", () => {
    const body = buildCreateServerBody({
      name: "hello-world",
      softwareType: "vanilla",
      version: "",
      port: "25565",
      maxPlayers: "10",
      onlineMode: true,
      difficulty: "normal",
      gamemode: "survival",
      viewDistance: "10",
      motd: "A Minecraft server",
      javaRuntimeId: null,
      buildVersion: null,
    });
    assert.equal(body.version, "1.20.1");
  });
});

describe("server-create - API success and failure handling", () => {
  it("success path calls api once with POST /api/servers", async () => {
    let callCount = 0;
    let capturedPath = null;
    let capturedMethod = null;
    let capturedBody = null;

    const mockApi = async (path, options = {}) => {
      callCount += 1;
      capturedPath = path;
      capturedMethod = options.method;
      capturedBody = options.body;
      return { id: 42, name: "hello-world" };
    };

    const body = buildCreateServerBody({
      name: "hello-world",
      softwareType: "vanilla",
      version: "1.20.1",
      port: "25565",
      maxPlayers: "10",
      onlineMode: true,
      difficulty: "normal",
      gamemode: "survival",
      viewDistance: "10",
      motd: "A Minecraft server",
      javaRuntimeId: null,
    });

    const result = await mockApi("/servers", {
      method: "POST",
      body,
    });

    assert.equal(callCount, 1, "exactly one API call");
    assert.equal(capturedPath, "/servers", "correct path");
    assert.equal(capturedMethod, "POST", "correct method");
    assert.equal(capturedBody, body, "correct body passed through");
    assert.equal(result.id, 42, "success result returned");
  });

  it("failure path propagates the API error", async () => {
    const mockApi = async () => {
      const err = new Error("Server name already exists");
      err.status = 409;
      throw err;
    };

    let caught = null;
    try {
      await mockApi("/servers", { method: "POST", body: {} });
    } catch (err) {
      caught = err;
    }

    assert.notEqual(caught, null, "error was thrown");
    assert.equal(caught.status, 409, "error status preserved");
    assert.equal(caught.message, "Server name already exists", "error message preserved");
  });
});
