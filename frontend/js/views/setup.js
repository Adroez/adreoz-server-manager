/* First-run administrator creation. */

import { api } from "../core/api.js";
import { el, setBusy } from "../core/dom.js";
import { getState } from "../core/store.js";
import { authShell } from "../components/auth-layout.js";
import {
  clearFormError,
  formError,
  formRow,
  makeField,
  makePasswordField,
  passwordsMatch,
  showFormError,
} from "../components/forms.js";
import { icon } from "../components/icons.js";

export function renderSetup(root, { onSuccess } = {}) {
  const status = getState();
  const errorBox = formError();

  const username = makeField({
    id: "setup-username",
    label: "Username",
    placeholder: "owner",
    autocomplete: "username",
    hint: "3–32 characters. Letters, numbers, dots, underscores, and hyphens.",
    extraAttrs: { autocapitalize: "none", spellcheck: "false" },
  });
  const email = makeField({
    id: "setup-email",
    label: "Email",
    type: "email",
    placeholder: "you@example.com",
    autocomplete: "email",
    required: false,
    optional: true,
    hint: "Used only for account identification. No email is sent.",
  });
  const password = makePasswordField({
    id: "setup-password",
    label: "Password",
    autocomplete: "new-password",
    hint: "At least 10 characters, including a letter and a number.",
  });
  const confirm = makePasswordField({
    id: "setup-confirm",
    label: "Confirm password",
    autocomplete: "new-password",
  });

  const submit = el(
    "button",
    { class: "btn btn-primary btn-block auth-submit", type: "submit" },
    icon("shield-check", 16),
    el("span", { text: "Create administrator account" })
  );

  const form = el(
    "form",
    { class: "auth-form", novalidate: true },
    el(
      "div",
      { class: "notice notice-info" },
      icon("shield", 15),
      el("span", {
        text: "This first account becomes the administrator and can create all other users.",
      })
    ),
    errorBox,
    username.field,
    formRow(email.field),
    password.field,
    confirm.field,
    submit
  );

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearFormError(errorBox);

    const usernameValue = username.input.value.trim();
    const passwordValue = password.input.value;

    if (!usernameValue) {
      showFormError(errorBox, "Choose a username.");
      username.input.focus();
      return;
    }
    if (passwordValue.length < 10 || !/[A-Za-z]/.test(passwordValue) || !/\d/.test(passwordValue)) {
      showFormError(
        errorBox,
        "Password must be at least 10 characters and include a letter and a number."
      );
      password.input.focus();
      return;
    }
    if (!passwordsMatch(passwordValue, confirm.input.value)) {
      showFormError(errorBox, "The two passwords do not match.");
      confirm.input.focus();
      return;
    }

    setBusy(submit, true);
    try {
      const data = await api("/setup", {
        method: "POST",
        body: {
          username: usernameValue,
          email: email.input.value.trim() || null,
          password: passwordValue,
        },
        handleUnauthorized: false,
      });
      onSuccess(data);
    } catch (error) {
      showFormError(errorBox, error.message);
      if (error.status === 409) {
        window.setTimeout(() => window.location.reload(), 1400);
      }
      setBusy(submit, false);
    }
  });

  const shell = authShell({
    mode: "setup",
    eyebrow: "First-run setup",
    title: "Create the owner account",
    subtitle:
      "This wizard runs only once. There are no default credentials, and setup cannot be reopened after it completes.",
    form,
    meta: el("span", {
      text: `${status.appName || "Adreoz Server Manager"}${status.version ? ` · v${status.version}` : ""}`,
    }),
  });

  root.replaceChildren(shell);
  username.input.focus();
}
