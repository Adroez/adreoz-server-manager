/* Application bootstrap and client-side router. */

import { api } from "./core/api.js";
import { el } from "./core/dom.js";
import { getState, clearSession, setSession, setSetupStatus } from "./core/store.js";
import { renderShell } from "./components/layout.js";
import { icon } from "./components/icons.js";
import { toast } from "./components/toast.js";
import { renderAccount } from "./views/account.js";
import { renderDashboard } from "./views/dashboard.js";
import { renderLogin } from "./views/login.js";
import { renderNotFound } from "./views/notfound.js";
import { renderCreateServer } from "./views/server-create.js";
import { renderServerConsole } from "./views/server-console.js";
import { renderServerDetail } from "./views/server-detail.js";
import { renderServers } from "./views/servers.js";
import { renderSettings } from "./views/settings.js";
import { renderSetup } from "./views/setup.js";
import { renderUsers } from "./views/users.js";

const root = document.getElementById("app");

const ROUTES = [
  {
    path: "/overview",
    title: "Overview",
    group: "Workspace",
    icon: "grid",
    render: renderDashboard,
  },
  {
    path: "/servers",
    title: "Servers",
    group: "Workspace",
    icon: "server",
    render: renderServers,
  },
  {
    path: "/servers/create",
    title: "Create server",
    group: "Workspace",
    icon: "plus",
    render: renderCreateServer,
  },
  {
    path: "/servers/detail",
    title: "Server details",
    group: "Workspace",
    icon: "server",
    render: renderServerDetail,
  },
  {
    path: "/servers/console",
    title: "Server console",
    group: "Workspace",
    icon: "terminal",
    render: renderServerConsole,
  },
  {
    path: "/users",
    title: "Users",
    group: "Administration",
    icon: "users",
    admin: true,
    render: renderUsers,
  },
  {
    path: "/settings",
    title: "Settings",
    group: "Administration",
    icon: "settings",
    admin: true,
    render: renderSettings,
  },
  {
    path: "/account",
    title: "Account",
    group: "Account",
    icon: "user",
    render: renderAccount,
  },
];

const NOT_FOUND = {
  path: "/not-found",
  title: "Not found",
  group: "",
  icon: "search",
  render: renderNotFound,
};

let shell = null;
let renderId = 0;
let logoutInFlight = false;
let loginInFlight = false;

function normalizePath(hash) {
  let path = (hash || "").replace(/^#/, "");
  if (!path) return "/overview";
  if (path.length > 1 && path.endsWith("/")) path = path.slice(0, -1);
  return path;
}

function resolveRoute() {
  const path = normalizePath(window.location.hash);
  // Exact match first.
  const exact = ROUTES.find((route) => route.path === path);
  if (exact) return exact;
  // Dynamic match: /servers/:id
  const serverMatch = path.match(/^\/servers\/(\d+)$/);
  if (serverMatch) {
    return { ...ROUTES.find((r) => r.path === "/servers/detail"), _serverId: serverMatch[1] };
  }
  // Dynamic match: /servers/:id/console
  const consoleMatch = path.match(/^\/servers\/(\d+)\/console$/);
  if (consoleMatch) {
    return { ...ROUTES.find((r) => r.path === "/servers/console"), _serverId: consoleMatch[1] };
  }
  return NOT_FOUND;
}

function resetShell({ clearHash = false } = {}) {
  shell = null;
  renderId += 1;
  document.body.classList.remove("sidebar-open");
  document.title = "Adreoz Server Manager";
  if (clearHash) {
    window.history.replaceState(null, "", `${window.location.pathname}`);
  }
}

function ensureShell() {
  if (shell) return shell;
  shell = renderShell({
    root,
    routes: ROUTES,
    user: getState().user,
    onLogout: handleLogout,
  });
  return shell;
}

function showSetup() {
  renderId += 1;
  resetShell({ clearHash: true });
  document.title = "Setup · Adreoz Server Manager";
  renderSetup(root, { onSuccess: handleAuthSuccess });
}

function showLogin({ message = null } = {}) {
  renderId += 1;
  resetShell();
  document.title = "Sign in · Adreoz Server Manager";
  renderLogin(root, { onSuccess: handleAuthSuccess, message });
}

function showFatal(error) {
  renderId += 1;
  root.replaceChildren(
    el(
      "div",
      { class: "auth-panel" },
      el(
        "div",
        { class: "auth-card stack-md" },
        el(
          "div",
          { class: "error-panel" },
          el("div", { class: "error-icon" }, icon("alert-triangle", 22)),
          el("h3", { text: "The panel could not start" }),
          el("p", { text: error.message || "An unknown error occurred." }),
          el(
            "button",
            {
              class: "btn btn-secondary btn-sm",
              type: "button",
              on: { click: () => window.location.reload() },
            },
            icon("refresh", 14),
            document.createTextNode("Reload")
          )
        )
      )
    )
  );
}

function handleAuthSuccess(data) {
  setSession(data);
  shell = null;
  if (!window.location.hash) {
    window.history.replaceState(null, "", "#/overview");
  }
  renderRoute();
}

async function handleLogout() {
  if (logoutInFlight) return;
  logoutInFlight = true;
  try {
    await api("/auth/logout", { method: "POST", handleUnauthorized: false });
  } catch (error) {
    // A failed request does not invalidate the server-side session. Keep the
    // current UI signed in rather than pretending the browser is safe.
    if (error.status !== 401) {
      toast(
        error.message || "Sign out failed. Your session is still active; try again.",
        "error"
      );
      return;
    }
  } finally {
    logoutInFlight = false;
  }

  clearSession();
  resetShell({ clearHash: true });
  showLogin({ message: "You have been signed out." });
}

async function renderRoute() {
  const state = getState();
  if (!state.user) {
    showLogin();
    return;
  }

  const route = resolveRoute();
  if (route.admin && !state.user.is_admin) {
    toast("Administrator access is required for that section.", "error");
    window.location.hash = "#/overview";
    return;
  }

  const currentShell = ensureShell();
  const currentRender = ++renderId;
  currentShell.setRoute(route);
  const context = {
    isCurrent: () => currentRender === renderId && Boolean(getState().user),
  };

  try {
    await route.render(currentShell.view, context);
  } catch (error) {
    console.error("View render failed", error);
    if (context.isCurrent()) {
      toast(error.message || "The page could not be loaded.", "error");
    }
  }
}

async function boot() {
  try {
    const status = await api("/setup/status", { handleUnauthorized: false });
    setSetupStatus(status);
    if (status.setup_required) {
      showSetup();
      return;
    }
  } catch (error) {
    showFatal(error);
    return;
  }

  let session;
  try {
    session = await api("/auth/me", { handleUnauthorized: false });
  } catch (error) {
    if (error.status === 401) {
      showLogin();
      return;
    }
    showFatal(error);
    return;
  }

  setSession(session);
  if (!window.location.hash) {
    window.history.replaceState(null, "", "#/overview");
  }
  await renderRoute();
}

window.addEventListener("hashchange", () => {
  if (!getState().user) return;
  renderRoute();
});

window.addEventListener("asm:unauthorized", () => {
  if (loginInFlight || !getState().user) return;
  loginInFlight = true;
  clearSession();
  resetShell();
  showLogin({ message: "Your session expired. Sign in again to continue." });
  loginInFlight = false;
});

window.addEventListener("unhandledrejection", (event) => {
  console.error("Unhandled promise rejection", event.reason);
});

boot();
