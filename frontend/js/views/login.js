/* Sign-in screen. */

import { api } from "../core/api.js";
import { el, setBusy } from "../core/dom.js";
import { authShell } from "../components/auth-layout.js";
import {
  formError,
  makeField,
  makePasswordField,
  showFormError,
  clearFormError,
} from "../components/forms.js";
import { icon } from "../components/icons.js";

export function renderLogin(root, { onSuccess, message = null } = {}) {
  const errorBox = formError();
  const username = makeField({
    id: "login-username",
    label: "Username",
    placeholder: "your username",
    autocomplete: "username",
    extraAttrs: { autocapitalize: "none", spellcheck: "false" },
  });
  const password = makePasswordField({
    id: "login-password",
    label: "Password",
    autocomplete: "current-password",
  });

  const submit = el(
    "button",
    { class: "btn btn-primary btn-block auth-submit", type: "submit" },
    icon("log-in", 16),
    el("span", { text: "Sign in" })
  );

  const form = el(
    "form",
    { class: "auth-form", novalidate: true },
    message
      ? el(
          "div",
          { class: "notice notice-info" },
          icon("info", 15),
          el("span", { text: message })
        )
      : null,
    errorBox,
    username.field,
    password.field,
    submit
  );

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearFormError(errorBox);
    const usernameValue = username.input.value.trim();
    const passwordValue = password.input.value;

    if (!usernameValue || !passwordValue) {
      showFormError(errorBox, "Enter both your username and password.");
      return;
    }

    setBusy(submit, true);
    try {
      const data = await api("/auth/login", {
        method: "POST",
        body: { username: usernameValue, password: passwordValue },
        handleUnauthorized: false,
      });
      onSuccess(data);
    } catch (error) {
      showFormError(errorBox, error.message);
      password.input.focus();
      password.input.select();
      setBusy(submit, false);
    }
  });

  const shell = authShell({
    mode: "login",
    eyebrow: "Secure sign in",
    title: "Welcome back",
    subtitle:
      "Sign in with the account an administrator created for you. The session is stored on the server, not in the browser.",
    form,
    meta: el("span", {
      text: "Accounts are created by an administrator. There is no public registration.",
    }),
  });

  root.replaceChildren(shell);
  username.input.focus();
}
