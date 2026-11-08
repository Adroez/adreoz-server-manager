/* Administrator application settings. */

import { api } from "../core/api.js";
import { el, setBusy } from "../core/dom.js";
import {
  clearFormError,
  formError,
  showFormError,
} from "../components/forms.js";
import { icon } from "../components/icons.js";
import { toast } from "../components/toast.js";
import {
  errorState,
  infoRow,
  loadingState,
  pageHeader,
} from "../components/ui.js";

export async function renderSettings(view, ctx) {
  view.replaceChildren(loadingState());

  let data;
  try {
    data = await api("/settings");
  } catch (error) {
    if (!ctx.isCurrent()) return;
    view.replaceChildren(
      errorState(error.message, () => renderSettings(view, ctx))
    );
    return;
  }

  if (!ctx.isCurrent()) return;
  render(data);

  function render(payload) {
    const errorBox = formError();
    const hoursInput = el("input", {
      class: "input",
      id: "session-lifetime",
      type: "number",
      min: 1,
      max: 720,
      step: 1,
      value: String(payload.session_lifetime_hours),
      required: true,
      attrs: { inputmode: "numeric" },
    });
    const saveButton = el(
      "button",
      { class: "btn btn-primary btn-sm", type: "submit" },
      icon("check", 14),
      document.createTextNode("Save changes")
    );

    const form = el(
      "form",
      { class: "form-grid", novalidate: true },
      errorBox,
      el(
        "div",
        { class: "setting-row" },
        el(
          "label",
          { class: "setting-copy", attrs: { for: "session-lifetime" } },
          el("h4", { text: "Session lifetime" }),
          el("p", {
            text: "How long a new sign-in stays valid before the user must authenticate again.",
          })
        ),
        el(
          "div",
          { class: "inline-field" },
          hoursInput,
          el("span", { class: "dim", text: "hours" }),
          saveButton
        )
      ),
      el(
        "div",
        { class: "notice notice-info" },
        icon("info", 15),
        el("span", {
          text: "Changes apply to newly issued sessions. Existing sessions keep their original expiry.",
        })
      )
    );

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      clearFormError(errorBox);
      const value = Number(hoursInput.value);
      if (!Number.isInteger(value) || value < 1 || value > 720) {
        showFormError(errorBox, "Enter a whole number between 1 and 720 hours.");
        return;
      }

      setBusy(saveButton, true);
      try {
        const updated = await api("/settings", {
          method: "PATCH",
          body: { session_lifetime_hours: value },
        });
        toast(
          `Session lifetime saved. New sessions last ${updated.session_lifetime_hours} hours.`
        );
        render(updated);
      } catch (error) {
        showFormError(errorBox, error.message);
        setBusy(saveButton, false);
      }
    });

    const policyCard = el(
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
            icon("key", 16),
            document.createTextNode("Session policy")
          ),
          el("p", {
            class: "card-subtitle",
            text: "Server-side session lifetime for this node",
          })
        )
      ),
      el("div", { class: "card-body" }, form)
    );

    const appCard = el(
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
            document.createTextNode("Application")
          ),
          el("p", {
            class: "card-subtitle",
            text: "Read-only runtime information",
          })
        )
      ),
      el(
        "div",
        { class: "card-body" },
        el(
          "dl",
          { class: "info-rows" },
          infoRow("Version", `v${payload.version}`),
          infoRow("Environment", payload.environment),
          infoRow("Database", payload.database_backend.toUpperCase()),
          infoRow(
            "Setup",
            el(
              "span",
              { class: `badge ${payload.setup_complete ? "badge-active" : "badge-inactive"}` },
              payload.setup_complete ? "Complete" : "Pending"
            )
          ),
          infoRow(
            "Cookie transport",
            el(
              "span",
              {
                class: `badge ${payload.cookie_secure ? "badge-active" : "badge-inactive"}`,
                text: payload.cookie_secure ? "Secure cookies" : "HTTP localhost",
              }
            )
          ),
          infoRow("Session lifetime", `${payload.session_lifetime_hours} hours`)
        )
      )
    );

    const securityItems = [
      [
        "scrypt password hashing",
        "Memory-hard hashes with per-password salts and versioned parameters.",
      ],
      [
        "Opaque server-side sessions",
        "Only a SHA-256 digest of each session token is stored in the database.",
      ],
      [
        "CSRF protection",
        "Every mutating request requires a session-bound token in a custom header.",
      ],
      [
        "Backend role enforcement",
        "Administrator routes are checked in the API, not just hidden in the UI.",
      ],
      [
        "Login rate limiting",
        "Sliding-window throttling slows credential guessing from a single address.",
      ],
      [
        "Strict response headers",
        "A self-only CSP, frame denial, MIME sniffing protection, and no-referrer policy.",
      ],
      [
        "Audit trail",
        "Sign-ins, account changes, password resets, and settings edits are recorded.",
      ],
    ];

    const securityCard = el(
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
            document.createTextNode("Security posture")
          ),
          el("p", {
            class: "card-subtitle",
            text: "Protections active in this build",
          })
        ),
        el("span", { class: "badge badge-admin", text: "Phase 1" })
      ),
      el(
        "div",
        { class: "card-body" },
        el(
          "div",
          { class: "security-list" },
          ...securityItems.map(([title, description]) =>
            el(
              "div",
              { class: "security-item" },
              el("span", { class: "security-check" }, icon("check", 12)),
              el(
                "div",
                {},
                el("strong", { text: title }),
                el("span", { text: description })
              )
            )
          )
        )
      )
    );

    const sections = [
      pageHeader({
        eyebrow: "Administration",
        title: "Settings",
        description:
          "Node-wide policy and runtime information. Only administrators can change these values.",
      }),
      el("div", { class: "settings-grid" }, policyCard, appCard),
      securityCard,
    ];

    if (!payload.cookie_secure) {
      sections.splice(
        2,
        0,
        el(
          "div",
          { class: "notice" },
          icon("alert-triangle", 15),
          el("span", {
            text: "Secure cookies are off because this panel is configured for plain HTTP on localhost. Set ASM_COOKIE_SECURE=true when serving the panel through a TLS reverse proxy.",
          })
        )
      );
    }

    view.replaceChildren(el("div", { class: "stack-md" }, ...sections));
  }
}
