/* Overview dashboard. */

import { api } from "../core/api.js";
import { el, setBusy } from "../core/dom.js";
import {
  formatBytes,
  formatDateTime,
  formatDuration,
  formatPercent,
  greeting,
  relativeTime,
} from "../core/format.js";
import { getState } from "../core/store.js";
import { icon } from "../components/icons.js";
import { toast } from "../components/toast.js";
import {
  activityItem,
  avatarBlock,
  emptyState,
  errorState,
  infoRow,
  loadingState,
  pageHeader,
  progressRow,
  roleBadge,
} from "../components/ui.js";

function progressBar(percent) {
  const rounded = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
  const tone = rounded >= 90 ? " is-danger" : rounded >= 75 ? " is-warn" : "";
  return el("progress", {
    class: `progress${tone}`,
    max: 100,
    value: rounded,
    attrs: { "aria-label": "Usage" },
  });
}

function statCard({ iconName, label, value, suffix, foot, percent }) {
  return el(
    "article",
    { class: "card stat-card" },
    el(
      "div",
      { class: "stat-top" },
      el("span", { class: "stat-label", text: label }),
      el("span", { class: "stat-icon" }, icon(iconName, 15))
    ),
    el(
      "div",
      { class: "stat-value" },
      document.createTextNode(String(value)),
      suffix ? el("span", { class: "stat-suffix", text: suffix }) : null
    ),
    typeof percent === "number" ? progressBar(percent) : null,
    el("div", { class: "stat-foot", text: foot })
  );
}

function phaseCard({ index, title, description, state }) {
  const className =
    state === "done" ? "phase is-done" : state === "next" ? "phase is-next" : "phase";
  const stateLabel = state === "done" ? "Complete" : state === "next" ? "Next" : "Planned";
  return el(
    "div",
    { class: className },
    el(
      "div",
      { class: "phase-index" },
      el("span", { text: `Phase ${index}` }),
      el("span", { text: stateLabel })
    ),
    el("div", { class: "phase-name", text: title }),
    el("p", { class: "phase-desc", text: description })
  );
}

export async function renderDashboard(view, ctx) {
  view.replaceChildren(loadingState());

  let data;
  try {
    data = await api("/dashboard/summary");
  } catch (error) {
    if (!ctx.isCurrent()) return;
    view.replaceChildren(
      errorState(error.message, () => renderDashboard(view, ctx))
    );
    return;
  }

  if (!ctx.isCurrent()) return;
  render(data);

  function render(summary) {
    const { user, system, account, platform } = summary;
    const state = getState();

    const refreshButton = el(
      "button",
      {
        class: "btn btn-secondary btn-sm",
        type: "button",
        on: { click: () => refresh(refreshButton) },
      },
      icon("refresh", 14),
      document.createTextNode("Refresh")
    );

    const hero = el(
      "section",
      { class: "hero" },
      el(
        "div",
        { class: "hero-copy" },
        el("span", { class: "eyebrow", text: "Panel online" }),
        el(
          "h2",
          {},
          `${greeting()}, `,
          el("span", { text: user.username })
        ),
        el("p", {
          text: "Accounts, sessions, and control-room security are live. Server lifecycle controls — install, launch, and stream status — arrive in the next milestone.",
        }),
        el(
          "div",
          { class: "hero-meta" },
          roleBadge(user.role),
          el(
            "span",
            { class: "badge" },
            icon("server", 12),
            document.createTextNode("Local node")
          ),
          el(
            "span",
            { class: "badge" },
            icon("database", 12),
            document.createTextNode("SQLite")
          )
        )
      ),
      el(
        "div",
        { class: "hero-side" },
        el("div", { class: "hero-side-label", text: "Panel status" }),
        el(
          "div",
          { class: "hero-side-value" },
          el("span", { class: "status-dot", attrs: { "aria-hidden": "true" } }),
          document.createTextNode("Operational")
        ),
        el("div", {
          class: "hero-side-meta",
          text: `Up ${formatDuration(system.panel_uptime_seconds)} · ${system.hostname}`,
        }),
        el("div", {
          class: "hero-side-meta",
          text: state.version ? `Adreoz v${state.version}` : "Adreoz Server Manager",
        })
      )
    );

    const stats = el(
      "div",
      { class: "stats-grid" },
      statCard({
        iconName: "users",
        label: "Active sessions",
        value: account.active_sessions,
        suffix: "sessions",
        foot: "Includes this browser",
      }),
      statCard({
        iconName: "cpu",
        label: "CPU usage",
        value: Math.round(system.cpu_percent),
        suffix: "%",
        foot: `${system.cpu_count} logical core${system.cpu_count === 1 ? "" : "s"}`,
        percent: system.cpu_percent,
      }),
      statCard({
        iconName: "memory",
        label: "Memory usage",
        value: formatPercent(system.memory.percent, 0),
        suffix: "",
        foot: `${formatBytes(system.memory.used_bytes)} of ${formatBytes(system.memory.total_bytes)}`,
        percent: system.memory.percent,
      }),
      statCard({
        iconName: "hard-drive",
        label: "Data volume",
        value: formatPercent(system.disk.percent, 0),
        suffix: "",
        foot: `${formatBytes(system.disk.used_bytes)} of ${formatBytes(system.disk.total_bytes)}`,
        percent: system.disk.percent,
      })
    );

    const systemCard = el(
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
            icon("server", 16),
            document.createTextNode("This node")
          ),
          el("p", { class: "card-subtitle", text: "Live metrics sampled from the host" })
        ),
        el(
          "span",
          { class: "badge badge-active" },
          el("span", { class: "status-dot", attrs: { "aria-hidden": "true" } }),
          document.createTextNode("Live")
        )
      ),
      el(
        "div",
        { class: "card-body" },
        el(
          "div",
          { class: "metric-list" },
          progressRow(
            "CPU usage",
            system.cpu_percent,
            system.load_1 !== null
              ? `Load average ${system.load_1.toFixed(2)} / ${system.load_5.toFixed(2)} / ${system.load_15.toFixed(2)}`
              : "Load average unavailable on this platform"
          ),
          progressRow(
            "Memory",
            system.memory.percent,
            `${formatBytes(system.memory.used_bytes)} used · ${formatBytes(system.memory.available_bytes)} available`
          ),
          progressRow(
            "Data volume",
            system.disk.percent,
            `${formatBytes(system.disk.free_bytes)} free on the panel data directory`
          )
        ),
        el(
          "dl",
          { class: "system-facts" },
          el(
            "div",
            { class: "fact" },
            el("dt", { text: "Hostname" }),
            el("dd", { text: system.hostname })
          ),
          el(
            "div",
            { class: "fact" },
            el("dt", { text: "Platform" }),
            el("dd", { text: system.platform })
          ),
          el(
            "div",
            { class: "fact" },
            el("dt", { text: "Host uptime" }),
            el("dd", { text: formatDuration(system.host_uptime_seconds) })
          ),
          el(
            "div",
            { class: "fact" },
            el("dt", { text: "Panel uptime" }),
            el("dd", { text: formatDuration(system.panel_uptime_seconds) })
          )
        )
      ),
      el(
        "div",
        { class: "card-footer" },
        el("span", { class: "dim", text: "Metrics refresh on demand" }),
        el(
          "span",
          { class: "badge" },
          icon("clock", 12),
          document.createTextNode("Live sample")
        )
      )
    );

    const accountCard = el(
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
            icon("user", 16),
            document.createTextNode("Your account")
          ),
          el("p", { class: "card-subtitle", text: "Profile and session details" })
        )
      ),
      el(
        "div",
        { class: "card-body" },
        el(
          "div",
          { class: "identity" },
          avatarBlock(user.username, true),
          el(
            "div",
            {},
            el("div", { class: "identity-name", text: user.username }),
            el("div", {
              class: "identity-meta",
              text: user.email || "No email address on file",
            })
          )
        ),
        el(
          "dl",
          { class: "info-rows" },
          infoRow("Role", roleBadge(user.role)),
          infoRow("Member since", formatDateTime(account.created_at)),
          infoRow(
            "Last sign-in",
            account.last_login_at
              ? el("span", { text: relativeTime(account.last_login_at), attrs: { title: formatDateTime(account.last_login_at) } })
              : "—"
          ),
          infoRow(
            "Session expires",
            el("span", {
              text: relativeTime(account.session_expires_at),
              attrs: { title: formatDateTime(account.session_expires_at) },
            })
          )
        )
      ),
      el(
        "div",
        { class: "card-footer" },
        el("span", { class: "dim", text: "Change your password from the account page" }),
        el(
          "a",
          { class: "btn btn-ghost btn-sm", attrs: { href: "#/account" } },
          document.createTextNode("Manage"),
          icon("arrow-right", 13)
        )
      )
    );

    const sections = [
      pageHeader({
        eyebrow: "Command center",
        title: "Overview",
        description: "A live view of the control panel and the Linux host that runs it.",
        actions: [refreshButton],
      }),
      hero,
      stats,
      el("div", { class: "split-grid" }, systemCard, accountCard),
    ];

    if (platform) {
      const activityCard = el(
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
              icon("activity", 16),
              document.createTextNode("Recent activity")
            ),
            el("p", { class: "card-subtitle", text: "Security events from the audit trail" })
          ),
          el(
            "a",
            { class: "btn btn-ghost btn-sm", attrs: { href: "#/users" } },
            document.createTextNode("Users"),
            icon("arrow-right", 13)
          )
        ),
        platform.recent_activity.length > 0
          ? el(
              "div",
              { class: "activity-list" },
              ...platform.recent_activity.slice(0, 6).map(activityItem)
            )
          : emptyState({
              iconName: "activity",
              title: "No activity yet",
              text: "Security-relevant actions will appear here.",
            })
      );

      const platformCard = el(
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
              icon("shield-check", 16),
              document.createTextNode("Platform")
            ),
            el("p", { class: "card-subtitle", text: "Accounts and access at a glance" })
          )
        ),
        el(
          "div",
          { class: "card-body" },
          el(
            "div",
            { class: "platform-stats" },
            el(
              "div",
              { class: "platform-stat" },
              el("strong", { text: String(platform.total_users) }),
              el("span", { text: "Users" })
            ),
            el(
              "div",
              { class: "platform-stat" },
              el("strong", { text: String(platform.active_users) }),
              el("span", { text: "Active" })
            ),
            el(
              "div",
              { class: "platform-stat" },
              el("strong", { text: String(platform.admins) }),
              el("span", { text: "Admins" })
            )
          ),
          el("p", {
            class: "page-desc",
            text: "Authorization is enforced by the API on every request. Interface visibility is only a convenience.",
          })
        ),
        el(
          "div",
          { class: "card-footer" },
          el("span", { class: "dim", text: "Manage accounts and access" }),
          el(
            "a",
            { class: "btn btn-secondary btn-sm", attrs: { href: "#/users" } },
            icon("users", 14),
            document.createTextNode("Open users")
          )
        )
      );

      sections.push(el("div", { class: "split-grid" }, activityCard, platformCard));
    }

    sections.push(
      el(
        "article",
        { class: "card roadmap-card" },
        el(
          "div",
          { class: "card-header" },
          el(
            "div",
            {},
            el(
              "h3",
              { class: "card-title" },
              icon("sparkles", 16),
              document.createTextNode("Build roadmap")
            ),
            el("p", {
              class: "card-subtitle",
              text: "Phase 1 is complete and verified. The next milestone is server lifecycle.",
            })
          ),
          el("span", { class: "badge badge-phase", text: "Phase 1 of 8" })
        ),
        el(
          "div",
          { class: "card-body" },
          el(
            "div",
            { class: "phase-grid" },
            phaseCard({
              index: 1,
              title: "Foundation",
              description: "Authentication, roles, audit trail, panel shell.",
              state: "done",
            }),
            phaseCard({
              index: 2,
              title: "Server lifecycle",
              description: "Create, configure, start, and stop Minecraft servers.",
              state: "done",
            }),
            phaseCard({
              index: 3,
              title: "Console & files",
              description: "WebSocket console and traversal-safe file manager.",
              state: "next",
            }),
            phaseCard({
              index: 4,
              title: "Backups & metrics",
              description: "Schedules, safe restores, and per-server monitoring.",
              state: "planned",
            })
          )
        )
      )
    );

    view.replaceChildren(el("div", { class: "stack-lg" }, ...sections));
  }

  async function refresh(button) {
    setBusy(button, true);
    try {
      const summary = await api("/dashboard/summary");
      if (ctx.isCurrent()) render(summary);
    } catch (error) {
      toast(error.message, "error");
      setBusy(button, false);
    }
  }
}
