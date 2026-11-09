/* Minecraft Version management page. */

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

export async function renderMinecraftVersions(view, ctx) {
  view.replaceChildren(loadingState());

  let versions = [];
  let error = null;
  let downloads = new Map(); // versionId -> { status, progress }

  try {
    const data = await api("/minecraft/versions?releases_only=true");
    versions = data.items || [];
  } catch (err) {
    error = err;
  }

  if (!ctx.isCurrent()) return;
  render(versions, error, downloads);

  function render(items, err, dl) {
    const headerActions = [];
    headerActions.push(
      actionButton({
        label: "Refresh",
        iconName: "refresh",
        handler: () => loadVersions(),
      })
    );

    const sections = [
      pageHeader({
        eyebrow: "Minecraft Versions",
        title: "Version Manager",
        description:
          "Browse official Minecraft releases from Mojang's version manifest. " +
          "Download a server JAR to install it for a server.",
        actions: headerActions,
      }),
    ];

    if (err) {
      sections.push(
        errorState(err.message, () => renderMinecraftVersions(view, ctx))
      );
      view.replaceChildren(el("div", { class: "stack-md" }, ...sections));
      return;
    }

    const table = el("table", { class: "table" });
    const thead = el(
      "thead",
      {},
      el("tr", {},
        el("th", { attrs: { "data-label": "Version" } }, document.createTextNode("Version")),
        el("th", { attrs: { "data-label": "Type" } }, document.createTextNode("Type")),
        el("th", { attrs: { "data-label": "Released" } }, document.createTextNode("Release date")),
        el("th", { attrs: { "data-label": "Java required" } }, document.createTextNode("Java required")),
        el("th", { attrs: { "data-label": "Actions" } }, document.createTextNode("Actions"))
      )
    );
    table.appendChild(thead);

    const tbody = el("tbody");
    if (items.length === 0) {
      tbody.appendChild(
        el("tr", {},
          el("td", { attrs: { colspan: "5", class: "empty-cell" } },
            document.createTextNode("No versions available."))
        )
      );
    } else {
      for (const v of items) {
        const downloadState = dl.get(v.id);
        const row = el("tr", {});
        row.appendChild(el("td", { attrs: { "data-label": "Version" } }, document.createTextNode(v.id)));
        row.appendChild(el("td", { attrs: { "data-label": "Type" } }, document.createTextNode(v.type)));
        row.appendChild(el("td", { attrs: { "data-label": "Released" } }, document.createTextNode(v.release_time || v.time || "—")));
        row.appendChild(el("td", { attrs: { "data-label": "Java required" } }, document.createTextNode(javaForVersion(v.id))));
        const actions = el("td", { attrs: { "data-label": "Actions" } });
        const actionGroup = el("div", { class: "cell-actions" });
        if (downloadState) {
          if (downloadState.status === "downloading") {
            const progress = el("div", { class: "progress" });
            const bar = el("div", {
              class: "progress-bar",
              style: { width: `${downloadState.progress || 0}%` },
            });
            progress.appendChild(bar);
            actionGroup.appendChild(progress);
            actionGroup.appendChild(document.createTextNode(` ${downloadState.progress || 0}%`));
          } else if (downloadState.status === "done") {
            actionGroup.appendChild(
              actionButton({
                label: "Installed",
                iconName: "check",
                handler: () => {},
              })
            );
          } else if (downloadState.status === "error") {
            actionGroup.appendChild(
              actionButton({
                label: "Retry",
                iconName: "refresh",
                handler: () => downloadVersion(v.id),
              })
            );
          }
        } else {
          actionGroup.appendChild(
            actionButton({
              label: "Download JAR",
              iconName: "download",
              handler: () => downloadVersion(v.id),
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

  function javaForVersion(versionId) {
    const major = parseInt(versionId, 10);
    if (major === 1) {
      const minor = parseInt(versionId.split(".")[1] || "0", 10);
      if (minor >= 21) return "21";
      if (minor >= 17) return "17";
      return "16";
    }
    return "17";
  }

  async function loadVersions() {
    try {
      const data = await api("/minecraft/versions?releases_only=true");
      if (!ctx.isCurrent()) return;
      render(data.items || [], null, downloads);
    } catch (err) {
      if (!ctx.isCurrent()) return;
      render([], err, downloads);
    }
  }

  async function downloadVersion(versionId) {
    // Prompt user to pick a target server.
    const serverId = window.prompt(
      `Download Minecraft ${versionId} server JAR for which server ID?`
    );
    if (!serverId || !Number.isInteger(Number(serverId))) {
      return;
    }

    const sid = Number(serverId);
    downloads.set(versionId, { status: "downloading", progress: 0 });
    if (!ctx.isCurrent()) return;
    render(versions, null, downloads);

    try {
      const result = await api(`/minecraft/versions/${versionId}/download`, {
        method: "POST",
        body: { server_id: sid },
      });
      downloads.set(versionId, { status: "done", progress: 100 });
      toast(`Downloaded Minecraft ${result.version} server JAR.`);
      if (!ctx.isCurrent()) return;
      render(versions, null, downloads);
    } catch (error) {
      downloads.set(versionId, { status: "error", progress: 0 });
      toast(error.message || "Download failed.", "error");
      if (!ctx.isCurrent()) return;
      render(versions, null, downloads);
    }
  }
}
