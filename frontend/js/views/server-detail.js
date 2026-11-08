/* Server details page. */

import { api } from "../core/api.js";
import { el, setBusy } from "../core/dom.js";
import { formatDateTime, relativeTime } from "../core/format.js";
import { icon } from "../components/icons.js";
import { toast } from "../components/toast.js";
import {
  actionButton,
  emptyState,
  errorState,
  infoRow,
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

export async function renderServerDetail(view, ctx) {
  // Extract server ID from hash: #/servers/123
  const hash = window.location.hash.slice(1);
  const parts = hash.split("/");
  const serverId = Number(parts[1]);
  const tab = parts[3] || "details";

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

  function render(data) {
    const canManage = true; // Ownership checked by backend; UI shows all controls

    const headerActions = [];
    if (data.status === "running") {
      headerActions.push(
        actionButton({
          label: "Console",
          iconName: "terminal",
          handler: () => {
            window.location.hash = `#/servers/${data.id}/console`;
          },
        })
      );
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

    const statusNode = el(
      "span",
      {
        class: STATUS_BADGE[data.status] || "badge",
      },
      el("span", {
        class: "status-dot",
        attrs: {
          "aria-hidden": "true",
          class: data.status === "running" ? "" : " is-offline",
        },
      }),
      document.createTextNode(
        data.status.charAt(0).toUpperCase() + data.status.slice(1)
      )
    );

    const configCard = el(
      "article",
      { class: "card" },
      el(
        "div",
        { class: "card-header" },
        el(
          "div",
          {},
          el(
            "h3",
            { class: "card-title" },
            icon("settings", 16),
            document.createTextNode("Configuration")
          ),
          el("p", { class: "card-subtitle", text: "Edit when the server is stopped." })
        )
      ),
      el(
        "div",
        { class: "card-body" },
        el(
          "dl",
          { class: "info-rows" },
          infoRow("Name", data.name),
          infoRow("Unique ID", data.unique_id),
          infoRow("Software", `${data.software_type} ${data.version}`),
          infoRow("Status", statusNode),
          infoRow("Port", String(data.port)),
          infoRow("Max players", String(data.max_players)),
          infoRow("Online mode", String(data.online_mode)),
          infoRow("Difficulty", data.difficulty),
          infoRow("Gamemode", data.gamemode),
          infoRow("View distance", String(data.view_distance)),
          infoRow("MOTD", data.motd),
          infoRow("Directory", data.directory),
          infoRow("Owner", `User ${data.owner_id}`),
          infoRow("Created", formatDateTime(data.created_at)),
          data.started_at
            ? infoRow("Started", formatDateTime(data.started_at))
            : null,
          data.stopped_at
            ? infoRow("Stopped", formatDateTime(data.stopped_at))
            : null
        )
      )
    );

    const controlsCard = el(
      "article",
      { class: "card" },
      el(
        "div",
        { class: "card-header" },
        el(
          "div",
          {},
          el(
            "h3",
            { class: "card-title" },
            icon("power", 16),
            document.createTextNode("Controls")
          ),
          el("p", { class: "card-subtitle", text: "Start, stop, or restart the server." })
        )
      ),
      el(
        "div",
        { class: "card-body" },
        el(
          "div",
          { class: "form-grid" },
          el(
            "div",
            {},
            el(
              "button",
              {
                class: `btn btn-${data.status === "running" ? "danger" : "primary"}`,
                type: "button",
                disabled: data.status === "starting" || data.status === "stopping",
                on: { click: () => changeStatus(data.status === "running" ? "stop" : "start") },
              },
              icon(data.status === "running" ? "power" : "power", 15),
              document.createTextNode(
                data.status === "running" ? "Stop server" : "Start server"
              )
            )
          ),
          el(
            "div",
            {},
            el(
              "button",
              {
                class: "btn btn-secondary",
                type: "button",
                disabled:
                  data.status !== "running" && data.status !== "stopped" && data.status !== "error",
                on: { click: () => changeStatus("restart") },
              },
              icon("refresh", 15),
              document.createTextNode("Restart server")
            )
          )
        )
      )
    );

    const logsCard = el(
      "article",
      { class: "card" },
      el(
        "div",
        { class: "card-header" },
        el(
          "div",
          {},
          el(
            "h3",
            { class: "card-title" },
            icon("activity", 16),
            document.createTextNode("Recent logs")
          ),
          el("p", { class: "card-subtitle", text: "Server process output." })
        )
      ),
      el(
        "div",
        { class: "card-body" },
        el("div", { class: "log-placeholder", text: "No logs yet. Start the server to see output." })
      )
    );

    const sections = [
      pageHeader({
        eyebrow: "Server · " + data.name,
        title: data.name,
        description: `${data.software_type} ${data.version} · ${data.status}`,
        actions: headerActions,
      }),
      el("div", { class: "split-grid" }, configCard, controlsCard),
      logsCard,
    ];

    view.replaceChildren(el("div", { class: "stack-md" }, ...sections));
  }

  async function changeStatus(action) {
    try {
      if (action === "start") {
        await api(`/servers/${serverId}/start`, { method: "POST" });
        toast(`Starting ${data.name}…`, "info");
      } else if (action === "stop") {
        await api(`/servers/${serverId}/stop`, { method: "POST" });
        toast(`Stopping ${data.name}…`, "info");
      } else {
        await api(`/servers/${serverId}/restart`, { method: "POST" });
        toast(`Restarting ${data.name}…`, "info");
      }
      // Refresh the page data.
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
}
