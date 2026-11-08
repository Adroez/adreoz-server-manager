/* Fallback route. */

import { el } from "../core/dom.js";
import { icon } from "../components/icons.js";

export function renderNotFound(view) {
  view.replaceChildren(
    el(
      "div",
      { class: "error-panel" },
      el("div", { class: "error-icon" }, icon("search", 22)),
      el("h3", { text: "Page not found" }),
      el("p", { text: "That section does not exist in this build of Adreoz Server Manager." }),
      el(
        "a",
        { class: "btn btn-secondary btn-sm", attrs: { href: "#/overview" } },
        icon("arrow-right", 14),
        document.createTextNode("Back to overview")
      )
    )
  );
}
