/* Server configuration editor — server.properties editor. */

import { api } from "../core/api.js";
import { el, setBusy } from "../core/dom.js";
import { icon } from "../components/icons.js";
import { toast } from "../components/toast.js";
import {
  actionButton,
  emptyState,
  errorState,
  loadingState,
  pageHeader,
} from "../components/ui.js";

const BOOLEAN_VALUES = ["true", "false"];

export async function renderServerConfig(view, ctx) {
  // Extract server ID from hash: #/servers/123/config
  const hash = window.location.hash.slice(1);
  const parts = hash.split("/");
  const serverId = Number(parts[1]);

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
  render(server);

  // --- Config state ---
  let originalProperties = {};
  let properties = {};
  let isDirty = false;
  let etag = null;
  let validationInfo = null;

  const propertiesContainer = el("div", { class: "config-properties" });

  function render(data) {
    const headerActions = [];

    headerActions.push(
      actionButton({
        label: "Save",
        iconName: "save",
        handler: () => saveConfig(),
        variant: "primary",
      })
    );
    headerActions.push(
      actionButton({
        label: "Discard",
        iconName: "x",
        handler: () => discardChanges(),
        variant: "danger",
      })
    );

    headerActions.push(
      actionButton({
        label: "Back to server",
        iconName: "folder",
        handler: () => {
          window.location.hash = `#/servers/${serverId}`;
        },
      })
    );

    const sections = [
      pageHeader({
        eyebrow: "Configuration · " + data.name,
        title: "Server properties",
        description: `Edit server.properties for ${data.name}. Changes require a server restart to take effect.`,
        actions: headerActions,
      }),
      el(
        "div",
        { class: "config-card" },
        el(
          "div",
          { class: "config-header" },
          el(
            "h3",
            { class: "config-title" },
            icon("settings", 16),
            document.createTextNode("server.properties")
          ),
          el(
            "p",
            { class: "config-subtitle" },
            "Edit Minecraft server properties. Boolean values are true/false. Integer values have range limits."
          )
        ),
        propertiesContainer,
        el(
          "div",
          { class: "config-actions-bar" },
          el(
            "span",
            { class: "config-dirty-indicator", id: "config-dirty-indicator" }
          )
        )
      ),
    ];

    view.replaceChildren(el("div", { class: "stack-md" }, ...sections));

    // Load validation info and properties.
    loadValidationInfo();
    loadProperties();
  }

  async function loadValidationInfo() {
    try {
      validationInfo = await api(`/servers/${serverId}/config/validation`);
    } catch {
      validationInfo = { boolean: [], enum: {}, integer: {}, text: [] };
    }
  }

  async function loadProperties() {
    try {
      const data = await api(`/servers/${serverId}/config/properties`);
      originalProperties = data.properties || {};
      properties = { ...originalProperties };
      etag = data.etag || null;
      isDirty = false;
      renderProperties();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function renderProperties() {
    propertiesContainer.replaceChildren();

    const groups = [
      { key: "motd", label: "MOTD", type: "text" },
      { key: "server-port", label: "Server port", type: "integer" },
      { key: "max-players", label: "Max players", type: "integer" },
      { key: "online-mode", label: "Online mode", type: "boolean" },
      { key: "difficulty", label: "Difficulty", type: "enum" },
      { key: "gamemode", label: "Gamemode", type: "enum" },
      { key: "view-distance", label: "View distance", type: "integer" },
      { key: "simulation-distance", label: "Simulation distance", type: "integer" },
      { key: "pvp", label: "PVP", type: "boolean" },
      { key: "spawn-protection", label: "Spawn protection", type: "integer" },
      { key: "enable-command-block", label: "Command blocks", type: "boolean" },
      { key: "level-name", label: "Level name", type: "text" },
      { key: "level-seed", label: "Level seed", type: "text" },
    ];

    // Add unknown properties that exist but aren't in our known list.
    const knownKeys = new Set(groups.map((g) => g.key));
    for (const [key] of Object.entries(properties)) {
      if (!knownKeys.has(key)) {
        groups.push({ key, label: key, type: "text" });
      }
    }

    for (const group of groups) {
      const key = group.key;
      const value = properties[key] !== undefined ? properties[key] : "";
      const type = group.type;

      const row = el("div", { class: "config-row" });

      const labelCell = el(
        "div",
        { class: "config-label" },
        el("label", { attrs: { for: `config-${key}` } }, group.label),
        el("span", { class: "config-key", text: key })
      );

      let input;
      if (type === "boolean") {
        input = el("select", {
          class: "config-input",
          id: `config-${key}`,
        });
        for (const val of BOOLEAN_VALUES) {
          input.appendChild(
            el("option", { value: val, text: val.charAt(0).toUpperCase() + val.slice(1) })
          );
        }
        input.value = value;
      } else if (type === "enum") {
        input = el("select", {
          class: "config-input",
          id: `config-${key}`,
        });
        const allowed = validationInfo && validationInfo.enum && validationInfo.enum[key];
        if (allowed) {
          for (const val of allowed) {
            input.appendChild(el("option", { value: val, text: val }));
          }
        }
        input.value = value;
      } else {
        input = el("input", {
          class: "config-input",
          id: `config-${key}`,
          type: "text",
          value: value,
          attrs: { spellcheck: "false" },
        });
      }

      input.addEventListener("change", () => {
        properties[key] = input.value;
        isDirty = true;
        updateDirtyIndicator();
      });

      const hint = el("div", { class: "config-hint" });
      if (type === "integer" && validationInfo && validationInfo.integer && validationInfo.integer[key]) {
        const range = validationInfo.integer[key];
        hint.textContent = `Integer: ${range.min}–${range.max}`;
      } else if (type === "boolean") {
        hint.textContent = "true or false";
      }

      row.appendChild(labelCell);
      row.appendChild(el("div", { class: "config-input-cell" }, input));
      row.appendChild(hint);

      propertiesContainer.appendChild(row);
    }
  }

  function updateDirtyIndicator() {
    const indicator = document.getElementById("config-dirty-indicator");
    if (indicator) {
      indicator.textContent = isDirty ? "• Unsaved changes — changes require a server restart" : "";
    }
  }

  async function saveConfig() {
    try {
      const headers = {};
      if (etag) {
        headers["If-Match"] = etag;
      }
      const result = await api(`/servers/${serverId}/config/properties`, {
        method: "PATCH",
        headers,
        body: { properties },
      });
      originalProperties = { ...result.properties };
      properties = { ...result.properties };
      etag = result.etag || null;
      isDirty = false;
      updateDirtyIndicator();
      toast("Server properties saved. Changes require a server restart to take effect.");
    } catch (error) {
      if (error.status === 409) {
        toast(
          "Conflict: Another user has modified the server configuration. "
          "Reload and try again.",
          "error"
        );
        await loadProperties();
      } else {
        toast(error.message, "error");
      }
    }
  }

  function discardChanges() {
    properties = { ...originalProperties };
    isDirty = false;
    updateDirtyIndicator();
    renderProperties();
    toast("Changes discarded.");
  }

  // Warn before navigating away with unsaved changes.
  const beforeUnloadHandler = (event) => {
    if (isDirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  };
  window.addEventListener("beforeunload", beforeUnloadHandler);

  const originalIsCurrent = ctx.isCurrent;
  ctx.isCurrent = () => {
    if (!originalIsCurrent()) {
      window.removeEventListener("beforeunload", beforeUnloadHandler);
      return false;
    }
    return true;
  };
}
