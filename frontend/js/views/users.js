/* Administrator user management. */

import { api } from "../core/api.js";
import { avatar, el, setBusy } from "../core/dom.js";
import {
  formatDate,
  formatDateTime,
  pluralize,
  relativeTime,
} from "../core/format.js";
import { getState } from "../core/store.js";
import {
  clearFormError,
  formError,
  generatePassword,
  makeField,
  makePasswordField,
  passwordsMatch,
  showFormError,
} from "../components/forms.js";
import { icon } from "../components/icons.js";
import { confirmDialog, openModal } from "../components/modal.js";
import { toast } from "../components/toast.js";
import {
  actionButton,
  emptyState,
  errorState,
  loadingState,
  pageHeader,
  roleBadge,
  statusBadge,
} from "../components/ui.js";

const PAGE_SIZE = 50;

export async function renderUsers(view, ctx) {
  view.replaceChildren(loadingState());

  let initial;
  try {
    initial = await api(`/users?limit=${PAGE_SIZE}&offset=0`);
  } catch (error) {
    if (!ctx.isCurrent()) return;
    view.replaceChildren(errorState(error.message, () => renderUsers(view, ctx)));
    return;
  }

  if (!ctx.isCurrent()) return;

  const state = {
    items: initial.items,
    total: initial.total,
    search: "",
  };

  const tbody = el("tbody");
  const countNode = el("span", { class: "toolbar-meta" });
  const footer = el("div", { class: "card-footer" });
  let searchTimer = null;

  const searchInput = el("input", {
    class: "input",
    id: "users-search",
    name: "search",
    type: "search",
    placeholder: "Search users…",
    attrs: { "aria-label": "Search users", autocomplete: "off" },
  });

  const createButton = el(
    "button",
    {
      class: "btn btn-primary",
      type: "button",
      on: { click: () => openCreateUser() },
    },
    icon("plus", 15),
    document.createTextNode("Create user")
  );

  const table = el(
    "table",
    { class: "table is-cards" },
    el(
      "thead",
      {},
      el(
        "tr",
        {},
        el("th", { text: "User" }),
        el("th", { text: "Role" }),
        el("th", { text: "Status" }),
        el("th", { text: "Created" }),
        el("th", { text: "Last sign-in" }),
        el("th", { class: "table-actions-head", text: "Actions" })
      )
    ),
    tbody
  );

  const card = el(
    "div",
    { class: "card" },
    el(
      "div",
      { class: "toolbar" },
      el(
        "div",
        { class: "search-field" },
        icon("search", 15),
        searchInput
      ),
      countNode
    ),
    el("div", { class: "table-wrap" }, table),
    footer
  );

  view.replaceChildren(
    el(
      "div",
      { class: "stack-md" },
      pageHeader({
        eyebrow: "Administration",
        title: "Users",
        description:
          "Create accounts, assign roles, and revoke access. Every action is checked by the API and written to the audit trail.",
        actions: [createButton],
      }),
      card
    )
  );

  searchInput.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      state.search = searchInput.value.trim();
      load({ append: false });
    }, 240);
  });

  renderRows();

  function renderRows() {
    tbody.replaceChildren();
    countNode.textContent = `${state.total} ${pluralize(state.total, "account")}`;

    if (state.items.length === 0) {
      tbody.append(
        el(
          "tr",
          {},
          el(
            "td",
            { class: "table-empty", attrs: { colspan: "6" } },
            emptyState({
              iconName: "search",
              title: state.search ? "No matching accounts" : "No accounts yet",
              text: state.search
                ? "Try a different username or email."
                : "Create the first additional account.",
            })
          )
        )
      );
    }

    const currentUser = getState().user;

    for (const item of state.items) {
      const isSelf = currentUser && item.id === currentUser.id;

      const userCell = el(
        "td",
        { attrs: { "data-label": "User" } },
        el(
          "div",
          { class: "cell-user" },
          avatar(item.username),
          el(
            "div",
            {},
            el(
              "div",
              { class: "cell-user-name" },
              document.createTextNode(item.username),
              isSelf ? el("span", { class: "user-self-tag", text: "You" }) : null
            ),
            el("div", {
              class: "cell-user-meta",
              text: item.email || "No email",
            })
          )
        )
      );

      const created = el("td", {
        attrs: { "data-label": "Created", title: formatDateTime(item.created_at) },
        text: formatDate(item.created_at),
      });

      const lastLogin = el(
        "td",
        { attrs: { "data-label": "Last sign-in" } },
        item.last_login_at
          ? el("span", {
              text: relativeTime(item.last_login_at),
              attrs: { title: formatDateTime(item.last_login_at) },
            })
          : el("span", { class: "dim", text: "Never" })
      );

      const actions = el("td", {
        class: "cell-actions",
        attrs: { "data-label": "Actions" },
      });
      if (isSelf) {
        actions.append(el("span", { class: "dim", text: "Current account" }));
      } else {
        actions.append(
          actionButton({
            label: "Password",
            iconName: "key",
            handler: (event) => openResetPassword(item, event.currentTarget),
          }),
          actionButton({
            label: item.role === "admin" ? "Make user" : "Make admin",
            iconName: "shield",
            handler: (event) =>
              changeRole(item, event.currentTarget),
          }),
          actionButton({
            label: item.is_active ? "Disable" : "Enable",
            iconName: "power",
            handler: (event) => changeActive(item, event.currentTarget),
          }),
          actionButton({
            label: "Delete",
            iconName: "trash",
            variant: "danger",
            handler: (event) => removeUser(item, event.currentTarget),
          })
        );
      }

      tbody.append(
        el(
          "tr",
          {},
          userCell,
          el("td", { attrs: { "data-label": "Role" } }, roleBadge(item.role)),
          el("td", { attrs: { "data-label": "Status" } }, statusBadge(item.is_active)),
          created,
          lastLogin,
          actions
        )
      );
    }

    footer.replaceChildren();
    if (state.items.length < state.total) {
      footer.append(
        el("span", {
          class: "dim",
          text: `Showing ${state.items.length} of ${state.total}`,
        }),
        el(
          "button",
          {
            class: "btn btn-secondary btn-sm",
            type: "button",
            on: { click: (event) => load({ append: true, button: event.currentTarget }) },
          },
          icon("refresh", 14),
          document.createTextNode("Load more")
        )
      );
    } else {
      footer.append(
        el("span", {
          class: "dim",
          text: `${state.items.length} of ${state.total} account${state.total === 1 ? "" : "s"} shown`,
        })
      );
    }
  }

  async function load({ append = false, button = null } = {}) {
    const offset = append ? state.items.length : 0;
    const params = new URLSearchParams({
      limit: String(PAGE_SIZE),
      offset: String(offset),
    });
    if (state.search) params.set("search", state.search);

    if (button) setBusy(button, true);
    try {
      const data = await api(`/users?${params.toString()}`);
      if (!ctx.isCurrent()) return;
      state.items = append ? [...state.items, ...data.items] : data.items;
      state.total = data.total;
      renderRows();
    } catch (error) {
      toast(error.message, "error");
      if (button) setBusy(button, false);
    }
  }

  async function runAction(button, action) {
    if (button) button.disabled = true;
    try {
      await action();
    } catch (error) {
      toast(error.message, "error");
      if (button && button.isConnected) button.disabled = false;
    }
  }

  async function changeRole(item, button) {
    const nextRole = item.role === "admin" ? "user" : "admin";
    const confirmed = await confirmDialog({
      title: nextRole === "admin" ? "Promote user" : "Demote administrator",
      message:
        nextRole === "admin"
          ? `Give ${item.username} full administrator access to this panel?`
          : `Remove administrator access from ${item.username}? Their sessions stay signed in, but admin endpoints will be denied immediately.`,
      confirmLabel: nextRole === "admin" ? "Promote" : "Demote",
      danger: nextRole !== "admin",
      iconName: "shield",
    });
    if (!confirmed) return;

    await runAction(button, async () => {
      await api(`/users/${item.id}`, {
        method: "PATCH",
        body: { role: nextRole },
      });
      toast(`${item.username} is now ${nextRole === "admin" ? "an administrator" : "a regular user"}.`);
      await load();
    });
  }

  async function changeActive(item, button) {
    if (item.is_active) {
      const confirmed = await confirmDialog({
        title: "Disable account",
        message: `Disable ${item.username} and revoke all of their sessions? They will not be able to sign in until re-enabled.`,
        confirmLabel: "Disable",
        danger: true,
        iconName: "power",
      });
      if (!confirmed) return;
    }

    await runAction(button, async () => {
      await api(`/users/${item.id}`, {
        method: "PATCH",
        body: { is_active: !item.is_active },
      });
      toast(
        `${item.username} is now ${item.is_active ? "disabled" : "active"}.`
      );
      await load();
    });
  }

  async function removeUser(item, button) {
    const confirmed = await confirmDialog({
      title: "Delete account",
      message: `Permanently delete ${item.username}? All sessions are revoked. Their audit history is preserved. This cannot be undone.`,
      confirmLabel: "Delete account",
      danger: true,
      iconName: "trash",
    });
    if (!confirmed) return;

    await runAction(button, async () => {
      const result = await api(`/users/${item.id}`, { method: "DELETE" });
      toast(result.message);
      await load();
    });
  }

  function fillGenerated(password, confirm) {
    const value = generatePassword(20);
    password.input.value = value;
    confirm.input.value = value;
    if (password.input.type === "password") password.toggle.click();
    if (confirm.input.type === "password") confirm.toggle.click();
    toast("A strong password was generated. Copy it before creating the account.", "info");
  }

  function openCreateUser() {
    const errorBox = formError();
    const username = makeField({
      id: "new-user-username",
      label: "Username",
      placeholder: "player-one",
      autocomplete: "off",
      hint: "3–32 characters. Letters, numbers, dots, underscores, and hyphens.",
      extraAttrs: { autocapitalize: "none", spellcheck: "false" },
    });
    const email = makeField({
      id: "new-user-email",
      label: "Email",
      type: "email",
      placeholder: "player@example.com",
      autocomplete: "off",
      required: false,
      optional: true,
    });
    const roleSelect = el(
      "select",
      { class: "select", id: "new-user-role" },
      el("option", { value: "user", selected: true, text: "Regular user" }),
      el("option", { value: "admin", text: "Administrator" })
    );
    const roleField = el(
      "div",
      { class: "field" },
      el("label", { attrs: { for: "new-user-role" }, text: "Role" }),
      roleSelect,
      el("p", {
        class: "field-hint",
        text: "Administrators can manage every user, server, and setting.",
      })
    );
    const password = makePasswordField({
      id: "new-user-password",
      label: "Password",
      hint: "At least 10 characters with a letter and a number.",
    });
    const confirm = makePasswordField({
      id: "new-user-confirm",
      label: "Confirm password",
    });
    const generateButton = el(
      "button",
      {
        class: "btn btn-ghost btn-xs",
        type: "button",
        on: { click: () => fillGenerated(password, confirm) },
      },
      icon("wand", 13),
      document.createTextNode("Generate strong password")
    );

    const formId = "create-user-form";
    const form = el(
      "form",
      { class: "form-grid", id: formId, novalidate: true },
      errorBox,
      username.field,
      email.field,
      roleField,
      password.field,
      confirm.field,
      el("div", { class: "row" }, generateButton)
    );

    const submit = el(
      "button",
      {
        class: "btn btn-primary",
        type: "submit",
        attrs: { form: formId },
      },
      icon("user-check", 15),
      document.createTextNode("Create user")
    );

    const footer = el(
      "div",
      { class: "modal-footer" },
      el(
        "button",
        {
          class: "btn btn-ghost",
          type: "button",
          on: { click: () => modal.close() },
        },
        document.createTextNode("Cancel")
      ),
      submit
    );

    const modal = openModal({
      title: "Create user",
      subtitle: "The account can sign in immediately with this password.",
      body: form,
      footer,
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      clearFormError(errorBox);

      const usernameValue = username.input.value.trim();
      const passwordValue = password.input.value;

      if (!usernameValue) {
        showFormError(errorBox, "Choose a username.");
        return;
      }
      if (
        passwordValue.length < 10 ||
        !/[A-Za-z]/.test(passwordValue) ||
        !/\d/.test(passwordValue)
      ) {
        showFormError(
          errorBox,
          "Password must be at least 10 characters and include a letter and a number."
        );
        return;
      }
      if (!passwordsMatch(passwordValue, confirm.input.value)) {
        showFormError(errorBox, "The two passwords do not match.");
        return;
      }

      setBusy(submit, true);
      try {
        const created = await api("/users", {
          method: "POST",
          body: {
            username: usernameValue,
            email: email.input.value.trim() || null,
            password: passwordValue,
            role: roleSelect.value,
          },
        });
        modal.close();
        toast(`Created ${created.username}.`);
        await load();
      } catch (error) {
        showFormError(errorBox, error.message);
        setBusy(submit, false);
      }
    });
  }

  function openResetPassword(target, trigger) {
    if (trigger) trigger.disabled = true;
    const errorBox = formError();
    const password = makePasswordField({
      id: "reset-password",
      label: "New password",
      hint: "At least 10 characters with a letter and a number.",
    });
    const confirm = makePasswordField({
      id: "reset-confirm",
      label: "Confirm new password",
    });
    const generateButton = el(
      "button",
      {
        class: "btn btn-ghost btn-xs",
        type: "button",
        on: { click: () => fillGenerated(password, confirm) },
      },
      icon("wand", 13),
      document.createTextNode("Generate strong password")
    );

    const formId = "reset-password-form";
    const form = el(
      "form",
      { class: "form-grid", id: formId, novalidate: true },
      errorBox,
      el(
        "div",
        { class: "form-note" },
        document.createTextNode(
          `Resetting the password for ${target.username} revokes every active session for that account.`
        )
      ),
      password.field,
      confirm.field,
      generateButton
    );

    const submit = el(
      "button",
      { class: "btn btn-primary", type: "submit", attrs: { form: formId } },
      icon("key", 15),
      document.createTextNode("Reset password")
    );

    const footer = el(
      "div",
      { class: "modal-footer" },
      el(
        "button",
        {
          class: "btn btn-ghost",
          type: "button",
          on: { click: () => modal.close() },
        },
        document.createTextNode("Cancel")
      ),
      submit
    );

    const modal = openModal({
      title: `Reset password`,
      subtitle: target.username,
      body: form,
      footer,
      onClose: () => {
        if (trigger && trigger.isConnected) trigger.disabled = false;
      },
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      clearFormError(errorBox);
      const passwordValue = password.input.value;
      if (
        passwordValue.length < 10 ||
        !/[A-Za-z]/.test(passwordValue) ||
        !/\d/.test(passwordValue)
      ) {
        showFormError(
          errorBox,
          "Password must be at least 10 characters and include a letter and a number."
        );
        return;
      }
      if (!passwordsMatch(passwordValue, confirm.input.value)) {
        showFormError(errorBox, "The two passwords do not match.");
        return;
      }

      setBusy(submit, true);
      try {
        const result = await api(`/users/${target.id}/password`, {
          method: "POST",
          body: { new_password: passwordValue },
        });
        modal.close();
        toast(result.message);
        await load();
      } catch (error) {
        showFormError(errorBox, error.message);
        setBusy(submit, false);
      }
    });
  }
}
