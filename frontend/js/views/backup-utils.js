/* Pure helpers for the backups view, kept DOM-free for unit tests. */

export const SUPPORTED_BACKUP_INTERVALS = [1, 6, 12, 24, 72, 168];

export const BACKUP_STATUS_LABELS = {
  creating: "Creating",
  completed: "Completed",
  failed: "Failed",
  restoring: "Restoring",
};

const STATUS_CLASS = {
  completed: "badge badge-active",
  failed: "badge badge-danger",
  creating: "badge badge-warn",
  restoring: "badge badge-warn",
};

export function backupStatusLabel(status) {
  return BACKUP_STATUS_LABELS[status] || "Unknown";
}

export function backupStatusClass(status) {
  return STATUS_CLASS[status] || "badge";
}

export function canCreateBackup(serverStatus) {
  return serverStatus === "stopped";
}

export function canRestoreBackup(serverStatus, backupStatus) {
  return serverStatus === "stopped" && backupStatus === "completed";
}

export function intervalLabel(hours) {
  if (hours === 24) return "Every 24 hours (daily)";
  if (hours === 72) return "Every 72 hours (3 days)";
  if (hours === 168) return "Every 168 hours (weekly)";
  return `Every ${hours} hour${hours === 1 ? "" : "s"}`;
}

export function formatRetentionSummary(maxBackups) {
  const count = Math.max(1, Math.min(Number(maxBackups) || 1, 100));
  return `Keeps the newest ${count} backup${count === 1 ? "" : "s"} for this server. Older backups are removed automatically.`;
}

/**
 * Validate the automatic-backup form before sending it to the API.
 * Returns `{ valid, errors, value }` so the view can render inline feedback.
 */
export function validateBackupSettings({ enabled, interval_hours, max_backups }) {
  const errors = [];
  const interval = Number(interval_hours);
  const retention = Number(max_backups);

  if (!SUPPORTED_BACKUP_INTERVALS.includes(interval)) {
    errors.push(
      `Interval must be one of: ${SUPPORTED_BACKUP_INTERVALS.join(", ")} hours.`
    );
  }
  if (!Number.isInteger(retention) || retention < 1 || retention > 100) {
    errors.push("Retention must be a whole number between 1 and 100.");
  }

  return {
    valid: errors.length === 0,
    errors,
    value: {
      enabled: Boolean(enabled),
      interval_hours: interval,
      max_backups: retention,
    },
  };
}

export function backupActionErrorMessage(status) {
  if (status === 409) {
    return "Another backup or restore is already running for this server. Try again in a moment.";
  }
  if (status === 403) {
    return "You do not have access to this server's backups.";
  }
  if (status === 404) {
    return "That backup could not be found. Reload the list and try again.";
  }
  return "";
}

/**
 * Convert the API list into stable view models.
 * Newest first; unknown fields are tolerated.
 */
export function describeBackupList(items) {
  if (!Array.isArray(items)) return [];
  return items
    .filter((item) => item && Number.isInteger(item.id))
    .map((item) => ({
      id: item.id,
      uniqueId: item.unique_id || String(item.id),
      label: `Backup ${item.unique_id ? item.unique_id.slice(0, 8) : item.id}`,
      filename: item.filename || "",
      sizeBytes: Number(item.size_bytes) || 0,
      createdAt: item.created_at || null,
      status: item.status || "unknown",
      statusLabel: backupStatusLabel(item.status),
      statusClass: backupStatusClass(item.status),
      error: item.error || null,
      downloadable: item.status === "completed",
      restorable: item.status === "completed",
    }))
    .sort((a, b) => {
      const left = a.createdAt ? Date.parse(a.createdAt) : 0;
      const right = b.createdAt ? Date.parse(b.createdAt) : 0;
      return right - left || b.id - a.id;
    });
}
