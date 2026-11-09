/* Server backup and restore view. */

import { api } from "../core/api.js";
import { el, setBusy } from "../core/dom.js";
import { formatBytes, formatDateTime, relativeTime } from "../core/format.js";
import { icon } from "../components/icons.js";
import { toast } from "../components/toast.js";
import { confirmDialog, openModal } from "../components/modal.js";
import {
  actionButton,
  emptyState,
  errorState,
  infoRow,
  loadingState,
  pageHeader,
} from "../components/ui.js";
import {
  SUPPORTED_BACKUP_INTERVALS,
  backupActionErrorMessage,
  canCreateBackup,
  canRestoreBackup,
  describeBackupList,
  formatRetentionSummary,
  intervalLabel,
  validateBackupSettings,
} from "./backup-utils.js";

export async function renderBackups(view, ctx) {
  const parts = window.location.hash.slice(1).split("/");
  const serverId = Number(parts[2]);

  if (!Number.isInteger(serverId)) {
    view.replaceChildren(
      errorState("Invalid server ID.", () => (window.location.hash = "#/servers"))
    );
    return;
  }

  view.replaceChildren(loadingState());

  let server = null;
  let backups = [];
  let settings = null;
  let listError = null;
  let settingsError = null;

  try {
    server = await api(`/servers/${serverId}`);
  } catch (error) {
    if (!ctx.isCurrent()) return;
    view.replaceChildren(
      errorState(error.message, () => (window.location.hash = "#/servers"))
    );
    return;
  }

  await refreshData();
  if (!ctx.isCurrent()) return;
  render();

  async function refreshData() {
    const [listResult, settingsResult] = await Promise.allSettled([
      api(`/servers/${serverId}/backups`),
      api(`/servers/${serverId}/backups/settings`),
    ]);

    if (listResult.status === "fulfilled") {
      backups = describeBackupList(listResult.value.items || []);
      listError = null;
    } else {
      backups = [];
      listError = listResult.reason.message;
    }

    if (settingsResult.status === "fulfilled") {
      settings = settingsResult.value;
      settingsError = null;
    } else {
      settingsError = settingsResult.reason.message;
    }

    try {
      server = await api(`/servers/${serverId}`);
    } catch {
      // Keep the previously loaded server snapshot.
    }
  }

  function render() {
    const stopped = canCreateBackup(server.status);
    const createButton = el(
      "button",
      {
        class: "btn btn-primary btn-sm",
        type: "button",
        disabled: !stopped,
        attrs: {
          title: stopped
            ? "Create a backup of this server"
            : "Stop the server before creating a backup",
        },
        on: { click: () => createBackup(createButton) },
      },
      icon("archive", 14),
      document.createTextNode("Create backup")
    );

    const headerActions = [
      createButton,
      actionButton({
        label: "Back to server",
        iconName: "server",
        handler: () => {
          window.location.hash = `#/servers/${serverId}`;
        },
      }),
    ];

    const notice = !stopped
      ? el(
          "div",
          { class: "backup-notice", attrs: { role: "status" } },
          icon("alert-triangle", 16),
          el(
            "div",
            {},
            el("strong", { text: "The server is not stopped." }),
            el("p", {
              text: "Backups can only be created or restored while the server is stopped. Stop the server, then come back here.",
            })
          )
        )
      : null;

    const sections = [
      pageHeader({
        eyebrow: "Backups · " + server.name,
        title: "Backups & restore",
        description: `Create, download, restore, and retain backups for ${server.name}. Archives are stored outside the server directory.`,
        actions: headerActions,
      }),
      notice,
      backupListCard(),
      settingsCard(),
    ].filter(Boolean);

    view.replaceChildren(el("div", { class: "stack-md" }, ...sections));
  }

  function backupListCard() {
    const body = el("div", { class: "card-body" });

    if (listError) {
      body.append(errorState(listError, () => retry()));
    } else if (backups.length === 0) {
      body.append(
        emptyState({
          iconName: "archive",
          title: "No backups yet",
          text: stoppedText(
            "Create a backup to capture this server's files and configuration."
          ),
        })
      );
    } else {
      body.append(backupTable());
    }

    return el(
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
            icon("archive", 16),
            document.createTextNode("Backup history")
          ),
          el("p", {
            class: "card-subtitle",
            text: `${backups.length} backup${backups.length === 1 ? "" : "s"} for this server.`,
          })
        ),
        actionButton({
          label: "Refresh",
          iconName: "refresh",
          handler: () => retry(),
        })
      ),
      body
    );
  }

  function stoppedText(text) {
    return canCreateBackup(server.status)
      ? text
      : "Stop the server before creating a backup.";
  }

  function backupTable() {
    const tbody = el("tbody");

    for (const backup of backups) {
      const row = el("tr", {});

      row.append(
        el(
          "td",
          { attrs: { "data-label": "Created" } },
          el("div", { class: "backup-time" }, document.createTextNode(formatDateTime(backup.createdAt))),
          el("div", {
            class: "backup-relative",
            text: relativeTime(backup.createdAt),
          })
        )
      );

      row.append(
        el(
          "td",
          { attrs: { "data-label": "Size" } },
          el("span", { class: "backup-size", text: formatBytes(backup.sizeBytes) })
        )
      );

      const statusCell = el(
        "td",
        { attrs: { "data-label": "Status" } },
        el("span", { class: backup.statusClass, text: backup.statusLabel })
      );
      if (backup.error) {
        statusCell.append(
          el("div", { class: "backup-error-text", text: backup.error })
        );
      }
      row.append(statusCell);

      const actions = el("div", { class: "cell-actions" });
      actions.append(
        actionButton({
          label: "Details",
          iconName: "info",
          handler: (event) => {
            event.preventDefault();
            showDetails(backup);
          },
        })
      );

      const downloadButton = actionButton({
        label: "Download",
        iconName: "download",
        handler: (event) => {
          event.preventDefault();
          downloadBackup(backup);
        },
      });
      downloadButton.disabled = !backup.downloadable;
      actions.append(downloadButton);

      const restoreButton = actionButton({
        label: "Restore",
        iconName: "rotate-ccw",
        handler: (event) => {
          event.preventDefault();
          restoreBackup(backup);
        },
      });
      restoreButton.disabled = !canRestoreBackup(server.status, backup.status);
      restoreButton.title = canRestoreBackup(server.status, backup.status)
        ? "Restore this backup"
        : "Stop the server and use a completed backup to restore";
      actions.append(restoreButton);

      actions.append(
        actionButton({
          label: "Delete",
          iconName: "trash",
          variant: "danger",
          handler: (event) => {
            event.preventDefault();
            deleteBackup(backup);
          },
        })
      );

      row.append(
        el("td", { attrs: { "data-label": "Actions" } }, actions)
      );
      tbody.append(row);
    }

    return el(
      "div",
      { class: "file-table-wrap" },
      el(
        "table",
        { class: "table is-cards" },
        el(
          "thead",
          {},
          el(
            "tr",
            {},
            el("th", { text: "Created" }),
            el("th", { text: "Size" }),
            el("th", { text: "Status" }),
            el("th", { class: "align-right", text: "Actions" })
          )
        ),
        tbody
      )
    );
  }

  function settingsCard() {
    const body = el("div", { class: "card-body" });

    if (settingsError) {
      body.append(errorState(settingsError, () => retry()));
    } else if (!settings) {
      body.append(el("p", { class: "dim", text: "Loading settings…" }));
    } else {
      const enabledInput = el("input", {
        type: "checkbox",
        id: "backup-auto-enabled",
        class: "backup-checkbox",
      });
      enabledInput.checked = Boolean(settings.enabled);

      const intervalSelect = el("select", {
        class: "input",
        id: "backup-auto-interval",
      });
      for (const hours of SUPPORTED_BACKUP_INTERVALS) {
        const option = el("option", {
          value: String(hours),
          text: intervalLabel(hours),
        });
        intervalSelect.append(option);
      }
      intervalSelect.value = String(settings.interval_hours);

      const retentionInput = el("input", {
        class: "input",
        id: "backup-auto-retention",
        type: "number",
        min: 1,
        max: 100,
        step: 1,
      });
      retentionInput.value = String(settings.max_backups);

      const summary = el("p", {
        class: "backup-retention-summary",
        text: formatRetentionSummary(settings.max_backups),
      });
      retentionInput.addEventListener("input", () => {
        summary.textContent = formatRetentionSummary(retentionInput.value);
      });

      const saveButton = el(
        "button",
        {
          class: "btn btn-primary btn-sm",
          type: "button",
          on: {
            click: () =>
              saveSettings(
                {
                  enabled: enabledInput.checked,
                  interval_hours: intervalSelect.value,
                  max_backups: retentionInput.value,
                },
                saveButton
              ),
          },
        },
        icon("save", 14),
        document.createTextNode("Save settings")
      );

      body.append(
        el(
          "div",
          { class: "backup-settings" },
          el(
            "div",
            { class: "backup-setting-toggle" },
            enabledInput,
            el(
              "label",
              { attrs: { for: "backup-auto-enabled" } },
              el("strong", { text: "Automatic backups" }),
              el("span", {
                text: "Create a backup on a schedule while the panel is running.",
              })
            )
          ),
          el(
            "div",
            { class: "backup-settings-grid" },
            el(
              "div",
              { class: "field" },
              el("label", {
                attrs: { for: "backup-auto-interval" },
                text: "Frequency",
              }),
              intervalSelect
            ),
            el(
              "div",
              { class: "field" },
              el("label", {
                attrs: { for: "backup-auto-retention" },
                text: "Maximum backups to keep",
              }),
              retentionInput
            )
          ),
          summary,
          el(
            "p",
            { class: "dim backup-settings-note" },
            document.createTextNode(
              "A due automatic backup is skipped while the server is running. Retention cleanup only removes older backups belonging to this server."
            )
          ),
          el("div", { class: "backup-settings-actions" }, saveButton)
        )
      );
    }

    return el(
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
            icon("clock", 16),
            document.createTextNode("Automatic backups")
          ),
          el("p", {
            class: "card-subtitle",
            text: "Configure scheduled backups and retention for this server.",
          })
        )
      ),
      body
    );
  }

  // --- Actions -------------------------------------------------------------

  async function retry() {
    view.replaceChildren(loadingState());
    await refreshData();
    if (ctx.isCurrent()) render();
  }

  async function createBackup(button) {
    if (!canCreateBackup(server.status)) {
      toast("Stop the server before creating a backup.", "error");
      return;
    }
    setBusy(button, true);
    try {
      const backup = await api(`/servers/${serverId}/backups`, { method: "POST" });
      toast(`Created backup ${backup.unique_id.slice(0, 8)}.`);
      await refreshData();
      if (ctx.isCurrent()) render();
    } catch (error) {
      toast(
        backupActionErrorMessage(error.status) || error.message,
        "error"
      );
    } finally {
      if (button.isConnected) setBusy(button, false);
    }
  }

  function showDetails(backup) {
    const body = el(
      "dl",
      { class: "info-rows" },
      infoRow("Backup ID", String(backup.id)),
      infoRow("Unique ID", backup.uniqueId),
      infoRow("Archive", backup.filename),
      infoRow("Size", formatBytes(backup.sizeBytes)),
      infoRow("Status", backup.statusLabel),
      infoRow("Created", formatDateTime(backup.createdAt)),
      backup.error ? infoRow("Error", backup.error) : null
    );

    openModal({
      title: "Backup details",
      subtitle: backup.label,
      body,
    });
  }

  async function downloadBackup(backup) {
    try {
      const response = await fetch(
        `/api/servers/${serverId}/backups/${backup.id}/download`,
        { credentials: "same-origin" }
      );
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(
          backupActionErrorMessage(response.status) ||
            data.detail ||
            "Download failed."
        );
      }
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = backup.filename || `backup-${backup.id}.tar.gz`;
      document.body.appendChild(anchor);
      anchor.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(anchor);
      toast("Backup download started.");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function restoreBackup(backup) {
    if (!canRestoreBackup(server.status, backup.status)) {
      toast("Stop the server and choose a completed backup to restore.", "error");
      return;
    }

    const confirmed = await confirmDialog({
      title: "Restore backup",
      message: `Restore "${backup.uniqueId}"? This replaces all current files in the server directory with the backup contents. The backup archive itself is kept. This cannot be undone.`,
      confirmLabel: "Restore backup",
      danger: true,
      iconName: "alert-triangle",
    });
    if (!confirmed) return;

    try {
      await api(`/servers/${serverId}/backups/${backup.id}/restore`, {
        method: "POST",
      });
      toast(`Restored backup ${backup.uniqueId.slice(0, 8)}.`);
      await refreshData();
      if (ctx.isCurrent()) render();
    } catch (error) {
      toast(
        backupActionErrorMessage(error.status) || error.message,
        "error"
      );
    }
  }

  async function deleteBackup(backup) {
    const confirmed = await confirmDialog({
      title: "Delete backup",
      message: `Permanently delete backup "${backup.uniqueId}"? The archive will be removed from disk. This cannot be undone.`,
      confirmLabel: "Delete backup",
      danger: true,
      iconName: "trash",
    });
    if (!confirmed) return;

    try {
      await api(`/servers/${serverId}/backups/${backup.id}`, {
        method: "DELETE",
      });
      toast("Backup deleted.");
      await refreshData();
      if (ctx.isCurrent()) render();
    } catch (error) {
      toast(
        backupActionErrorMessage(error.status) || error.message,
        "error"
      );
    }
  }

  async function saveSettings(values, button) {
    const result = validateBackupSettings(values);
    if (!result.valid) {
      toast(result.errors[0], "error");
      return;
    }

    setBusy(button, true);
    try {
      settings = await api(`/servers/${serverId}/backups/settings`, {
        method: "PATCH",
        body: result.value,
      });
      toast("Automatic backup settings saved.");
      await refreshData();
      if (ctx.isCurrent()) render();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      if (button.isConnected) setBusy(button, false);
    }
  }
}
