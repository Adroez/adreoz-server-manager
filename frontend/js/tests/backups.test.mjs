/* Frontend tests for the Phase 5 backup and restore view.
 *
 * jsdom is not available in this environment, so these tests exercise the
 * DOM-free helpers that drive the backup list, actions, settings form, and
 * error/confirmation states. The view itself only renders the values returned
 * by these helpers.
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { formatBytes } from "../core/format.js";
import {
  SUPPORTED_BACKUP_INTERVALS,
  backupActionErrorMessage,
  backupStatusClass,
  backupStatusLabel,
  canCreateBackup,
  canRestoreBackup,
  describeBackupList,
  formatRetentionSummary,
  intervalLabel,
  validateBackupSettings,
} from "../views/backup-utils.js";

describe("backups - status presentation", () => {
  it("labels and styles every known status", () => {
    assert.equal(backupStatusLabel("completed"), "Completed");
    assert.equal(backupStatusLabel("failed"), "Failed");
    assert.equal(backupStatusLabel("creating"), "Creating");
    assert.equal(backupStatusLabel("restoring"), "Restoring");

    assert.match(backupStatusClass("completed"), /badge-active/);
    assert.match(backupStatusClass("failed"), /badge-danger/);
    assert.match(backupStatusClass("creating"), /badge-warn/);
    assert.match(backupStatusClass("restoring"), /badge-warn/);
  });

  it("falls back safely for an unknown status", () => {
    assert.equal(backupStatusLabel("mystery"), "Unknown");
    assert.equal(backupStatusClass("mystery"), "badge");
  });
});

describe("backups - action availability", () => {
  it("only allows creation while the server is stopped", () => {
    assert.equal(canCreateBackup("stopped"), true);
    assert.equal(canCreateBackup("running"), false);
    assert.equal(canCreateBackup("starting"), false);
    assert.equal(canCreateBackup("stopping"), false);
    assert.equal(canCreateBackup("error"), false);
  });

  it("only allows restore for a stopped server and completed backup", () => {
    assert.equal(canRestoreBackup("stopped", "completed"), true);
    assert.equal(canRestoreBackup("running", "completed"), false);
    assert.equal(canRestoreBackup("stopped", "failed"), false);
    assert.equal(canRestoreBackup("stopped", "creating"), false);
  });

  it("download is only offered for completed backups", () => {
    const [completed] = describeBackupList([
      { id: 1, status: "completed", size_bytes: 10, created_at: "2026-01-01T00:00:00Z" },
    ]);
    const [failed] = describeBackupList([
      { id: 2, status: "failed", size_bytes: 0, created_at: "2026-01-01T00:00:00Z" },
    ]);
    assert.equal(completed.downloadable, true);
    assert.equal(failed.downloadable, false);
  });
});

describe("backups - list rendering model", () => {
  const items = [
    {
      id: 1,
      unique_id: "11111111-aaaa-bbbb-cccc-000000000001",
      filename: "server-1-old.tar.gz",
      size_bytes: 1024,
      status: "completed",
      error: null,
      created_at: "2026-01-01T00:00:00Z",
    },
    {
      id: 2,
      unique_id: "22222222-aaaa-bbbb-cccc-000000000002",
      filename: "server-1-new.tar.gz",
      size_bytes: 2048,
      status: "failed",
      error: "The backup operation failed.",
      created_at: "2026-01-02T00:00:00Z",
    },
  ];

  it("maps API records to view models", () => {
    const rows = describeBackupList(items);
    assert.equal(rows.length, 2);
    assert.equal(rows[0].id, 2);
    assert.equal(rows[0].label, "Backup 22222222");
    assert.equal(rows[0].statusLabel, "Failed");
    assert.equal(rows[0].error, "The backup operation failed.");
    assert.equal(rows[1].sizeBytes, 1024);
  });

  it("sorts newest backups first", () => {
    const rows = describeBackupList(items);
    assert.deepEqual(rows.map((row) => row.id), [2, 1]);
  });

  it("tolerates empty and malformed lists", () => {
    assert.deepEqual(describeBackupList([]), []);
    assert.deepEqual(describeBackupList(null), []);
    assert.deepEqual(
      describeBackupList([{ id: "not-a-number" }, {}, { id: 5, status: "completed" }]).map(
        (row) => row.id
      ),
      [5]
    );
  });

  it("formats sizes with the shared byte formatter", () => {
    const [row] = describeBackupList(items);
    assert.equal(formatBytes(row.sizeBytes), "2.0 KB");
  });
});

describe("backups - automatic settings validation", () => {
  it("accepts a supported configuration", () => {
    const result = validateBackupSettings({
      enabled: true,
      interval_hours: "12",
      max_backups: "5",
    });
    assert.equal(result.valid, true);
    assert.deepEqual(result.errors, []);
    assert.deepEqual(result.value, {
      enabled: true,
      interval_hours: 12,
      max_backups: 5,
    });
  });

  it("rejects unsupported intervals", () => {
    const result = validateBackupSettings({
      enabled: true,
      interval_hours: 2,
      max_backups: 5,
    });
    assert.equal(result.valid, false);
    assert.match(result.errors[0], /Interval must be one of/);
  });

  it("rejects out-of-range retention", () => {
    for (const retention of [0, 101, -1, 2.5, "many"]) {
      const result = validateBackupSettings({
        enabled: true,
        interval_hours: 24,
        max_backups: retention,
      });
      assert.equal(result.valid, false, `retention ${retention} should be rejected`);
    }
  });

  it("exposes the supported intervals used by the select", () => {
    assert.deepEqual(SUPPORTED_BACKUP_INTERVALS, [1, 6, 12, 24, 72, 168]);
    assert.equal(intervalLabel(24), "Every 24 hours (daily)");
    assert.equal(intervalLabel(168), "Every 168 hours (weekly)");
    assert.equal(intervalLabel(6), "Every 6 hours");
  });

  it("describes the retention rule", () => {
    assert.match(formatRetentionSummary(1), /newest 1 backup/);
    assert.match(formatRetentionSummary(10), /newest 10 backups/);
    assert.match(formatRetentionSummary(0), /newest 1 backup/);
    assert.match(formatRetentionSummary(1000), /newest 100 backups/);
  });
});

describe("backups - error states", () => {
  it("explains concurrent operation conflicts", () => {
    assert.match(backupActionErrorMessage(409), /already running/i);
  });

  it("explains authorization and not-found errors", () => {
    assert.match(backupActionErrorMessage(403), /do not have access/i);
    assert.match(backupActionErrorMessage(404), /could not be found/i);
  });

  it("returns an empty fallback so the API message can be used", () => {
    assert.equal(backupActionErrorMessage(500), "");
    assert.equal(backupActionErrorMessage(0), "");
  });
});
