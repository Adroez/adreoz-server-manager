/* Brand lockup used by the auth screens and application sidebar. */

import { el } from "../core/dom.js";

export function brandLockup({ className = "brand-lockup", href = null } = {}) {
  const tag = href ? "a" : "div";
  return el(
    tag,
    {
      class: className,
      attrs: href ? { href, "aria-label": "Adreoz Server Manager" } : {},
    },
    el("img", {
      src: "/assets/logo.svg",
      alt: "",
      width: 46,
      height: 46,
      attrs: { "aria-hidden": "true" },
    }),
    el(
      "div",
      { class: "brand-wordmark" },
      el("strong", { text: "Adreoz" }),
      el("span", { text: "Server Manager" })
    )
  );
}
