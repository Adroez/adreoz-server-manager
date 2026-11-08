/* Shared authentication screen chrome. */

import { el } from "../core/dom.js";
import { getState } from "../core/store.js";
import { brandLockup } from "./brand.js";
import { icon } from "./icons.js";

const FEATURES = {
  login: [
    ["shield-check", "Server-side sessions with CSRF protection"],
    ["users", "Role-based access enforced by the backend"],
    ["activity", "Security actions recorded in an audit trail"],
  ],
  setup: [
    ["user-check", "The first account becomes the administrator"],
    ["lock", "Passwords hashed with scrypt"],
    ["server", "Bound to localhost until you expose it"],
  ],
};

export function authShell({
  mode,
  eyebrow,
  title,
  subtitle,
  form,
  meta = null,
}) {
  const state = getState();
  const isSetup = mode === "setup";

  const hero = el(
    "div",
    { class: "auth-hero" },
    el("span", { class: "eyebrow", text: isSetup ? "First-run setup" : "Local-first hosting" }),
    el(
      "h1",
      {},
      isSetup ? "Claim the " : "Command your worlds from one ",
      el("span", {
        class: "gradient-text",
        text: isSetup ? "control room." : "quiet control room.",
      })
    ),
    el("p", {
      text: isSetup
        ? "Create the owner account that will administer this panel. Nothing is exposed publicly and no default credentials exist."
        : "Adreoz Server Manager keeps your Minecraft servers, users, and security telemetry in one carefully built local console.",
    }),
    el(
      "div",
      { class: "auth-features" },
      ...FEATURES[mode].map(([iconName, label]) =>
        el(
          "div",
          { class: "auth-feature" },
          el("span", { class: "auth-feature-icon" }, icon(iconName, 14)),
          el("span", { text: label })
        )
      )
    )
  );

  const brandPanel = el(
    "section",
    { class: "auth-brand", attrs: { "aria-hidden": "false" } },
    el("div", { class: "auth-brand-grid", attrs: { "aria-hidden": "true" } }),
    brandLockup(),
    hero,
    el(
      "div",
      { class: "auth-brand-footer" },
      el(
        "span",
        { class: "badge badge-active" },
        el("span", { class: "status-dot", attrs: { "aria-hidden": "true" } }),
        document.createTextNode("Local node")
      ),
      el("span", {
        text: `${state.version ? `v${state.version} · ` : ""}Phase 2 server lifecycle`,
      })
    )
  );

  const card = el(
    "div",
    { class: "auth-card" },
    brandLockup({ className: "auth-mobile-brand" }),
    el(
      "div",
      { class: "auth-card-header" },
      el("span", { class: "eyebrow", text: eyebrow }),
      el("h2", { text: title }),
      el("p", { text: subtitle })
    ),
    form,
    meta ? el("div", { class: "auth-meta" }, meta) : null
  );

  return el(
    "div",
    { class: "auth-shell" },
    brandPanel,
    el("section", { class: "auth-panel" }, card)
  );
}
