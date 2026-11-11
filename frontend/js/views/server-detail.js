/* Server details page. */

import { api } from "../core/api.js";
import { el } from "../core/dom.js";
import { formatDateTime } from "../core/format.js";
import { icon } from "../components/icons.js";
import { toast } from "../components/toast.js";
import {
  actionButton,
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

const INSTALL_BADGE = {
  pending: "badge is-warn",
  completed: "badge-active",
  failed: "badge badge-danger",
  none: "badge-inactive",
};

export async function renderServerDetail(view, ctx) {
  // Extract server ID from hash: #/servers/123
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
  let runtimes = [];
  try {
    const [serverResult, runtimesResult] = await Promise.allSettled([
      api(`/servers/${serverId}`),
      api("/java/runtimes"),
    ]);
    if (serverResult.status !== "fulfilled") {
      throw serverResult.reason;
    }
    server = serverResult.value;
    if (runtimesResult.status === "fulfilled") {
      runtimes = runtimesResult.value.items || [];
    }
  } catch (error) {
    if (!ctx.isCurrent()) return;
    view.replaceChildren(
      errorState(error.message, () => (window.location.hash = "#/servers"))
    );
    return;
  }

  if (!ctx.isCurrent()) return;
  render(server, runtimes);

  function render(data, availableRuntimes) {
    const headerActions = [];
    const installComplete =
      data.install_status === "completed" || data.install_status === "none";
    const canStart =
      data.status === "stopped" || data.status === "error";
    // Derived once in render scope: used by both the header Start action and
    // the Controls card. The Start action requires a stopped/error server
    // whose software installation is complete.
    const startDisabled = !canStart || !installComplete;
    const operationsBusy =
      data.status === "starting" || data.status === "stopping";

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
          disabled: startDisabled,
          handler: () => changeStatus("start"),
          title: !installComplete
            ? "Server software is not installed. Install software before starting."
            : undefined,
        })
      );
    }
    headerActions.push(
      actionButton({
        label: "File manager",
        iconName: "folder",
        handler: () => {
          window.location.hash = `#/servers/${data.id}/files`;
        },
      })
    );
    headerActions.push(
      actionButton({
        label: "Configuration",
        iconName: "settings",
        handler: () => {
          window.location.hash = `#/servers/${data.id}/config`;
        },
      })
    );
    headerActions.push(
      actionButton({
        label: "Backups",
        iconName: "archive",
        handler: () => {
          window.location.hash = `#/servers/${data.id}/backups`;
        },
      })
    );
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

    // Software install action (only when stopped and install not completed).
    if (
      data.status === "stopped" &&
      data.install_status !== "completed"
    ) {
      headerActions.push(
        actionButton({
          label:
            data.install_status === "failed"
              ? "Retry Install"
              : "Install Software",
          iconName: "download",
          variant: "secondary",
          handler: () => installSoftware(data),
        })
      );
    }

    const statusNode = el(
      "span",
      {
        class: STATUS_BADGE[data.status] || "badge",
      },
      el("span", {
        class: `status-dot${data.status === "running" ? "" : " is-offline"}`,
        attrs: { "aria-hidden": "true" },
      }),
      document.createTextNode(
        data.status.charAt(0).toUpperCase() + data.status.slice(1)
      )
    );

    const installNode = el(
      "span",
      {
        class: INSTALL_BADGE[data.install_status] || "badge",
      },
      document.createTextNode(
        data.install_status.charAt(0).toUpperCase() + data.install_status.slice(1)
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
          data.software_version
            ? infoRow("Software Version", data.software_version)
            : null,
          infoRow("Install Status", installNode),
          data.install_error
            ? infoRow("Install Error", data.install_error)
            : null,
          infoRow(
            "Java runtime",
            (() => {
              if (data.java_runtime_id === null) {
                return "System default";
              }
              const rt = availableRuntimes.find((r) => r.id === data.java_runtime_id);
              if (rt) return `${rt.name} ${rt.version}`;
              return "Unknown runtime";
            })()
          ),
          (() => {
            if (data.status !== "stopped" && data.status !== "error") return null;
            const select = el("select", {
              class: "config-input",
              id: `server-java-runtime-${data.id}`,
            });
            select.appendChild(
              el("option", { value: "", text: "System default" })
            );
            for (const rt of availableRuntimes) {
              select.appendChild(
                el(
                  "option",
                  { value: String(rt.id), text: `${rt.name} ${rt.version}` }
                )
              );
            }
            if (data.java_runtime_id) select.value = String(data.java_runtime_id);
            select.addEventListener("change", () => {
              selectJavaRuntime(data.id, select.value);
            });
            return el(
              "div",
              { class: "field" },
              el(
                "label",
                { attrs: { for: `server-java-runtime-${data.id}` } },
                document.createTextNode("Select Java runtime")
              ),
              select,
              el(
                "p",
                { class: "field-hint" },
                document.createTextNode(
                  "Change the Java runtime used to launch this server. Requires a restart to take effect."
                )
              )
            );
          })(),
          infoRow("Status", statusNode),
          infoRow("Port", String(data.port)),
          infoRow("Max players", String(data.max_players)),
          infoRow("Online mode", String(data.online_mode)),
          infoRow("Difficulty", data.difficulty),
          infoRow("Gamemode", data.gamemode),
          infoRow("View distance", String(data.view_distance)),
          infoRow("MOTD", data.motd),
          infoRow("Directory", data.directory),
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
                disabled:
                  operationsBusy ||
                  (data.status !== "running" && startDisabled),
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
                  operationsBusy ||
                  (!installComplete && data.status !== "running"),
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
        toast(`Starting ${server.name}…`, "info");
      } else if (action === "stop") {
        await api(`/servers/${serverId}/stop`, { method: "POST" });
        toast(`Stopping ${server.name}…`, "info");
      } else {
        await api(`/servers/${serverId}/restart`, { method: "POST" });
        toast(`Restarting ${server.name}…`, "info");
      }
      // Refresh the page data.
      const updated = await api(`/servers/${serverId}`);
      if (ctx.isCurrent()) render(updated, runtimes);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function installSoftware(serverData) {
    const confirmed = await confirmInstall(serverData);
    if (!confirmed) return;

    try {
      await api(`/servers/${serverId}/software/install`, {
        method: "POST",
        body: {
          software_type: serverData.software_type,
          version: serverData.version,
          build_version: serverData.software_version || null,
        },
      });
      toast("Software installation started.", "info");
      const updated = await api(`/servers/${serverId}`);
      if (ctx.isCurrent()) render(updated, runtimes);
    } catch (error) {
      toast(error.message || "Failed to start installation.", "error");
    }
  }

  function confirmInstall(serverData) {
    return new Promise((resolve) => {
      const dialog = el("dialog", { class: "modal" });

      const close = () => {
        if (dialog.open) dialog.close();
      };

      const header = el(
        "div",
        { class: "modal-header" },
        el(
          "div",
          {},
          el(
            "h3",
            { class: "modal-title",
            text:
              serverData.install_status === "failed"
                ? "Retry Software Installation"
                : "Install Server Software"
            }
          )
        ),
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
        el("div", { class: "empty-icon confirm-icon" }, icon("download", 20)),
        el(
          "p",
          { class: "confirm-copy" },
          document.createTextNode(
            `Install ${serverData.software_type} ${serverData.version} for "${serverData.name}"?\n\n` +
            "The EULA (eula.txt) will not be automatically accepted. You must accept it manually before first startup."
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
            class: "btn btn-primary",
            type: "button",
            on: { click: () => { close(); resolve(true); } },
          },
          icon("download", 15),
          document.createTextNode("Install")
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

  async function selectJavaRuntime(serverIdValue, runtimeIdValue) {
    const id = runtimeIdValue === "" ? null : Number(runtimeIdValue);
    try {
      await api(`/servers/${serverIdValue}`, {
        method: "PATCH",
        body: { java_runtime_id: id },
      });
      const label = id === null ? "System default" : "selected runtime";
      toast(`Java runtime changed to ${label}.`);
      // Refresh to reflect the change.
      const updated = await api(`/servers/${serverIdValue}`);
      if (ctx.isCurrent()) render(updated, runtimes);
    } catch (error) {
      toast(error.message || "Failed to change Java runtime.", "error");
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