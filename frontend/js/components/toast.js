/* Non-blocking toast notifications. */

import { el } from "../core/dom.js";
import { icon } from "./icons.js";

const MAX_TOASTS = 4;
let stack = null;

function getStack() {
  if (!stack || !stack.isConnected) {
    stack = document.getElementById("toast-stack");
    if (!stack) {
      stack = el("div", {
        id: "toast-stack",
        class: "toast-stack",
        attrs: { "aria-live": "polite" },
      });
      document.body.append(stack);
    }
  }
  return stack;
}

export function toast(message, type = "success", duration = 4800) {
  const host = getStack();
  const iconName =
    type === "error" ? "alert-triangle" : type === "info" ? "info" : "check";

  const node = el(
    "div",
    { class: `toast toast-${type}`, attrs: { role: "status" } },
    el("div", { class: "toast-icon" }, icon(iconName, 14)),
    el("div", { class: "toast-message", text: message }),
    el(
      "button",
      {
        class: "toast-close",
        type: "button",
        attrs: { "aria-label": "Dismiss notification" },
        on: { click: () => removeToast(node) },
      },
      icon("x", 13)
    )
  );

  host.append(node);
  while (host.children.length > MAX_TOASTS) {
    removeToast(host.firstElementChild);
  }

  if (duration > 0) {
    node._timeout = window.setTimeout(() => removeToast(node), duration);
  }
  return node;
}

function removeToast(node) {
  if (!node || !node.isConnected) return;
  window.clearTimeout(node._timeout);
  node.classList.add("is-leaving");
  window.setTimeout(() => node.remove(), 190);
}
