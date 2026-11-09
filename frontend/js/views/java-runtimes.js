/* Java Runtime management page. */

import { api } from "../core/api.js";
import { el, setBusy } from "../core/dom.js";
import { icon } from "../components/icons.js";
import { toast } from "../components/toast.js";
import {
  actionButton,
  errorState,
  loadingState,
  pageHeader,
} from "../components/ui.js";

export async function renderJavaRuntimes(view, ctx) {
  view.replaceChildren(loadingState());

  let runtimes = [];
  let error = null;

  try {
    const data = await api("/java/runtimes");
    runtimes = data.items || [];
  } catch (err) {
    error = err;
  }

  if (!ctx.isCurrent()) return;
  render(runtimes, error);

  function displayName(rt) {
    const parts = [];
    if (rt.name) parts.push(rt.name);
    if (rt.version) parts.push(rt.version);
    return parts.length ? parts.join(" ") : rt.executable_path;
  }

  function render(items, err) {
    const headerActions = [];
    headerActions.push(
      actionButton({
        label: "Install Java 17",
        iconName: "download",
        handler: () => installJava17(),
        variant: "primary",
      })
    );

    const sections = [
      pageHeader({
        eyebrow: "Java Runtimes",
        title: "Java Runtime Manager",
        description:
          "Detect and manage Java runtimes used to launch Minecraft servers. " +
          "System Java is detected automatically; managed runtimes are installed " +
          "into the application data directory only.",
        actions: headerActions,
      }),
    ];

    if (err) {
      sections.push(
        errorState(err.message, () => renderJavaRuntimes(view, ctx))
      );
      view.replaceChildren(el("div", { class: "stack-md" }, ...sections));
      return;
    }

    const table = el("table", { class: "table" });
    const thead = el(
      "thead",
      {},
      el("tr", {},
        el("th", { attrs: { "data-label": "Name" } }, document.createTextNode("Name")),
        el("th", { attrs: { "data-label": "Vendor" } }, document.createTextNode("Vendor")),
        el("th", { attrs: { "data-label": "Version" } }, document.createTextNode("Version")),
        el("th", { attrs: { "data-label": "Type" } }, document.createTextNode("Type")),
        el("th", { attrs: { "data-label": "Path" } }, document.createTextNode("Executable path")),
        el("th", { attrs: { "data-label": "Actions" } }, document.createTextNode("Actions"))
      )
    );
    table.appendChild(thead);

    const tbody = el("tbody");
    if (items.length === 0) {
      tbody.appendChild(
        el("tr", {},
          el("td", { attrs: { colspan: "6", class: "empty-cell" } },
            document.createTextNode("No Java runtimes detected."))
        )
      );
    } else {
      for (const rt of items) {
        const isManaged = Boolean(rt.is_managed);
        const typeLabel = isManaged ? "Managed" : "System";
        const row = el("tr", {});
        row.appendChild(el("td", { attrs: { "data-label": "Name" } }, document.createTextNode(displayName(rt))));
        row.appendChild(el("td", { attrs: { "data-label": "Vendor" } }, document.createTextNode(rt.vendor)));
        row.appendChild(el("td", { attrs: { "data-label": "Version" } }, document.createTextNode(rt.version)));
        row.appendChild(el("td", { attrs: { "data-label": "Type" } }, document.createTextNode(typeLabel)));
        row.appendChild(el("td", { attrs: { "data-label": "Path" } }, document.createTextNode(rt.executable_path)));
        const actions = el("td", { attrs: { "data-label": "Actions" } });
        const actionGroup = el("div", { class: "cell-actions" });
        if (isManaged) {
          actionGroup.appendChild(
            actionButton({
              label: "Delete",
              iconName: "trash",
              variant: "danger",
              handler: () => deleteRuntime(rt),
            })
          );
        }
        actions.appendChild(actionGroup);
        row.appendChild(actions);
        tbody.appendChild(row);
      }
    }
    table.appendChild(tbody);

    sections.push(el("div", { class: "card" }, el("div", { class: "card-body" }, table)));

    view.replaceChildren(el("div", { class: "stack-md" }, ...sections));
  }

  async function installJava17() {
    try {
      const created = await api("/java/runtimes/install", {
        method: "POST",
        body: {},
      });
      toast(`Installed ${created.runtime.name}.`);
      await loadRuntimes();
    } catch (error) {
      toast(error.message || "Failed to install Java runtime.", "error");
    }
  }

  async function deleteRuntime(rt) {
    if (
      !window.confirm(
        `Delete the managed Java runtime '${rt.name}'?\n\n` +
        "This cannot be undone. Servers assigned to this runtime will have their selection cleared."
      )
    ) {
      return;
    }
    try {
      await api(`/java/runtimes/${rt.id}`, { method: "DELETE" });
      toast(`Deleted ${rt.name}.`);
      await loadRuntimes();
    } catch (error) {
      toast(error.message || "Failed to delete Java runtime.", "error");
    }
  }

  async function loadRuntimes() {
    try {
      const data = await api("/java/runtimes");
      if (!ctx.isCurrent()) return;
      render(data.items || [], null);
    } catch (err) {
      if (!ctx.isCurrent()) return;
      render([], err);
    }
  }
}
