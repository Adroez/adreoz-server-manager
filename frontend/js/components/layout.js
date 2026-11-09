/* Authenticated application shell: sidebar, topbar, and content host. */

import { avatar, el } from "../core/dom.js";
import { brandLockup } from "./brand.js";
import { icon } from "./icons.js";

export function renderShell({ root, routes, user, onLogout }) {
  const visibleRoutes = routes.filter(
    (route) => !route.admin || user.is_admin
  );

  const navLinks = new Map();
  const nav = el("nav", { class: "nav", attrs: { "aria-label": "Primary navigation" } });

  const groups = [];
  for (const route of visibleRoutes) {
    let group = groups.find((item) => item.name === route.group);
    if (!group) {
      group = { name: route.group, routes: [] };
      groups.push(group);
    }
    group.routes.push(route);
  }

  for (const group of groups) {
    nav.append(el("div", { class: "nav-label", text: group.name }));
    for (const route of group.routes) {
      const link = el(
        "a",
        {
          class: "nav-link",
          attrs: { href: `#${route.path}`, "data-path": route.path },
        },
        icon(route.icon, 17),
        el("span", { text: route.title })
      );
      link.addEventListener("click", () => closeSidebar());
      navLinks.set(route.path, link);
      nav.append(link);
    }
  }

  const roadmap = el(
    "div",
    { class: "sidebar-roadmap" },
    el(
      "div",
      { class: "roadmap-title" },
      document.createTextNode("Phase 5"),
      el("span", { class: "badge badge-phase", text: "Live" })
    ),
    el("p", {
      class: "roadmap-copy",
      text: "Backups: archive, restore, and retain server saves automatically.",
    })
  );

  const logoutButton = el(
    "button",
    {
      class: "sidebar-logout",
      type: "button",
      attrs: { title: "Sign out", "aria-label": "Sign out" },
      on: { click: onLogout },
    },
    icon("logout", 16)
  );

  const userCard = el(
    "div",
    { class: "sidebar-user" },
    avatar(user.username),
    el(
      "div",
      { class: "sidebar-user-meta" },
      el("div", { class: "sidebar-user-name", text: user.username }),
      el("div", { class: "sidebar-user-role", text: user.role })
    ),
    logoutButton
  );

  const sidebar = el(
    "aside",
    { class: "sidebar", attrs: { "aria-label": "Sidebar" } },
    brandLockup({ className: "sidebar-brand", href: "#/overview" }),
    nav,
    el("div", { class: "sidebar-spacer" }),
    roadmap,
    userCard
  );

  const titleNode = el("h1", { class: "topbar-title" });
  const menuButton = el(
    "button",
    {
      class: "menu-btn",
      type: "button",
      attrs: { "aria-label": "Open navigation", "aria-expanded": "false" },
      on: { click: () => toggleSidebar() },
    },
    icon("menu", 18)
  );

  const topbar = el(
    "header",
    { class: "topbar" },
    el(
      "div",
      { class: "topbar-left" },
      menuButton,
      el(
        "div",
        {},
        el("div", { class: "topbar-eyebrow", text: "Control panel" }),
        titleNode
      )
    ),
    el(
      "div",
      { class: "topbar-actions" },
      el(
        "span",
        { class: "node-chip" },
        el("span", { class: "status-dot", attrs: { "aria-hidden": "true" } }),
        document.createTextNode("Local node")
      ),
      el(
        "div",
        { class: "topbar-user" },
        avatar(user.username),
        el(
          "div",
          { class: "topbar-user-copy" },
          el("div", { class: "topbar-user-name", text: user.username }),
          el("div", { class: "topbar-user-role", text: user.role })
        )
      )
    )
  );

  const backdrop = el("button", {
    class: "sidebar-backdrop",
    type: "button",
    attrs: { "aria-label": "Close navigation", tabindex: "-1" },
    on: { click: () => closeSidebar() },
  });

  const view = el("main", {
    class: "view",
    id: "main",
    attrs: { tabindex: "-1" },
  });

  const shell = el(
    "div",
    { class: "app-shell" },
    sidebar,
    backdrop,
    el("div", { class: "main-column" }, topbar, view)
  );

  root.replaceChildren(shell);

  function closeSidebar() {
    document.body.classList.remove("sidebar-open");
    menuButton.setAttribute("aria-expanded", "false");
  }

  function openSidebar() {
    document.body.classList.add("sidebar-open");
    menuButton.setAttribute("aria-expanded", "true");
  }

  function toggleSidebar() {
    if (document.body.classList.contains("sidebar-open")) {
      closeSidebar();
    } else {
      openSidebar();
    }
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSidebar();
  });

  function setRoute(route) {
    titleNode.textContent = route.title;
    document.title = `${route.title} · Adreoz Server Manager`;
    for (const [path, link] of navLinks.entries()) {
      const active = path === route.path;
      link.classList.toggle("is-active", active);
      if (active) {
        link.setAttribute("aria-current", "page");
      } else {
        link.removeAttribute("aria-current");
      }
    }
    closeSidebar();
    window.scrollTo(0, 0);
  }

  return { view, setRoute, closeSidebar };
}
