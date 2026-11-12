/* DOM-level regression tests for the Server Details view.
 *
 * These tests mount the real view module in a jsdom document and mock only the
 * network boundary (`fetch`, which `core/api.js` wraps). No rendering logic or
 * markup is recreated here, so a scope/ReferenceError, a lost disabled state,
 * or a broken control silently regresses the suite.
 *
 * The historical bug this guards against:
 *   ReferenceError: startDisabled is not defined
 * where a block-scoped value used by the Controls card was declared inside the
 * header-action branch, leaving the page stuck on its loading skeleton.
 */

import { JSDOM } from "jsdom";
import { beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";

const dom = new JSDOM("<!doctype html><html><head></head><body></body></html>", {
  url: "http://localhost/",
  pretendToBeVisual: true,
});

// The view and its helpers resolve `document`, `window` and `Node` from the
// global scope, exactly like they do in the browser.
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.Node = dom.window.Node;
globalThis.Event = dom.window.Event;
globalThis.CustomEvent = dom.window.CustomEvent;

// Toast dismissal timers are irrelevant to these assertions and would keep the
// jsdom timer queue alive after the suite ends. Notifications still render.
dom.window.setTimeout = () => 0;
dom.window.clearTimeout = () => {};

const { renderServerDetail } = await import("../views/server-detail.js");
const { actionButton } = await import("../components/ui.js");
const { setSession } = await import("../core/store.js");

const INSTALL_TOOLTIP =
  "Server software is not installed. Install software before starting.";

const RUNTIMES = [
  { id: 1, name: "Temurin", version: "21.0.5", major_version: 21 },
  { id: 2, name: "Temurin", version: "17.0.13", major_version: 17 },
];

/** A realistic payload matching the backend ServerOut schema. */
function makeServer(overrides = {}) {
  return {
    id: 5,
    name: "Help",
    unique_id: "srv_help0001",
    software_type: "fabric",
    version: "26.3",
    software_version: "0.16.9",
    install_status: "completed",
    install_error: null,
    java_runtime_id: null,
    status: "stopped",
    port: 25565,
    max_players: 20,
    online_mode: true,
    difficulty: "normal",
    gamemode: "survival",
    view_distance: 10,
    motd: "A Minecraft Server",
    directory: "/srv/adreoz/servers/help",
    created_at: "2026-11-11T17:34:53Z",
    started_at: null,
    stopped_at: null,
    ...overrides,
  };
}

/** Minimal Response lookalike: `api()` reads only status, ok and text(). */
function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async text() {
      return body === null || body === undefined ? "" : JSON.stringify(body);
    },
  };
}

/** Installs a fetch mock that routes on `${METHOD} ${path}` and records calls. */
function mockFetch(routes) {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const method = (options.method || "GET").toUpperCase();
    calls.push({
      url,
      method,
      body: options.body ?? null,
      headers: options.headers ?? {},
    });
    const handler = routes[`${method} ${url}`];
    if (!handler) {
      return jsonResponse({ detail: `No mock route for ${method} ${url}` }, 500);
    }
    return typeof handler === "function" ? handler() : handler;
  };
  return calls;
}

function routesFor(server, { runtimes = RUNTIMES, extra = {} } = {}) {
  return {
    [`GET /api/servers/${server.id}`]: jsonResponse(server),
    "GET /api/java/runtimes": jsonResponse({ items: runtimes }),
    ...extra,
  };
}

function useApi(server, options = {}) {
  return mockFetch(routesFor(server, options));
}

function newView() {
  const view = document.createElement("main");
  document.body.append(view);
  return view;
}

/** Mirrors the router's context contract: only isCurrent() is used by the view. */
function createContext() {
  return { isCurrent: () => true };
}

async function renderPage(server, { extra, runtimes } = {}) {
  window.location.hash = `#/servers/${server.id}`;
  const calls = useApi(server, { extra, runtimes });
  const view = newView();
  await renderServerDetail(view, createContext());
  return { view, calls };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

function headerActions(view) {
  return Array.from(view.querySelectorAll(".page-actions button"));
}

function headerAction(view, label) {
  return (
    headerActions(view).find((button) => button.textContent.trim() === label) ??
    null
  );
}

function cardTitles(view) {
  return Array.from(view.querySelectorAll(".card-title")).map((node) =>
    node.textContent.trim()
  );
}

function controlsCard(view) {
  return (
    Array.from(view.querySelectorAll(".card")).find(
      (card) => card.querySelector(".card-title")?.textContent.trim() === "Controls"
    ) ?? null
  );
}

function controlsButtons(view) {
  const card = controlsCard(view);
  assert.ok(card, "Controls card should be rendered");
  return Array.from(card.querySelectorAll(".card-body button"));
}

function controlButton(view, label) {
  return (
    controlsButtons(view).find((button) => button.textContent.trim() === label) ??
    null
  );
}

function infoValue(view, label) {
  const row = Array.from(view.querySelectorAll(".info-row")).find(
    (node) => node.querySelector(".info-label")?.textContent.trim() === label
  );
  return row ? row.querySelector(".info-value")?.textContent.trim() ?? null : null;
}

function skeletonCount(view) {
  return view.querySelectorAll(".skeleton").length;
}

function busyCount(view) {
  return view.querySelectorAll('[aria-busy="true"]').length;
}

beforeEach(() => {
  document.body.replaceChildren();
  window.history.replaceState(null, "", "/");
  setSession({
    user: { id: 1, username: "admin", is_admin: true },
    csrf_token: "test-csrf-token",
    expires_at: null,
  });
});

/* ------------------------------------------------------------------------- */
/* Server states and control matrices                                        */
/* ------------------------------------------------------------------------- */

const STATE_CASES = [
  {
    title: "stopped server with completed install",
    overrides: { status: "stopped", install_status: "completed" },
    statusText: "Stopped",
    installText: "Completed",
    header: {
      start: { label: "Start", disabled: false, title: "Start" },
      stop: false,
      console: false,
      install: null,
      delete: true,
    },
    controls: { label: "Start server", disabled: false },
    restartDisabled: false,
  },
  {
    title: "running server with completed install",
    overrides: { status: "running", install_status: "completed" },
    statusText: "Running",
    installText: "Completed",
    header: {
      start: null,
      stop: true,
      console: true,
      install: null,
      delete: false,
    },
    controls: { label: "Stop server", disabled: false },
    restartDisabled: false,
  },
  {
    title: "starting server with completed install",
    overrides: { status: "starting", install_status: "completed" },
    statusText: "Starting",
    installText: "Completed",
    header: {
      start: { label: "Start", disabled: true, title: "Start" },
      stop: false,
      console: false,
      install: null,
      delete: false,
    },
    controls: { label: "Start server", disabled: true },
    restartDisabled: true,
  },
  {
    title: "stopping server with completed install",
    overrides: { status: "stopping", install_status: "completed" },
    statusText: "Stopping",
    installText: "Completed",
    header: {
      start: { label: "Start", disabled: true, title: "Start" },
      stop: false,
      console: false,
      install: null,
      delete: false,
    },
    controls: { label: "Start server", disabled: true },
    restartDisabled: true,
  },
  {
    title: "stopped server with installation pending",
    overrides: { status: "stopped", install_status: "pending" },
    statusText: "Stopped",
    installText: "Pending",
    header: {
      start: { label: "Start", disabled: true, title: INSTALL_TOOLTIP },
      stop: false,
      console: false,
      install: { label: "Install Software" },
      delete: true,
    },
    controls: { label: "Start server", disabled: true },
    restartDisabled: true,
  },
  {
    title: "stopped server with installation failed",
    overrides: {
      status: "stopped",
      install_status: "failed",
      install_error: "Fabric loader download failed.",
    },
    statusText: "Stopped",
    installText: "Failed",
    header: {
      start: { label: "Start", disabled: true, title: INSTALL_TOOLTIP },
      stop: false,
      console: false,
      install: { label: "Retry Install" },
      delete: true,
    },
    controls: { label: "Start server", disabled: true },
    restartDisabled: true,
  },
  {
    title: "errored server with completed install",
    overrides: { status: "error", install_status: "completed" },
    statusText: "Error",
    installText: "Completed",
    header: {
      start: { label: "Retry", disabled: false, title: "Retry" },
      stop: false,
      console: false,
      install: null,
      delete: true,
    },
    controls: { label: "Start server", disabled: false },
    restartDisabled: false,
  },
];

describe("server-detail DOM - server states", () => {
  for (const testCase of STATE_CASES) {
    it(`renders a ${testCase.title}`, async () => {
      const server = makeServer(testCase.overrides);
      const { view } = await renderPage(server);

      // The loading skeleton must be fully replaced by the real page.
      assert.equal(skeletonCount(view), 0, "loading skeleton must be gone");
      assert.equal(busyCount(view), 0, "view must not stay aria-busy");

      // Page header and summary text come from the real pageHeader component.
      assert.equal(view.querySelector(".page-head h2").textContent, server.name);
      assert.equal(
        view.querySelector(".eyebrow").textContent,
        `Server · ${server.name}`
      );
      assert.equal(
        view.querySelector(".page-desc").textContent,
        `${server.software_type} ${server.version} · ${server.status}`
      );

      // All three production cards render, exactly once each.
      assert.deepEqual(cardTitles(view), [
        "Configuration",
        "Controls",
        "Recent logs",
      ]);

      // Configuration details are populated from the API payload.
      assert.equal(infoValue(view, "Name"), server.name);
      assert.equal(infoValue(view, "Unique ID"), server.unique_id);
      assert.equal(
        infoValue(view, "Software"),
        `${server.software_type} ${server.version}`
      );
      assert.equal(infoValue(view, "Status"), testCase.statusText);
      assert.equal(infoValue(view, "Install Status"), testCase.installText);
      assert.equal(infoValue(view, "Port"), String(server.port));
      assert.equal(infoValue(view, "Max players"), String(server.max_players));
      assert.equal(infoValue(view, "Java runtime"), "System default");

      if (testCase.overrides.install_error) {
        assert.equal(
          infoValue(view, "Install Error"),
          testCase.overrides.install_error
        );
      } else {
        assert.equal(infoValue(view, "Install Error"), null);
      }

      // Header actions.
      const expected = testCase.header;
      if (expected.console) {
        assert.ok(headerAction(view, "Console"), "Console action visible");
      } else {
        assert.equal(headerAction(view, "Console"), null, "Console hidden");
      }

      if (expected.stop) {
        const stop = headerAction(view, "Stop");
        assert.ok(stop, "Stop action visible while running");
        assert.equal(stop.disabled, false, "Stop enabled while running");
      } else {
        assert.equal(headerAction(view, "Stop"), null, "Stop hidden");
      }

      if (expected.start) {
        const start = headerAction(view, expected.start.label);
        assert.ok(start, `${expected.start.label} action visible`);
        assert.equal(
          start.disabled,
          expected.start.disabled,
          `${expected.start.label} disabled state`
        );
        assert.equal(
          start.getAttribute("title"),
          expected.start.title,
          `${expected.start.label} tooltip`
        );
      } else {
        assert.equal(headerAction(view, "Start"), null, "Start hidden");
        assert.equal(headerAction(view, "Retry"), null, "Retry hidden");
      }

      for (const installLabel of ["Install Software", "Retry Install"]) {
        const install = headerAction(view, installLabel);
        if (expected.install?.label === installLabel) {
          assert.ok(install, `${installLabel} action visible`);
          assert.equal(install.disabled, false, `${installLabel} enabled`);
        } else {
          assert.equal(install, null, `${installLabel} hidden`);
        }
      }

      if (expected.delete) {
        const remove = headerAction(view, "Delete");
        assert.ok(remove, "Delete action visible when stopped/errored");
      } else {
        assert.equal(headerAction(view, "Delete"), null, "Delete hidden");
      }

      // Controls card.
      const primary = controlButton(view, testCase.controls.label);
      assert.ok(primary, `${testCase.controls.label} control visible`);
      assert.equal(
        primary.disabled,
        testCase.controls.disabled,
        `${testCase.controls.label} disabled state`
      );

      const opposite =
        testCase.controls.label === "Start server" ? "Stop server" : "Start server";
      assert.equal(controlButton(view, opposite), null, `${opposite} hidden`);

      const restart = controlButton(view, "Restart server");
      assert.ok(restart, "Restart control visible");
      assert.equal(
        restart.disabled,
        testCase.restartDisabled,
        "Restart disabled state"
      );

      // Header and card must never disagree about whether starting is allowed.
      if (expected.start && testCase.controls.label === "Start server") {
        assert.equal(
          expected.start.disabled,
          primary.disabled,
          "header Start and Controls Start agree"
        );
      }
    });
  }
});

/* ------------------------------------------------------------------------- */
/* Rendering reliability                                                     */
/* ------------------------------------------------------------------------- */

describe("server-detail DOM - rendering reliability", () => {
  it("mounts the loading skeleton synchronously and replaces it on success", async () => {
    const server = makeServer();
    const calls = useApi(server);
    window.location.hash = `#/servers/${server.id}`;

    const view = newView();
    const pending = renderServerDetail(view, createContext());

    // `loadingState()` is mounted before the first await.
    assert.ok(
      view.querySelector('[aria-busy="true"]'),
      "loading skeleton visible while the API is pending"
    );

    await pending;

    assert.equal(busyCount(view), 0, "aria-busy cleared after load");
    assert.equal(skeletonCount(view), 0, "no skeleton left behind");
    assert.equal(view.querySelector(".page-head h2").textContent, server.name);

    assert.deepEqual(
      calls.map((call) => `${call.method} ${call.url}`),
      [`GET /api/servers/${server.id}`, "GET /api/java/runtimes"],
      "both data dependencies were requested through the real API client"
    );
  });

  it("does not throw when optional server properties are absent", async () => {
    const server = makeServer({
      software_version: undefined,
      install_error: undefined,
      started_at: undefined,
      stopped_at: undefined,
      java_runtime_id: undefined,
    });

    const { view } = await renderPage(server);

    assert.equal(view.querySelector(".page-head h2").textContent, server.name);
    assert.equal(infoValue(view, "Software Version"), null);
    assert.equal(infoValue(view, "Install Error"), null);
    assert.equal(infoValue(view, "Started"), null);
    assert.equal(infoValue(view, "Stopped"), null);
    assert.equal(infoValue(view, "Java runtime"), "Unknown runtime");
    assert.equal(skeletonCount(view), 0);
  });

  it("shows the error state when the server request fails", async () => {
    window.location.hash = "#/servers/5";
    mockFetch({
      "GET /api/servers/5": jsonResponse({ detail: "Server not found." }, 404),
      "GET /api/java/runtimes": jsonResponse({ items: RUNTIMES }),
    });

    const view = newView();
    await renderServerDetail(view, createContext());

    const panel = view.querySelector(".error-panel");
    assert.ok(panel, "error panel rendered");
    assert.equal(panel.getAttribute("role"), "alert");
    assert.match(panel.textContent, /Server not found\./);
    assert.ok(panel.querySelector("button"), "retry action available");
    assert.equal(skeletonCount(view), 0, "no permanent skeleton on failure");
  });

  it("shows the error state when the panel cannot be reached", async () => {
    window.location.hash = "#/servers/5";
    globalThis.fetch = async () => {
      throw new TypeError("network down");
    };

    const view = newView();
    await renderServerDetail(view, createContext());

    const panel = view.querySelector(".error-panel");
    assert.ok(panel, "error panel rendered");
    assert.match(panel.textContent, /Cannot reach the panel/);
    assert.equal(skeletonCount(view), 0);
  });

  it("still renders the page when the Java runtimes request fails", async () => {
    window.location.hash = "#/servers/5";
    const server = makeServer();
    mockFetch({
      "GET /api/servers/5": jsonResponse(server),
      "GET /api/java/runtimes": jsonResponse({ detail: "boom" }, 500),
    });

    const view = newView();
    await renderServerDetail(view, createContext());

    assert.equal(view.querySelector(".page-head h2").textContent, server.name);
    assert.equal(infoValue(view, "Java runtime"), "System default");
    assert.equal(view.querySelector(".error-panel"), null, "no error panel");
    assert.equal(skeletonCount(view), 0);
  });

  it("shows an invalid-id error without calling the API", async () => {
    const calls = mockFetch({});
    window.location.hash = "#/servers/not-a-number";

    const view = newView();
    await renderServerDetail(view, createContext());

    assert.match(view.textContent, /Invalid server ID\./);
    assert.equal(calls.length, 0, "no API request for an invalid id");
    assert.equal(skeletonCount(view), 0);
  });

  it("does not duplicate elements or click handlers across repeated renders", async () => {
    const server = makeServer();
    const calls = useApi(server, {
      extra: {
        "POST /api/servers/5/start": jsonResponse({ id: 5, status: "starting" }),
      },
    });
    window.location.hash = "#/servers/5";

    const view = newView();
    const ctx = createContext();

    await renderServerDetail(view, ctx);
    const firstActionCount = headerActions(view).length;
    const firstInfoRowCount = view.querySelectorAll(".info-row").length;

    await renderServerDetail(view, ctx);

    assert.equal(headerActions(view).length, firstActionCount);
    assert.equal(view.querySelectorAll(".info-row").length, firstInfoRowCount);
    assert.equal(view.querySelectorAll(".page-head").length, 1);
    assert.equal(view.querySelectorAll(".card").length, 3);
    assert.equal(view.querySelectorAll(".card-title").length, 3);
    assert.deepEqual(cardTitles(view), [
      "Configuration",
      "Controls",
      "Recent logs",
    ]);

    const primary = controlButton(view, "Start server");
    assert.ok(primary);
    primary.click();
    await flush();

    const startCalls = calls.filter(
      (call) => call.method === "POST" && call.url === "/api/servers/5/start"
    );
    assert.equal(startCalls.length, 1, "exactly one click handler is bound");
    assert.equal(startCalls[0].headers["X-CSRF-Token"], "test-csrf-token");
  });

  it("does not call the API from a disabled Start control", async () => {
    const server = makeServer({ status: "stopped", install_status: "pending" });
    const { view, calls } = await renderPage(server);

    const primary = controlButton(view, "Start server");
    assert.ok(primary);
    assert.equal(primary.disabled, true);

    primary.click();
    await flush();

    assert.equal(
      calls.filter((call) => call.method !== "GET").length,
      0,
      "disabled control must not trigger a mutating request"
    );
  });
});

/* ------------------------------------------------------------------------- */
/* Navigation                                                                */
/* ------------------------------------------------------------------------- */

describe("server-detail DOM - navigation", () => {
  it("renders again after navigating away and back", async () => {
    const server = makeServer();
    useApi(server);
    window.location.hash = "#/servers/5";

    const view = newView();
    await renderServerDetail(view, createContext());
    assert.equal(view.querySelector(".page-head h2").textContent, "Help");

    // The router swaps routes and reuses the shell's view element.
    window.location.hash = "#/servers";
    view.replaceChildren();

    const listView = document.createElement("main");
    listView.textContent = "Servers";
    document.body.append(listView);
    assert.equal(view.querySelector(".page-head h2"), null);

    // Navigating back re-runs the same view function.
    window.location.hash = "#/servers/5";
    await renderServerDetail(view, createContext());

    assert.equal(view.querySelector(".page-head h2").textContent, "Help");
    assert.deepEqual(cardTitles(view), [
      "Configuration",
      "Controls",
      "Recent logs",
    ]);
    assert.equal(skeletonCount(view), 0);
    assert.equal(controlButton(view, "Start server").disabled, false);
  });
});

/* ------------------------------------------------------------------------- */
/* Shared actionButton component                                             */
/* ------------------------------------------------------------------------- */

describe("server-detail DOM - directory and JVM arguments", () => {
  it("shows the servers-relative directory instead of the host path", async () => {
    const server = makeServer({
      directory: "/srv/adreoz/servers/help",
      relative_directory: "help",
    });
    const { view } = await renderPage(server);
    assert.equal(infoValue(view, "Directory"), "help");
  });

  it("falls back to the absolute directory when no relative form exists", async () => {
    const server = makeServer({
      directory: "/srv/custom/help",
      relative_directory: null,
    });
    const { view } = await renderPage(server);
    assert.equal(infoValue(view, "Directory"), "/srv/custom/help");
  });

  it("prefills the editor with the configured JVM arguments", async () => {
    const server = makeServer({
      status: "stopped",
      install_status: "completed",
      jvm_args: "-Xmx2G -XX:+UseG1GC",
    });
    const { view } = await renderPage(server);
    const input = view.querySelector(`#server-jvm-args-${server.id}`);
    assert.ok(input, "JVM arguments input should be rendered");
    assert.equal(input.value, "-Xmx2G -XX:+UseG1GC");
  });

  it("PATCHes edited JVM arguments and refreshes the server", async () => {
    const server = makeServer({
      status: "stopped",
      install_status: "completed",
      jvm_args: null,
    });
    const updated = { ...server, jvm_args: "-Xmx3G" };
    const { view, calls } = await renderPage(server, {
      extra: {
        [`PATCH /api/servers/${server.id}`]: jsonResponse(updated),
      },
    });

    const input = view.querySelector(`#server-jvm-args-${server.id}`);
    assert.ok(input, "JVM arguments input should be rendered");
    input.value = "-Xmx3G";
    const save = Array.from(view.querySelectorAll(".field-row button")).find(
      (button) => button.textContent.trim() === "Save"
    );
    assert.ok(save, "Save button should be rendered");
    save.click();
    await flush();
    await flush();

    const patch = calls.find((call) => call.method === "PATCH");
    assert.ok(patch, "a PATCH request should be sent");
    assert.deepEqual(JSON.parse(patch.body), { jvm_args: "-Xmx3G" });
  });
});

describe("actionButton DOM", () => {
  it("renders an enabled button when disabled is omitted", () => {
    const button = actionButton({
      label: "Start",
      iconName: "power",
      handler: () => {},
    });

    assert.equal(button.tagName, "BUTTON");
    assert.equal(button.getAttribute("type"), "button");
    assert.equal(button.disabled, false);
    assert.ok(button.classList.contains("btn"));
  });

  it("renders a disabled button for disabled: true", () => {
    const button = actionButton({
      label: "Start",
      iconName: "power",
      disabled: true,
      handler: () => {},
    });

    assert.equal(button.disabled, true);
    assert.equal(button.hasAttribute("disabled"), true);
  });

  it("renders an enabled button for disabled: false", () => {
    const button = actionButton({
      label: "Start",
      iconName: "power",
      disabled: false,
      handler: () => {},
    });

    assert.equal(button.disabled, false);
  });

  it("uses the label as the default tooltip and honours a custom one", () => {
    const plain = actionButton({
      label: "Start",
      iconName: "power",
      handler: () => {},
    });
    const custom = actionButton({
      label: "Start",
      iconName: "power",
      title: "Install software first.",
      handler: () => {},
    });

    assert.equal(plain.getAttribute("title"), "Start");
    assert.equal(custom.getAttribute("title"), "Install software first.");
  });

  it("still invokes the click handler when enabled", () => {
    let clicks = 0;
    const button = actionButton({
      label: "Start",
      iconName: "power",
      handler: () => {
        clicks += 1;
      },
    });

    button.click();
    assert.equal(clicks, 1);
  });

  it("does not invoke the click handler when disabled", () => {
    let clicks = 0;
    const button = actionButton({
      label: "Start",
      iconName: "power",
      disabled: true,
      handler: () => {
        clicks += 1;
      },
    });

    button.click();
    assert.equal(clicks, 0);
  });
});
