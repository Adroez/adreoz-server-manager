/* Form field builders and validation helpers. */

import { el } from "../core/dom.js";
import { icon } from "./icons.js";

export function makeField({
  id,
  label,
  type = "text",
  value = "",
  placeholder = "",
  autocomplete,
  required = true,
  optional = false,
  hint = null,
  minLength,
  maxLength,
  min,
  max,
  step,
  extraAttrs = {},
}) {
  const input = el("input", {
    class: "input",
    id,
    name: id,
    type,
    value,
    placeholder,
    required,
    autocomplete,
    minLength,
    maxLength,
    min,
    max,
    step,
    attrs: { ...extraAttrs },
  });

  const labelNode = el(
    "label",
    { attrs: { for: id } },
    document.createTextNode(label),
    optional ? el("span", { class: "optional", text: "Optional" }) : null
  );

  const field = el(
    "div",
    { class: "field" },
    labelNode,
    input,
    hint ? el("p", { class: "field-hint", text: hint }) : null
  );

  return { field, input };
}

export function makePasswordField({
  id,
  label,
  autocomplete = "new-password",
  hint = null,
  required = true,
}) {
  const input = el("input", {
    class: "input",
    id,
    name: id,
    type: "password",
    required,
    autocomplete,
    spellcheck: false,
    attrs: { autocapitalize: "off" },
  });

  const toggle = el(
    "button",
    {
      class: "password-toggle",
      type: "button",
      attrs: { "aria-label": "Show password", "aria-pressed": "false" },
      on: {
        click: () => {
          const showing = input.type === "text";
          input.type = showing ? "password" : "text";
          toggle.setAttribute("aria-pressed", showing ? "false" : "true");
          toggle.setAttribute("aria-label", showing ? "Show password" : "Hide password");
          toggle.replaceChildren(icon(showing ? "eye" : "eye-off", 16));
          input.focus();
        },
      },
    },
    icon("eye", 16)
  );

  const field = el(
    "div",
    { class: "field" },
    el("label", { attrs: { for: id }, text: label }),
    el("div", { class: "password-wrap" }, input, toggle),
    hint ? el("p", { class: "field-hint", text: hint }) : null
  );

  return { field, input, toggle };
}

export function formGrid(...children) {
  return el("div", { class: "form-grid" }, ...children);
}

export function formRow(...children) {
  return el("div", { class: "form-row" }, ...children);
}

export function showFormError(node, message) {
  if (!node) return;
  node.textContent = message;
  node.classList.toggle("is-visible", Boolean(message));
}

export function clearFormError(node) {
  showFormError(node, "");
}

export function formError() {
  return el("div", {
    class: "form-error",
    attrs: { role: "alert" },
  });
}

export function generatePassword(length = 20) {
  const letters = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
  const digits = "23456789";
  const symbols = "!@#$%*-_+=";
  const all = `${letters}${digits}${symbols}`;
  const buffer = new Uint32Array(1);
  const randomIndex = (size) => {
    // Rejection sampling avoids modulo bias in generated credentials.
    const limit = Math.floor(0x100000000 / size) * size;
    let value = 0;
    do {
      crypto.getRandomValues(buffer);
      value = buffer[0];
    } while (value >= limit);
    return value % size;
  };
  const chars = [letters[randomIndex(letters.length)], digits[randomIndex(digits.length)]];
  while (chars.length < length) {
    chars.push(all[randomIndex(all.length)]);
  }
  for (let i = chars.length - 1; i > 0; i -= 1) {
    const j = randomIndex(i + 1);
    [chars[i], chars[j]] = [chars[j], chars[i]];
  }
  return chars.join("");
}

export function passwordsMatch(first, second) {
  return first === second && first.length > 0;
}
