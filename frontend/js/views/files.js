/* Server file manager view. */

import { api } from "../core/api.js";
import { el } from "../core/dom.js";
import { getState } from "../core/store.js";
import { relativeTime } from "../core/format.js";
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

const TYPE_ICON = {
  directory: "folder",
  file: "file",
};

export async function renderFileManager(view, ctx) {
  // Extract server ID from hash: #/servers/123/files or #/servers/123/files/
  const hash = window.location.hash.slice(1);
  const parts = hash.split("/");
  const serverId = Number(parts[2]);
  const queryStart = hash.indexOf("?");
  const query = queryStart >= 0 ? hash.slice(queryStart + 1) : "";
  const initialPath = new URLSearchParams(query).get("path") || "";

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

  // --- File manager state ---
  let currentPath = "";
  let breadcrumbs = []; // { name, path }

  const breadcrumbContainer = el("div", { class: "file-breadcrumbs" });
  const pathInput = el("input", {
    class: "input",
    id: "file-manager-path",
    type: "text",
    placeholder: "Enter path…",
    disabled: true,
    attrs: { readonly: true },
  });
  const pathInputField = el(
    "div",
    { class: "field" },
    el("label", { attrs: { for: "file-manager-path" }, text: "Current path" }),
    pathInput
  );

  const table = el("table", { class: "table is-cards" });
  const tbody = el("tbody");
  table.appendChild(tbody);

  const uploadInput = el("input", {
    class: "input",
    id: "file-upload-input",
    type: "file",
    attrs: { multiple: true, hidden: true },
  });
  const uploadButton = el(
    "button",
    {
      class: "btn btn-secondary btn-sm",
      type: "button",
      disabled: true,
      on: { click: () => uploadInput.click() },
    },
    icon("upload", 13),
    document.createTextNode("Upload")
  );

  const newFolderButton = el(
    "button",
    {
      class: "btn btn-ghost btn-sm",
      type: "button",
      on: { click: () => promptNewFolder() },
    },
    icon("folder", 13),
    document.createTextNode("New folder")
  );

  const newFileButton = el(
    "button",
    {
      class: "btn btn-ghost btn-sm",
      type: "button",
      on: { click: () => promptNewFile() },
    },
    icon("file-text", 13),
    document.createTextNode("New file")
  );

  const upButton = el(
    "button",
    {
      class: "btn btn-ghost btn-sm",
      type: "button",
      disabled: true,
      on: { click: () => goUp() },
    },
    icon("chevron-right", 13),
    document.createTextNode("Up")
  );

  const backButton = el(
    "button",
    {
      class: "btn btn-secondary btn-sm",
      type: "button",
      on: { click: () => (window.location.hash = `#/servers/${serverId}`) },
    },
    icon("arrow-right", 13),
    document.createTextNode("Back to server")
  );

  if (!ctx.isCurrent()) return;

  render(server);

  function render(data) {
    const headerActions = [];
    headerActions.push(
      actionButton({
        label: "Console",
        iconName: "terminal",
        handler: () => {
          window.location.hash = `#/servers/${data.id}/console`;
        },
      })
    );

    const sections = [
      pageHeader({
        eyebrow: "File manager · " + data.name,
        title: data.name,
        description: `Browse and edit files in ${data.directory}.`,
        actions: [
          el(
            "div",
            { class: "file-manager-toolbar" },
            upButton,
            newFolderButton,
            newFileButton,
            uploadButton,
            uploadInput,
            backButton
          )
        ],
      }),
      el(
        "div",
        { class: "file-manager-card" },
        el(
          "div",
          { class: "file-manager-header" },
          breadcrumbContainer,
          pathInputField
        ),
        el("div", { class: "file-table-wrap" }, table)
      ),
    ];

    view.replaceChildren(el("div", { class: "stack-md" }, ...sections));

    // Load the requested directory (or the root).
    loadDirectory(initialPath);
  }

  function updateBreadcrumbs(path) {
    breadcrumbContainer.replaceChildren();
    breadcrumbs = [];

    if (path) {
      const parts = path.split("/");
      let acc = "";
      for (const part of parts) {
        acc = acc ? `${acc}/${part}` : part;
        breadcrumbs.push({ name: part, path: acc });
      }
    }

    // Home / root button.
    const rootBtn = el(
      "button",
      {
        class: "btn btn-ghost btn-xs",
        type: "button",
        on: { click: () => loadDirectory("") },
      },
      icon("home", 12),
      document.createTextNode("Home")
    );
    breadcrumbContainer.appendChild(rootBtn);

    for (let i = 0; i < breadcrumbs.length; i++) {
      const crumb = breadcrumbs[i];
      const isLast = i === breadcrumbs.length - 1;

      if (i > 0) {
        breadcrumbContainer.appendChild(
          el("span", { class: "file-breadcrumb-sep", text: "/" })
        );
      }

      if (isLast) {
        breadcrumbContainer.appendChild(
          el("span", { class: "file-breadcrumb-current", text: crumb.name })
        );
      } else {
        const btn = el(
          "button",
          {
            class: "btn btn-ghost btn-xs",
            type: "button",
            on: { click: () => loadDirectory(crumb.path) },
          },
          document.createTextNode(crumb.name)
        );
        breadcrumbContainer.appendChild(btn);
      }
    }

    pathInput.value = path || "/";
    upButton.disabled = !path;
  }

  async function loadDirectory(path) {
    try {
      const params = new URLSearchParams();
      if (path) params.set("fp", path);
      const data = await api(`/servers/${serverId}/files?${params.toString()}`);

      if (!ctx.isCurrent()) return;

      currentPath = data.path;
      updateBreadcrumbs(data.path);
      renderDirectory(data.items);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function renderDirectory(items) {
    tbody.replaceChildren();

    if (items.length === 0) {
      tbody.append(
        el(
          "tr",
          {},
          el(
            "td",
            { class: "table-empty", attrs: { colspan: "5" } },
            emptyState({
              iconName: "folder",
              title: currentPath ? "Empty folder" : "No files yet",
              text: currentPath
                ? "This folder is empty. Create a new file or folder to get started."
                : "No files in this server directory yet.",
            })
          )
        )
      );
      return;
    }

    for (const item of items) {
      const isDir = item.type === "directory";
      const row = el("tr", {});

      // Name / icon cell.
      const nameCell = el("td", { attrs: { "data-label": "Name" } });
      const nameLink = el(
        "a",
        {
          class: "file-name-link",
          attrs: { href: "#" },
          on: {
            click: (event) => {
              event.preventDefault();
              if (isDir) {
                const nextPath = currentPath
                  ? `${currentPath}/${item.name}`
                  : item.name;
                loadDirectory(nextPath);
              } else {
                viewFile(item.name);
              }
            },
          },
        },
        el("span", { class: "file-icon" }, icon(TYPE_ICON[item.type], 14)),
        el("span", { class: "file-name", text: item.name })
      );
      nameCell.appendChild(nameLink);
      row.appendChild(nameCell);

      // Type cell.
      const typeCell = el(
        "td",
        { attrs: { "data-label": "Type" } },
        el("span", { class: "badge", text: item.type.charAt(0).toUpperCase() + item.type.slice(1) })
      );
      row.appendChild(typeCell);

      // Size cell.
      const sizeCell = el(
        "td",
        { attrs: { "data-label": "Size" } },
        isDir
          ? el("span", { class: "dim", text: "—" })
          : el("span", { text: formatBytes(item.size) })
      );
      row.appendChild(sizeCell);

      // Modified cell.
      const modifiedCell = el(
        "td",
        {
          attrs: { "data-label": "Modified" },
          text: relativeTime(item.modified_at),
        }
      );
      row.appendChild(modifiedCell);

      // Actions cell.
      const actionsCell = el("td", { attrs: { "data-label": "Actions" } });
      const actions = el("div", { class: "cell-actions" });

      actions.append(
        actionButton({
          label: isDir ? "Open" : "Download",
          iconName: isDir ? "folder" : "download",
          handler: (event) => {
            event.preventDefault();
            if (isDir) {
              const nextPath = currentPath
                ? `${currentPath}/${item.name}`
                : item.name;
              loadDirectory(nextPath);
            } else {
              downloadFile(item.name);
            }
          },
        })
      );

      actions.append(
        actionButton({
          label: "Rename",
          iconName: "edit",
          handler: (event) => {
            event.preventDefault();
            promptRename(item.name, isDir);
          },
          variant: "ghost",
          size: "xs",
        })
      );

      actions.append(
        actionButton({
          label: "Delete",
          iconName: "trash",
          variant: "danger",
          handler: (event) => {
            event.preventDefault();
            promptDelete(item.name, isDir);
          },
          size: "xs",
        })
      );

      actionsCell.appendChild(actions);
      row.appendChild(actionsCell);

      tbody.appendChild(row);
    }
  }

  // --- Actions ---

  function viewFile(name) {
    const nextPath = currentPath ? `${currentPath}/${name}` : name;
    showFileEditor(serverId, nextPath);
  }

  async function downloadFile(name) {
    const nextPath = currentPath ? `${currentPath}/${name}` : name;

    try {
      const response = await fetch(
        `/api/servers/${serverId}/files/download?fp=${encodeURIComponent(nextPath)}`,
        { credentials: "same-origin" }
      );

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || "Download failed.");
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function promptNewFolder() {
    const name = window.prompt("Enter folder name:");
    if (!name || !name.trim()) return;

    const folderName = name.trim();
    const nextPath = currentPath ? `${currentPath}/${folderName}` : folderName;

    try {
      await api("/servers/" + serverId + "/files/folder", {
        method: "POST",
        body: { path: nextPath },
      });
      toast(`Created folder '${folderName}'.`);
      await loadDirectory(currentPath);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function promptNewFile() {
    const name = window.prompt("Enter file name:");
    if (!name || !name.trim()) return;

    const fileName = name.trim();
    const nextPath = currentPath ? `${currentPath}/${fileName}` : fileName;

    try {
      await api("/servers/" + serverId + "/files/write", {
        method: "POST",
        body: { path: nextPath, content: "" },
      });
      toast(`Created file '${fileName}'.`);
      await loadDirectory(currentPath);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function promptRename(name, isDir) {
    const newName = window.prompt("Enter new name:", name);
    if (!newName || !newName.trim() || newName.trim() === name) return;

    const newNameTrimmed = newName.trim();
    const oldPath = currentPath ? `${currentPath}/${name}` : name;
    const newPath = currentPath
      ? `${currentPath}/${newNameTrimmed}`
      : newNameTrimmed;

    try {
      await api("/servers/" + serverId + "/files/rename", {
        method: "POST",
        body: { old_path: oldPath, new_path: newPath },
      });
      toast(`Renamed '${name}' to '${newNameTrimmed}'.`);
      await loadDirectory(currentPath);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function promptDelete(name, isDir) {
    const typeLabel = isDir ? "folder" : "file";
    const confirmed = await confirmDialog({
      title: `Delete ${typeLabel}`,
      message: `Permanently delete "${name}"? This cannot be undone.`,
      confirmLabel: "Delete",
      danger: true,
      iconName: "trash",
    });
    if (!confirmed) return;

    const nextPath = currentPath ? `${currentPath}/${name}` : name;

    try {
      await api("/servers/" + serverId + "/files", {
        method: "DELETE",
        body: { path: nextPath },
      });
      toast(`Deleted '${name}'.`);
      await loadDirectory(currentPath);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function goUp() {
    if (!currentPath) return;
    const parts = currentPath.split("/");
    parts.pop();
    await loadDirectory(parts.join("/"));
  }

  async function uploadFile() {
    const files = uploadInput.files;
    if (!files || files.length === 0) return;

    let failures = 0;
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      const targetPath = currentPath
        ? `${currentPath}/${file.name}`
        : file.name;
      const params = new URLSearchParams({
        fp: targetPath,
        filename: file.name,
      });

      try {
        const response = await fetch(
          `/api/servers/${serverId}/files/upload?${params.toString()}`,
          {
            method: "POST",
            credentials: "same-origin",
            headers: {
              "Content-Type": "application/octet-stream",
              "X-CSRF-Token": getState().csrfToken || "",
            },
            body: file,
          }
        );

        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          throw new Error(errorData.detail || "Upload failed.");
        }
      } catch (error) {
        failures += 1;
        toast(`Failed to upload '${file.name}': ${error.message}`, "error");
      }
    }

    uploadInput.value = "";
    if (failures === 0) {
      toast("Upload complete.");
    }
    if (ctx.isCurrent()) await loadDirectory(currentPath);
  }

  uploadInput.addEventListener("change", uploadFile);

  // --- Lifecycle ---
  // No WebSocket to clean up here.
}

function formatBytes(bytes) {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

function showFileEditor(serverId, path) {
  // Navigate to the file editor view via hash.
  window.location.hash = `#/servers/${serverId}/files/edit?path=${encodeURIComponent(path)}`;
}
