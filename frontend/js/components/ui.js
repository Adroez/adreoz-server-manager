/* Shared visual building blocks. */

import { avatar as avatarNode, el } from "../core/dom.js";
import {
  formatDateTime,
  formatPercent,
  relativeTime,
} from "../core/format.js";
import { icon } from "./icons.js";

export function pageHeader({ eyebrow, title, description, actions = [] }) {
  return el(
    "div",
    { class: "page-head" },
    el(
      "div",
      { class: "page-head-copy" },
      eyebrow ? el("span", { class: "eyebrow", text: eyebrow }) : null,
      el("h2", { text: title }),
      description ? el("p", { class: "page-desc", text: description }) : null
    ),
    actions.length > 0
      ? el("div", { class: "page-actions" }, ...actions)
      : null
  );
}

export function avatarBlock(name, large = false) {
  return avatarNode(name, large);
}

export function roleBadge(role) {
  const isAdmin = role === "admin";
  return el("span", {
    class: `badge ${isAdmin ? "badge-admin" : "badge-user"}`,
    text: isAdmin ? "Administrator" : "User",
  });
}

export function statusBadge(active) {
  return el(
    "span",
    { class: `badge ${active ? "badge-active" : "badge-inactive"}` },
    el("span", {
      class: `status-dot${active ? "" : " is-offline"}`,
      attrs: { "aria-hidden": "true" },
    }),
    document.createTextNode(active ? "Active" : "Disabled")
  );
}

export function infoRow(label, value) {
  return el(
    "div",
    { class: "info-row" },
    el("dt", { class: "info-label", text: label }),
    el("dd", { class: "info-value" }, value)
  );
}

export function progressRow(label, percent, detail = null) {
  const rounded = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
  const tone =
    rounded >= 90 ? " is-danger" : rounded >= 75 ? " is-warn" : "";
  return el(
    "div",
    { class: "progress-row" },
    el(
      "div",
      { class: "progress-meta" },
      el("span", { text: label }),
      el("strong", { text: formatPercent(rounded) })
    ),
    el("progress", {
      class: `progress${tone}`,
      max: 100,
      value: rounded,
      attrs: { "aria-label": `${label} usage` },
    }),
    detail ? el("div", { class: "stat-foot", text: detail }) : null
  );
}

const ACTION_META = {
  "setup.completed": ["Panel initialized", "shield-check"],
  "auth.login_succeeded": ["Signed in", "log-in"],
  "auth.login_failed": ["Failed sign-in", "alert-triangle"],
  "auth.logout": ["Signed out", "logout"],
  "auth.password_changed": ["Password changed", "key"],
  "auth.other_sessions_revoked": ["Other sessions revoked", "shield"],
  "user.created": ["User created", "user-check"],
  "user.updated": ["User updated", "user"],
  "user.deleted": ["User deleted", "user-x"],
  "user.password_reset": ["Password reset", "key"],
  "settings.updated": ["Settings updated", "settings"],
};

export function activityItem(entry) {
  const [label, iconName] = ACTION_META[entry.action] || [entry.action, "activity"];
  const metaParts = [entry.actor_username ? `@${entry.actor_username}` : "System"];
  if (entry.action === "auth.login_failed" && entry.detail) {
    metaParts.push(entry.detail);
  }
  return el(
    "div",
    { class: "activity-item", attrs: { title: entry.detail || label } },
    el("div", { class: "activity-icon" }, icon(iconName, 14)),
    el(
      "div",
      { class: "activity-main" },
      el("div", { class: "activity-title", text: label }),
      el("div", { class: "activity-meta", text: metaParts.join(" · ") })
    ),
    el("div", {
      class: "activity-time",
      text: relativeTime(entry.created_at),
      attrs: { title: formatDateTime(entry.created_at) },
    })
  );
}

export function emptyState({ iconName = "sparkles", title, text, action = null }) {
  return el(
    "div",
    { class: "empty-state" },
    el("div", { class: "empty-icon" }, icon(iconName, 22)),
    el("div", { class: "empty-title", text: title }),
    text ? el("p", { class: "empty-text", text }) : null,
    action
  );
}

export function loadingState() {
  const header = el(
    "div",
    { class: "page-head" },
    el(
      "div",
      { class: "page-head-copy" },
      el("div", {
        class: "skeleton skeleton-line is-short",
        attrs: { "aria-hidden": "true" },
      }),
      el("div", {
        class: "skeleton skeleton-line is-medium",
        attrs: { "aria-hidden": "true" },
      })
    )
  );
  const cards = el(
    "div",
    { class: "loading-grid" },
    ...Array.from({ length: 4 }, () =>
      el("div", { class: "skeleton loading-card", attrs: { "aria-hidden": "true" } })
    )
  );
  return el(
    "div",
    { class: "stack-md", attrs: { "aria-busy": "true", "aria-label": "Loading" } },
    header,
    cards,
    el("div", { class: "skeleton loading-panel", attrs: { "aria-hidden": "true" } })
  );
}

export function errorState(message, onRetry = null) {
  return el(
    "div",
    { class: "error-panel", attrs: { role: "alert" } },
    el("div", { class: "error-icon" }, icon("alert-triangle", 22)),
    el("h3", { text: "Something went wrong" }),
    el("p", { text: message }),
    onRetry
      ? el(
          "button",
          {
            class: "btn btn-secondary btn-sm",
            type: "button",
            on: { click: onRetry },
          },
          icon("refresh", 15),
          document.createTextNode("Try again")
        )
      : null
  );
}

export function actionButton({ label, iconName, handler, variant = "ghost", size = "xs", title, disabled = false }) {
  return el(
    "button",
    {
      class: `btn btn-${variant} btn-${size}`,
      type: "button",
      disabled,
      attrs: { title: title || label },
      on: { click: handler },
    },
    icon(iconName, 13),
    document.createTextNode(label)
  );
}
