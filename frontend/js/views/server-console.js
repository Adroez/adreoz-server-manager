/* Server console page — live WebSocket console for Minecraft servers. */

import { api } from "../core/api.js";
import { el } from "../core/dom.js";
import { icon } from "../components/icons.js";
import { toast } from "../components/toast.js";
import {
  actionButton,
  errorState,
  loadingState,
  pageHeader,
} from "../components/ui.js";

const STATUS_BADGE = {
  stopped: "badge-inactive",
  starting: "badge is-warn",
  running: "badge-active",
  stopping: "badge is-warn",
  error: "badge badge-danger",
};

// Maximum number of log lines rendered in the DOM at once.
const MAX_RENDERED_LINES = 500;

export async function renderServerConsole(view, ctx) {
  // Extract server ID from hash: #/servers/123/console or #/servers/123
  const hash = window.location.hash.slice(1);
  const parts = hash.split("/");
  const serverId = Number(parts[2]);

  if (!Number.isInteger(serverId)) {
    view.replaceChildren(
      errorState("Invalid server ID.", () => (window.location.hash = "#/servers"))
    );
    return;
  }

  view.replaceChildren(loadingState());

  let server;
  try {
    server = await api(`/servers/${serverId}`);
  } catch (error) {
    if (!ctx.isCurrent()) return;
    view.replaceChildren(
      errorState(error.message, () => (window.location.hash = "#/servers"))
    );
    return;
  }

  if (!ctx.isCurrent()) return;

  // --- Console state ---
  let ws = null;
  let wsReconnectTimer = null;
  let wsConnected = false;
  let wsReconnecting = false;
  let disposed = false;
  let autoScroll = true;
  let logLines = []; // { stream, line, time }
  let renderScheduled = false;

  const statusNode = el("span", { class: "badge" });

  function buildHeaderActions(data) {
    const headerActions = [];
    if (data.status === "running") {
      headerActions.push(
        actionButton({
          label: "Stop",
          iconName: "power",
          variant: "danger",
          handler: () => changeStatus("stop"),
        })
      );
    } else {
      headerActions.push(
        actionButton({
          label: data.status === "error" ? "Retry" : "Start",
          iconName: "power",
          handler: () => changeStatus("start"),
        })
      );
    }
    if (data.status === "stopped" || data.status === "error") {
      headerActions.push(
        actionButton({
          label: "Delete",
          iconName: "trash",
          variant: "danger",
          handler: () => deleteServer(data),
        })
      );
    }
    return headerActions;
  }

  const connectionStatus = el(
    "span",
    { class: "badge badge-inactive" },
    el("span", { class: "status-dot is-offline", attrs: { "aria-hidden": "true" } }),
    document.createTextNode("Disconnected")
  );

  const logContainer = el("div", { class: "console-log" });
  const logScrollWrapper = el(
    "div",
    { class: "console-scroll" },
    logContainer
  );

  const commandInput = el("input", {
    class: "input",
    id: "console-command",
    type: "text",
    placeholder: "Type a command and press Enter…",
    disabled: true,
    attrs: { autocapitalize: "none", spellcheck: "false", autocomplete: "off" },
  });

  const sendButton = el(
    "button",
    {
      class: "btn btn-primary",
      type: "button",
      disabled: true,
      on: { click: () => sendCommand() },
    },
    icon("arrow-right", 13),
    document.createTextNode("Send")
  );

  const clearButton = el(
    "button",
    {
      class: "btn btn-ghost btn-sm",
      type: "button",
      on: { click: () => clearDisplay() },
    },
    icon("x", 13),
    document.createTextNode("Clear display")
  );

  const autoScrollToggle = el(
    "button",
    {
      class: "btn btn-ghost btn-sm",
      type: "button",
      attrs: { "aria-pressed": "true", "aria-label": "Auto-scroll" },
      on: { click: () => toggleAutoScroll() },
    },
    icon("clock", 13),
    document.createTextNode("Auto-scroll")
  );

  const reconnectButton = el(
    "button",
    {
      class: "btn btn-secondary btn-sm",
      type: "button",
      style: { display: "none" },
      on: { click: () => connectWebSocket() },
    },
    icon("refresh", 13),
    document.createTextNode("Reconnect")
  );

  function render(data) {
    const headerActions = buildHeaderActions(data);

    statusNode.className = STATUS_BADGE[data.status] || "badge";
    statusNode.replaceChildren(
      el("span", {
        class: `status-dot${data.status === "running" ? "" : " is-offline"}`,
        attrs: { "aria-hidden": "true" },
      }),
      document.createTextNode(
        data.status.charAt(0).toUpperCase() + data.status.slice(1)
      )
    );

    const sections = [
      pageHeader({
        eyebrow: "Server console · " + data.name,
        title: data.name,
        description: `${data.software_type} ${data.version} · ${data.status}`,
        actions: headerActions,
      }),
      el(
        "div",
        { class: "console-card" },
        el(
          "div",
          { class: "console-header" },
          el(
            "div",
            { class: "console-title" },
            el("span", { class: "console-icon" }, icon("terminal", 16)),
            el("strong", { text: data.name })
          ),
          el(
            "div",
            { class: "console-meta" },
            el("span", { text: `${data.software_type} ${data.version}` }),
            statusNode,
            connectionStatus
          )
        ),
        el(
          "div",
          { class: "console-body" },
          el("div", { class: "console-scroll-wrapper" }, logScrollWrapper),
          el(
            "div",
            { class: "console-input" },
            el(
              "div",
              { class: "input-row" },
              commandInput,
              sendButton
            ),
            el(
              "div",
              { class: "controls-row" },
              clearButton,
              autoScrollToggle,
              reconnectButton
            )
          )
        )
      )
    ];

    view.replaceChildren(el("div", { class: "stack-md" }, ...sections));

    // Restore auto-scroll toggle state.
    autoScrollToggle.setAttribute("aria-pressed", autoScroll ? "true" : "false");

    // Focus the command input if connected.
    if (wsConnected) commandInput.focus();

    // Render any buffered history.
    renderLogs();
  }

  // --- WebSocket connection ---

  function connectWebSocket() {
    if (wsReconnecting || disposed) return;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/api/servers/${serverId}/console`;

    try {
      ws = new WebSocket(wsUrl);
    } catch (error) {
      wsConnected = false;
      updateConnectionUI(false);
      scheduleReconnect();
      return;
    }

    wsReconnecting = false;
    if (wsReconnectTimer) {
      window.clearTimeout(wsReconnectTimer);
      wsReconnectTimer = null;
    }
    reconnectButton.style.display = "none";

    ws.onopen = () => {
      wsConnected = true;
      updateConnectionUI(true);
      commandInput.disabled = false;
      sendButton.disabled = false;
      commandInput.focus();
      toast(`Connected to console for ${server.name}.`, "info");
    };

    ws.onclose = () => {
      wsConnected = false;
      if (disposed) return;
      updateConnectionUI(false);
      commandInput.disabled = true;
      sendButton.disabled = true;
      if (!wsReconnecting) {
        toast("Console disconnected. Reconnecting…", "info");
        scheduleReconnect();
      }
    };

    ws.onerror = () => {
      wsConnected = false;
      if (disposed) return;
      updateConnectionUI(false);
      if (!wsReconnecting) {
        scheduleReconnect();
      }
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data && data.stream && typeof data.line === "string") {
          appendLog(data.stream, data.line);
        }
      } catch {
        // Ignore malformed messages.
      }
    };
  }

  function updateConnectionUI(connected) {
    if (connected) {
      connectionStatus.replaceChildren(
        el("span", { class: "status-dot", attrs: { "aria-hidden": "true" } }),
        document.createTextNode("Connected")
      );
      connectionStatus.className = "badge badge-active";
      reconnectButton.style.display = "none";
    } else {
      connectionStatus.replaceChildren(
        el("span", { class: "status-dot is-offline", attrs: { "aria-hidden": "true" } }),
        document.createTextNode("Disconnected")
      );
      connectionStatus.className = "badge badge-inactive";
    }
  }

  function scheduleReconnect() {
    if (wsReconnecting || disposed) return;
    wsReconnecting = true;
    reconnectButton.style.display = "inline-block";
    wsReconnectTimer = window.setTimeout(() => {
      wsReconnecting = false;
      wsReconnectTimer = null;
      if (!disposed) connectWebSocket();
    }, 3000);
  }

  function disconnectWebSocket() {
    disposed = true;
    if (wsReconnectTimer) {
      window.clearTimeout(wsReconnectTimer);
      wsReconnectTimer = null;
    }
    wsReconnecting = false;
    reconnectButton.style.display = "none";
    if (ws) {
      try {
        ws.close();
      } catch {
        // Ignore close errors.
      }
      ws = null;
    }
    wsConnected = false;
    updateConnectionUI(false);
    commandInput.disabled = true;
    sendButton.disabled = true;
  }

  // --- Log rendering ---

  function appendLog(stream, line) {
    const time = new Date().toISOString();
    logLines.push({ stream, line, time });
    if (logLines.length > MAX_RENDERED_LINES) {
      logLines.shift();
    }
    scheduleRenderLogs();
  }

  function scheduleRenderLogs() {
    if (renderScheduled) return;
    renderScheduled = true;
    window.requestAnimationFrame(() => {
      renderScheduled = false;
      renderLogs();
    });
  }

  function renderLogs() {
    const atBottom = isAtBottom();
    logContainer.replaceChildren();

    const fragment = document.createDocumentFragment();
    for (const entry of logLines) {
      const lineNode = el(
        "div",
        {
          class: `console-line console-${entry.stream}`,
          attrs: { "aria-label": `${entry.stream}: ${entry.line}` },
        },
        el("span", { class: "console-time", text: formatLogTime(entry.time) }),
        el("span", { class: "console-stream", text: entry.stream.toUpperCase() }),
        el("span", { class: "console-text", text: entry.line })
      );
      fragment.appendChild(lineNode);
    }
    logContainer.appendChild(fragment);

    if (autoScroll || atBottom) {
      scrollToBottom();
    }
  }

  function formatLogTime(iso) {
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString([], { hour12: false });
    } catch {
      return "";
    }
  }

  function isAtBottom() {
    const { scrollTop, scrollHeight, clientHeight } = logScrollWrapper;
    return scrollHeight - scrollTop - clientHeight < 20;
  }

  function scrollToBottom() {
    logScrollWrapper.scrollTop = logScrollWrapper.scrollHeight;
  }

  function clearDisplay() {
    logLines = [];
    logContainer.replaceChildren();
    toast("Display cleared. Server logs are preserved.", "info");
  }

  function toggleAutoScroll() {
    autoScroll = !autoScroll;
    autoScrollToggle.setAttribute("aria-pressed", autoScroll ? "true" : "false");
    if (autoScroll) scrollToBottom();
  }

  // --- Commands ---

  async function sendCommand() {
    const value = commandInput.value.trim();
    if (!value) return;

    if (!wsConnected || !ws || ws.readyState !== WebSocket.OPEN) {
      toast("Not connected to the console.", "error");
      return;
    }

    try {
      ws.send(value);
      commandInput.value = "";
      commandInput.focus();
    } catch (error) {
      toast("Failed to send command.", "error");
    }
  }

  commandInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      sendCommand();
    }
  });

  logScrollWrapper.addEventListener("scroll", () => {
    if (!isAtBottom()) {
      autoScroll = false;
      autoScrollToggle.setAttribute("aria-pressed", "false");
    } else if (!autoScroll) {
      autoScroll = true;
      autoScrollToggle.setAttribute("aria-pressed", "true");
    }
  });

  // --- Server status changes ---

  async function changeStatus(action) {
    try {
      if (action === "start") {
        await api(`/servers/${serverId}/start`, { method: "POST" });
        toast(`Starting ${server.name}…`, "info");
      } else if (action === "stop") {
        await api(`/servers/${serverId}/stop`, { method: "POST" });
        toast(`Stopping ${server.name}…`, "info");
      } else {
        await api(`/servers/${serverId}/restart`, { method: "POST" });
        toast(`Restarting ${server.name}…`, "info");
      }
      const updated = await api(`/servers/${serverId}`);
      if (ctx.isCurrent()) render(updated);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function deleteServer(serverData) {
    const confirmed = await confirmDelete(serverData);
    if (!confirmed) return;
    try {
      await api(`/servers/${serverId}`, { method: "DELETE" });
      toast(`Deleted ${serverData.name}.`);
      window.location.hash = "#/servers";
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function confirmDelete(serverData) {
    return new Promise((resolve) => {
      const dialog = el("dialog", { class: "modal" });

      const close = () => {
        if (dialog.open) dialog.close();
      };

      const header = el(
        "div",
        { class: "modal-header" },
        el("div", {}, el("h3", { class: "modal-title", text: "Delete server" })),
        el(
          "button",
          {
            class: "modal-close",
            type: "button",
            attrs: { "aria-label": "Close dialog" },
            on: { click: close },
          },
          icon("x", 16)
        )
      );

      const body = el(
        "div",
        { class: "modal-body confirm-body" },
        el("div", { class: "empty-icon confirm-icon" }, icon("alert-triangle", 20)),
        el(
          "p",
          { class: "confirm-copy" },
          document.createTextNode(
            `Permanently delete "${serverData.name}"? Its directory and all files will be removed. This cannot be undone.`
          )
        )
      );

      const footer = el(
        "div",
        { class: "modal-footer" },
        el(
          "button",
          {
            class: "btn btn-ghost",
            type: "button",
            on: { click: () => { close(); resolve(false); } },
          },
          document.createTextNode("Cancel")
        ),
        el(
          "button",
          {
            class: "btn btn-danger",
            type: "button",
            on: { click: () => { close(); resolve(true); } },
          },
          icon("trash", 15),
          document.createTextNode("Delete server")
        )
      );

      dialog.append(header, body, footer);
      dialog.addEventListener("click", (event) => {
        if (event.target === dialog) { close(); resolve(false); }
      });
      dialog.addEventListener("close", () => {
        dialog.remove();
      });

      document.body.append(dialog);
      dialog.showModal();
    });
  }

  // --- Lifecycle ---

  render(server);
  connectWebSocket();

  // Cleanup when the route changes.
  ctx.onCleanup(disconnectWebSocket);
}
