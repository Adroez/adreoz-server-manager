import test from "node:test";
import assert from "node:assert/strict";

import {
  formatBytes,
  formatDate,
  formatDateTime,
  formatDuration,
  formatNumber,
  formatPercent,
  greeting,
  initials,
  pluralize,
  relativeTime,
} from "../core/format.js";

test("formatBytes scales units", () => {
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(1536), "1.5 KB");
  assert.equal(formatBytes(1024 ** 3), "1.0 GB");
  assert.equal(formatBytes(null), "—");
});

test("formatDuration picks sensible units", () => {
  assert.equal(formatDuration(0), "0s");
  assert.equal(formatDuration(45), "45s");
  assert.equal(formatDuration(125), "2m");
  assert.equal(formatDuration(3661), "1h 1m");
  assert.equal(formatDuration(90061), "1d 1h");
  assert.equal(formatDuration(null), "—");
});

test("relativeTime handles past and future", () => {
  const now = new Date("2026-01-01T12:00:00Z").getTime();
  assert.equal(relativeTime("2026-01-01T11:59:30Z", now), "just now");
  assert.equal(relativeTime("2026-01-01T11:58:00Z", now), "2m ago");
  assert.equal(relativeTime("2026-01-01T14:00:00Z", now), "in 2h");
  assert.equal(relativeTime(null, now), "—");
});

test("initials handles usernames", () => {
  assert.equal(initials("owner"), "OW");
  assert.equal(initials("player-one"), "PO");
  assert.equal(initials("a.b.c"), "AC");
  assert.equal(initials(""), "?");
});

test("greeting changes by local hour", () => {
  assert.equal(greeting(new Date(2026, 0, 1, 4, 0)), "Still up");
  assert.equal(greeting(new Date(2026, 0, 1, 9, 0)), "Good morning");
  assert.equal(greeting(new Date(2026, 0, 1, 15, 0)), "Good afternoon");
  assert.equal(greeting(new Date(2026, 0, 1, 20, 0)), "Good evening");
});

test("number and percent formatting", () => {
  assert.equal(formatPercent(42.4), "42%");
  assert.equal(formatPercent(42.44, 1), "42.4%");
  assert.equal(formatPercent(null), "—");
  assert.equal(formatNumber(1234567), "1,234,567");
});

test("date formatting returns placeholders for invalid data", () => {
  assert.equal(formatDate(null), "—");
  assert.equal(formatDateTime("not-a-date"), "—");
  assert.notEqual(formatDate("2026-01-01T00:00:00Z"), "—");
});

test("pluralize", () => {
  assert.equal(pluralize(1, "account"), "account");
  assert.equal(pluralize(2, "account"), "accounts");
  assert.equal(pluralize(2, "phase", "phases"), "phases");
});
