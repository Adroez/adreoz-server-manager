/* Personal account management. */

import { api } from "../core/api.js";
import { el, setBusy } from "../core/dom.js";
import {
  formatDateTime,
  relativeTime,
} from "../core/format.js";
import { setSession } from "../core/store.js";
import {
  clearFormError,
  formError,
  makePasswordField,
  passwordsMatch,
  showFormError,
} from "../components/forms.js";
import { icon } from "../components/icons.js";
import { confirmDialog } from "../components/modal.js";
import { toast } from "../components/toast.js";
import {
  avatarBlock,
  errorState,
  infoRow,
  loadingState,
  pageHeader,
  roleBadge,
} from "../components/ui.js";

export async function renderAccount(view, ctx) {
  view.replaceChildren(loadingState());

  let summary;
  try {
    summary = await api("/dashboard/summary");
  } catch (error) {
    if (!ctx.isCurrent()) return;
    view.replaceChildren(errorState(error.message, () => renderAccount(view, ctx)));
    return;
  }

  if (!ctx.isCurrent()) return;
  render(summary);

  function render(data) {
    const { user, account } = data;
    const sessionCount = el("span", { text: String(account.active_sessions) });
    const sessionExpiry = el("span", {
      text: relativeTime(account.session_expires_at),
      attrs: { title: formatDateTime(account.session_expires_at) },
    });

    const revokeButton = el(
      "button",
      {
        class: "btn btn-secondary btn-sm",
        type: "button",
        disabled: account.active_sessions <= 1,
        on: { click: () => revokeOtherSessions(revokeButton) },
      },
      icon("logout", 14),
      document.createTextNode("Sign out other sessions")
    );

    const profileCard = el(
      "article",
      { class: "card" },
      el(
        "div",
        { class: "card-body" },
        el(
          "div",
          { class: "account-card-head" },
          avatarBlock(user.username, true),
          el(
            "div",
            {},
            el("div", { class: "account-name", text: user.username }),
            el("div", { class: "account-meta", text: user.email || "No email address" }),
            el("div", { class: "row" }, roleBadge(user.role))
          )
        ),
        el(
          "dl",
          { class: "info-rows account-facts" },
          infoRow("Username", user.username),
          infoRow("Email", user.email || "Not set"),
          infoRow(
            "Member since",
            el("span", { text: formatDateTime(account.created_at) })
          ),
          infoRow(
            "Last sign-in",
            account.last_login_at
              ? el("span", {
                  text: relativeTime(account.last_login_at),
                  attrs: { title: formatDateTime(account.last_login_at) },
                })
              : "—"
          ),
          infoRow("Active sessions", sessionCount),
          infoRow("Session expires", sessionExpiry)
        )
      ),
      el(
        "div",
        { class: "card-footer" },
        el("span", {
          class: "dim",
          text: "Revoking signs out every browser except this one.",
        }),
        revokeButton
      )
    );

    const errorBox = formError();
    const current = makePasswordField({
      id: "current-password",
      label: "Current password",
      autocomplete: "current-password",
    });
    const next = makePasswordField({
      id: "new-password",
      label: "New password",
      autocomplete: "new-password",
      hint: "At least 10 characters with a letter and a number.",
    });
    const confirm = makePasswordField({
      id: "confirm-password",
      label: "Confirm new password",
      autocomplete: "new-password",
    });
    const submit = el(
      "button",
      { class: "btn btn-primary", type: "submit" },
      icon("key", 15),
      document.createTextNode("Update password")
    );

    const form = el(
      "form",
      { class: "form-grid", novalidate: true },
      errorBox,
      current.field,
      next.field,
      confirm.field,
      el(
        "div",
        { class: "form-note" },
        document.createTextNode(
          "Changing your password revokes every other session. This browser is rotated to a fresh session automatically."
        )
      ),
      el("div", { class: "form-actions" }, submit)
    );

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      clearFormError(errorBox);
      const currentValue = current.input.value;
      const nextValue = next.input.value;

      if (!currentValue) {
        showFormError(errorBox, "Enter your current password.");
        return;
      }
      if (
        nextValue.length < 10 ||
        !/[A-Za-z]/.test(nextValue) ||
        !/\d/.test(nextValue)
      ) {
        showFormError(
          errorBox,
          "The new password must be at least 10 characters and include a letter and a number."
        );
        return;
      }
      if (!passwordsMatch(nextValue, confirm.input.value)) {
        showFormError(errorBox, "The two new passwords do not match.");
        return;
      }

      setBusy(submit, true);
      try {
        const result = await api("/auth/password", {
          method: "POST",
          body: { current_password: currentValue, new_password: nextValue },
        });
        setSession(result);
        current.input.value = "";
        next.input.value = "";
        confirm.input.value = "";
        sessionExpiry.textContent = relativeTime(result.expires_at);
        sessionExpiry.setAttribute("title", formatDateTime(result.expires_at));
        sessionCount.textContent = "1";
        revokeButton.disabled = true;
        toast("Password updated. Other sessions were revoked.");
        setBusy(submit, false);
      } catch (error) {
        showFormError(errorBox, error.message);
        setBusy(submit, false);
      }
    });

    const passwordCard = el(
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
            icon("lock", 16),
            document.createTextNode("Change password")
          ),
          el("p", {
            class: "card-subtitle",
            text: "Rotate credentials without leaving this browser",
          })
        )
      ),
      el("div", { class: "card-body" }, form)
    );

    async function revokeOtherSessions(button) {
      const confirmed = await confirmDialog({
        title: "Sign out other sessions",
        message:
          "Every other active session for your account will be revoked immediately. This browser stays signed in.",
        confirmLabel: "Sign out others",
        danger: true,
        iconName: "logout",
      });
      if (!confirmed) return;

      setBusy(button, true);
      try {
        const result = await api("/auth/sessions", { method: "DELETE" });
        toast(result.message);
        sessionCount.textContent = "1";
        revokeButton.disabled = true;
        setBusy(button, false);
      } catch (error) {
        toast(error.message, "error");
        setBusy(button, false);
      }
    }

    view.replaceChildren(
      el(
        "div",
        { class: "stack-md" },
        pageHeader({
          eyebrow: "Account",
          title: "Your profile",
          description:
            "Personal credentials and session controls. Account-wide administration lives under Users.",
        }),
        el("div", { class: "account-grid" }, profileCard, passwordCard)
      )
    );
  }
}
