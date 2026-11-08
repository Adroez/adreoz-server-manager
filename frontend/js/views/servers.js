/* Server list page. */

import { api } from "../core/api.js";
import { el, setBusy } from "../core/dom.js";
import { formatDateTime, relativeTime } from "../core/format.js";
import { getState } from "../core/store.js";
import { icon } from "../components/icons.js";
import { toast } from "../components/toast.js";
import { confirmDialog } from "../components/modal.js";
import {
  actionButton,
  emptyState,
  errorState,
  loadingState,
  pageHeader,
} from "../components/ui.js";

const PAGE_SIZE = 50;

const STATUS_BADGE = {
  stopped: "badge-inactive",
  starting: "badge is-warn",
  running: "badge-active",
  stopping: "badge is-warn",
  error: "badge badge-danger",
};

export async function renderServers(view, ctx) {
  view.replaceChildren(loadingState());

  let initial;
  try {
    initial = await api(`/servers?limit=${PAGE_SIZE}&offset=0`);
  } catch (error) {
    if (!ctx.isCurrent()) return;
    view.replaceChildren(errorState(error.message, () => renderServers(view, ctx)));
    return;
  }

  if (!ctx.isCurrent()) return;

  const state = {
    items: initial.items,
    total: initial.total,
    search: "",
  };

  const tbody = el("tbody");
  const countNode = el("span", { class: "toolbar-meta" });
  let searchTimer = null;

  const searchInput = el("input", {
    class: "input",
    type: "search",
    placeholder: "Search servers…",
    attrs: { "aria-label": "Search servers", autocomplete: "off" },
  });

  const createButton = el(
    "button",
    {
      class: "btn btn-primary",
      type: "button",
      on: { click: () => (window.location.hash = "#/servers/create") },
    },
    icon("plus", 15),
    document.createTextNode("Create server")
  );

  const table = el(
    "table",
    { class: "table is-cards" },
    el(
      "thead",
      {},
      el(
        "tr",
        {},
        el("th", { text: "Server" }),
        el("th", { text: "Software" }),
        el("th", { text: "Status" }),
        el("th", { text: "Port" }),
        el("th", { text: "Created" }),
        el("th", { class: "table-actions-head", text: "Actions" })
      )
    ),
    tbody
  );

  const card = el(
    "div",
    { class: "card" },
    el(
      "div",
      { class: "toolbar" },
      el(
        "div",
        { class: "search-field" },
        icon("search", 15),
        searchInput
      ),
      countNode
    ),
    el("div", { class: "table-wrap" }, table)
  );

  view.replaceChildren(
    el(
      "div",
      { class: "stack-md" },
      pageHeader({
        eyebrow: "Server lifecycle",
        title: "Servers",
        description:
          "Create, configure, start, and stop Minecraft servers. Each server runs in its own isolated directory.",
        actions: [createButton],
      }),
      card
    )
  );

  searchInput.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      state.search = searchInput.value.trim();
      load({ append: false });
    }, 240);
  });

  renderRows();

  function renderRows() {
    tbody.replaceChildren();
    countNode.textContent = `${state.total} ${state.total === 1 ? "server" : "servers"}`;

    if (state.items.length === 0) {
      tbody.append(
        el(
          "tr",
          {},
          el(
            "td",
            { class: "table-empty", attrs: { colspan: "6" } },
            emptyState({
              iconName: "server",
              title: state.search ? "No matching servers" : "No servers yet",
              text: state.search
                ? "Try a different name."
                : "Create your first Minecraft server.",
            })
          )
        )
      );
    }

    const currentUser = getState().user;
    const isAdmin = currentUser && currentUser.role === "admin";

    for (const item of state.items) {
      const isOwner = item.owner_id === currentUser?.id;
      const canManage = isAdmin || isOwner;

      const serverCell = el(
        "td",
        { attrs: { "data-label": "Server" } },
        el(
          "div",
          { class: "cell-server" },
          el(
            "div",
            {},
            el(
              "div",
              { class: "cell-server-name" },
              document.createTextNode(item.name)
            ),
            el("div", {
              class: "cell-server-meta",
              text: `${item.software_type} ${item.version}`,
            })
          )
        )
      );

      const software = el("td", {
        attrs: { "data-label": "Software" },
        text: `${item.software_type}`,
      });

      const status = el("td", {
        attrs: { "data-label": "Status" },
      });
      status.append(
        el(
          "span",
          {
            class: STATUS_BADGE[item.status] || "badge",
          },
          el("span", {
            class: "status-dot",
            attrs: {
              "aria-hidden": "true",
              class: item.status === "running" ? "" : " is-offline",
            },
          }),
          document.createTextNode(item.status.charAt(0).toUpperCase() + item.status.slice(1))
        )
      );

      const port = el("td", {
        attrs: { "data-label": "Port" },
        text: String(item.port),
      });

      const created = el("td", {
        attrs: { "data-label": "Created", title: formatDateTime(item.created_at) },
        text: relativeTime(item.created_at),
      });

      const actions = el("td", {
        class: "cell-actions",
        attrs: { "data-label": "Actions" },
      });

      if (canManage) {
        actions.append(
          actionButton({
            label: "Details",
            iconName: "server",
            handler: () => {
              window.location.hash = `#/servers/${item.id}`;
            },
          })
        );
        if (item.status === "running") {
          actions.append(
            actionButton({
              label: "Stop",
              iconName: "power",
              variant: "danger",
              handler: (event) => toggleServer(item, event.currentTarget, "stop"),
            })
          );
        } else {
          actions.append(
            actionButton({
              label: item.status === "error" ? "Retry" : "Start",
              iconName: "power",
              handler: (event) => toggleServer(item, event.currentTarget, "start"),
            })
          );
        }
        if (item.status === "stopped" || item.status === "error") {
          actions.append(
            actionButton({
              label: "Delete",
              iconName: "trash",
              variant: "danger",
              handler: (event) => deleteServer(item, event.currentTarget),
            })
          );
        }
      } else {
        actions.append(el("span", { class: "dim", text: "No access" }));
      }

      tbody.append(
        el("tr", {}, serverCell, software, status, port, created, actions)
      );
    }
  }

  async function load({ append = false } = {}) {
    const offset = append ? state.items.length : 0;
    const params = new URLSearchParams({
      limit: String(PAGE_SIZE),
      offset: String(offset),
    });
    if (state.search) params.set("search", state.search);
    try {
      const data = await api(`/servers?${params.toString()}`);
      if (!ctx.isCurrent()) return;
      state.items = append ? [...state.items, ...data.items] : data.items;
      state.total = data.total;
      renderRows();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function toggleServer(item, button, action) {
    setBusy(button, true);
    try {
      if (action === "start") {
        await api(`/servers/${item.id}/start`, { method: "POST" });
        toast(`Starting ${item.name}…`, "info");
      } else {
        await api(`/servers/${item.id}/stop`, { method: "POST" });
        toast(`Stopping ${item.name}…`, "info");
      }
      await load();
    } catch (error) {
      toast(error.message, "error");
      setBusy(button, false);
    }
  }

  async function deleteServer(item, button) {
    const confirmed = await confirmDialog({
      title: "Delete server",
      message: `Permanently delete "${item.name}"? Its directory and all files will be removed. This cannot be undone.`,
      confirmLabel: "Delete server",
      danger: true,
      iconName: "trash",
    });
    if (!confirmed) return;

    setBusy(button, true);
    try {
      await api(`/servers/${item.id}`, { method: "DELETE" });
      toast(`Deleted ${item.name}.`);
      await load();
    } catch (error) {
      toast(error.message, "error");
      setBusy(button, false);
    }
  }
}
