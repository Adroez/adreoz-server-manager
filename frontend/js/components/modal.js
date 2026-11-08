/* Accessible modal dialogs built on <dialog>. */

import { el, focusFirst } from "../core/dom.js";
import { icon } from "./icons.js";

export function openModal({ title, subtitle = null, body, footer = null, onClose = null }) {
  const dialog = el("dialog", { class: "modal" });

  const close = () => {
    if (dialog.open) dialog.close();
  };

  const header = el(
    "div",
    { class: "modal-header" },
    el(
      "div",
      {},
      el("h3", { class: "modal-title", text: title }),
      subtitle ? el("p", { class: "modal-subtitle", text: subtitle }) : null
    ),
    el(
      "button",
      {
        class: "modal-close",
        type: "button",
        attrs: { "aria-label": "Close dialog" },
        on: { click: close },
      },
      icon("x", 16)
    )
  );

  dialog.append(header, el("div", { class: "modal-body" }, body));
  if (footer) dialog.append(footer);

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) close();
  });
  dialog.addEventListener("close", () => {
    dialog.remove();
    if (onClose) onClose();
  });

  document.body.append(dialog);
  dialog.showModal();
  window.requestAnimationFrame(() => focusFirst(dialog));
  return { dialog, close };
}

export function confirmDialog({
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  danger = false,
  iconName = "alert-triangle",
}) {
  return new Promise((resolve) => {
    let settled = false;

    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
      controller.close();
    };

    const footer = el(
      "div",
      { class: "modal-footer" },
      el(
        "button",
        {
          class: "btn btn-ghost",
          type: "button",
          on: { click: () => finish(false) },
        },
        document.createTextNode(cancelLabel)
      ),
      el(
        "button",
        {
          class: `btn ${danger ? "btn-danger" : "btn-primary"}`,
          type: "button",
          on: { click: () => finish(true) },
        },
        danger ? icon(iconName, 15) : null,
        document.createTextNode(confirmLabel)
      )
    );

    const body = el(
      "div",
      { class: "confirm-body" },
      el("div", { class: "empty-icon confirm-icon" }, icon(iconName, 20)),
      el("p", { class: "confirm-copy", text: message })
    );

    const controller = openModal({
      title,
      body,
      footer,
      onClose: () => {
        if (!settled) {
          settled = true;
          resolve(false);
        }
      },
    });
  });
}
