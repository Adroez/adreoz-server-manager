/* File editor view — view and edit text files. */

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

export async function renderFileEdit(view, ctx) {
  // Extract server ID and path from hash: #/servers/123/files/edit?path=...
  const hash = window.location.hash.slice(1);
  const parts = hash.split("/");
  const serverId = Number(parts[1]);
  const queryStart = hash.indexOf("?");
  const query = queryStart >= 0 ? hash.slice(queryStart + 1) : "";
  const params = new URLSearchParams(query);
  const filePath = params.get("path") || "";

  if (!Number.isInteger(serverId)) {
    view.replaceChildren(
      errorState("Invalid server ID.", () => (window.location.hash = "#/servers"))
    );
    return;
  }

  if (!filePath) {
    view.replaceChildren(
      errorState("No file specified.", () => (window.location.hash = "#/servers/" + serverId + "/files"))
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

  let fileContent;
  let mimeType = "text/plain";
  let fileSize = 0;
  let isDirty = false;

  try {
    const data = await api(
      `/servers/${serverId}/files/read?path=${encodeURIComponent(filePath)}`
    );
    fileContent = data.content;
    mimeType = data.mime_type || "text/plain";
    fileSize = data.size || 0;
  } catch (error) {
    if (!ctx.isCurrent()) return;
    view.replaceChildren(
      errorState(error.message, () => (window.location.hash = "#/servers/" + serverId + "/files"))
    );
    return;
  }

  if (!ctx.isCurrent()) return;
  render(server);

  function render(data) {
    const isBinary = mimeType !== "text/plain" && !mimeType.startsWith("text/");

    const headerActions = [];

    if (!isBinary) {
      headerActions.push(
        actionButton({
          label: "Save",
          iconName: "save",
          handler: () => saveFile(),
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
    }

    headerActions.push(
      actionButton({
        label: "Back to files",
        iconName: "folder",
        handler: () => {
          window.location.hash = `#/servers/${serverId}/files${filePath ? "?path=" + encodeURIComponent(filePath) : ""}`;
        },
      })
    );

    const editor = el("textarea", {
      class: "file-editor",
      id: "file-editor",
      value: fileContent,
      disabled: isBinary,
      attrs: {
        spellcheck: "false",
        wrap: "off",
        readonly: isBinary,
      },
    });

    const infoBar = el(
      "div",
      { class: "file-info-bar" },
      el("span", { class: "file-info-path", text: filePath }),
      el("span", { class: "file-info-meta", text: `${fileSize} bytes · ${mimeType}` }),
      el("span", { class: "file-info-dirty", text: "", id: "file-dirty-indicator" })
    );

    const binaryNotice = isBinary
      ? el(
          "div",
          { class: "binary-notice" },
          el("span", { class: "binary-icon" }, icon("alert-triangle", 14)),
          el("span", { text: "Binary file detected. Editing is disabled." })
        )
      : null;

    const sections = [
      pageHeader({
        eyebrow: "File editor · " + data.name,
        title: filePath,
        description: `Edit ${filePath} on server "${data.name}".`,
        actions: headerActions,
      }),
      el(
        "div",
        { class: "file-editor-card" },
        infoBar,
        binaryNotice,
        el("div", { class: "file-editor-wrap" }, editor)
      ),
    ];

    view.replaceChildren(el("div", { class: "stack-md" }, ...sections));

    // Set up dirty tracking.
    if (!isBinary) {
      editor.addEventListener("input", () => {
        isDirty = editor.value !== fileContent;
        const indicator = document.getElementById("file-dirty-indicator");
        if (indicator) {
          indicator.textContent = isDirty ? "• Unsaved changes" : "";
        }
      });
    }
  }

  async function saveFile() {
    const editor = document.getElementById("file-editor");
    if (!editor) return;

    const newContent = editor.value;

    try {
      const result = await api(`/servers/${serverId}/files/write`, {
        method: "POST",
        body: { path: filePath, content: newContent },
      });
      fileContent = newContent;
      isDirty = false;
      const indicator = document.getElementById("file-dirty-indicator");
      if (indicator) indicator.textContent = "";
      toast(`Saved '${filePath}'.`);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function discardChanges() {
    const editor = document.getElementById("file-editor");
    if (!editor) return;

    editor.value = fileContent;
    isDirty = false;
    const indicator = document.getElementById("file-dirty-indicator");
    if (indicator) indicator.textContent = "";
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
