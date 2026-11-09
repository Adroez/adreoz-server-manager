/* Create server page. */

import { api } from "../core/api.js";
import { el, setBusy } from "../core/dom.js";
import {
  clearFormError,
  formError,
  makeField,
  makePasswordField,
  showFormError,
} from "../components/forms.js";
import { icon } from "../components/icons.js";
import { toast } from "../components/toast.js";
import {
  errorState,
  loadingState,
  pageHeader,
} from "../components/ui.js";

const SOFTWARE_TYPES = [
  { value: "vanilla", label: "Vanilla", hint: "Official Minecraft server software" },
  { value: "paper", label: "Paper", hint: "High-performance Minecraft server" },
  { value: "fabric", label: "Fabric", hint: "Lightweight modded server loader" },
  { value: "forge", label: "Forge", hint: "Classic modded server loader" },
];

// Validates create-server form inputs. Returns an error message string, or
// null when all inputs are valid. Kept separate from the view so it can be
// unit-tested without a DOM.
export function validateCreateServerInputs({
  name,
  port,
  maxPlayers,
  viewDistance,
  motd,
}) {
  if (!name || !name.trim()) {
    return "Choose a server name.";
  }
  const portValue = Number(port);
  if (!Number.isInteger(portValue) || portValue < 1024 || portValue > 65535) {
    return "Port must be between 1024 and 65535.";
  }
  const maxPlayersValue = Number(maxPlayers);
  if (!Number.isInteger(maxPlayersValue) || maxPlayersValue < 1 || maxPlayersValue > 1000) {
    return "Max players must be between 1 and 1000.";
  }
  const viewDistanceValue = Number(viewDistance);
  if (!Number.isInteger(viewDistanceValue) || viewDistanceValue < 2 || viewDistanceValue > 32) {
    return "View distance must be between 2 and 32.";
  }
  const motdValue = String(motd).trim();
  if (!motdValue || motdValue.length > 128) {
    return "MOTD must be between 1 and 128 characters.";
  }
  return null;
}

// Builds the API request body for creating a server. Exported so it can be
// unit-tested without a DOM.
export function buildCreateServerBody({
  name,
  softwareType,
  version,
  port,
  maxPlayers,
  onlineMode,
  difficulty,
  gamemode,
  viewDistance,
  motd,
  javaRuntimeId,
}) {
  return {
    name: name.trim(),
    software_type: softwareType,
    version: version.trim() || "1.20.1",
    port: Number(port),
    max_players: Number(maxPlayers),
    online_mode: Boolean(onlineMode),
    difficulty,
    gamemode,
    view_distance: Number(viewDistance),
    motd: motd.trim(),
    java_runtime_id: javaRuntimeId === "" || javaRuntimeId == null ? null : Number(javaRuntimeId),
  };
}

export async function renderCreateServer(view, ctx) {
  view.replaceChildren(loadingState());

  if (!ctx.isCurrent()) return;

  const errorBox = formError();
  const name = makeField({
    id: "create-server-name",
    label: "Server name",
    placeholder: "my-survival",
    hint: "3–64 characters. Letters, numbers, dots, underscores, hyphens.",
    extraAttrs: { autocapitalize: "none", spellcheck: "false" },
  });
  const softwareSelect = el(
    "select",
    { class: "select", id: "create-server-software" },
    ...SOFTWARE_TYPES.map(
      (opt) =>
        el("option", { value: opt.value, text: `${opt.label} — ${opt.hint}` })
    )
  );
  const softwareField = el(
    "div",
    { class: "field" },
    el("label", { attrs: { for: "create-server-software" }, text: "Software" }),
    softwareSelect,
    el("p", { class: "field-hint", text: "Server software type and installer." })
  );
  const version = makeField({
    id: "create-server-version",
    label: "Minecraft version",
    value: "1.20.1",
    hint: "Version will be validated when the server is installed.",
  });
  const javaRuntimeSelect = el("select", {
    class: "select",
    id: "create-server-java-runtime",
  });
  javaRuntimeSelect.appendChild(
    el("option", { value: "", text: "System default" })
  );
  let javaRuntimes = [];
  try {
    const data = await api("/java/runtimes");
    javaRuntimes = data.items || [];
  } catch {
    // Runtimes are optional at creation; system default is always available.
  }
  for (const rt of javaRuntimes) {
    javaRuntimeSelect.appendChild(
      el("option", { value: String(rt.id), text: `${rt.name} ${rt.version}` })
    );
  }
  const javaRuntimeField = el(
    "div",
    { class: "field" },
    el(
      "label",
      { attrs: { for: "create-server-java-runtime" } },
      document.createTextNode("Java runtime")
    ),
    javaRuntimeSelect,
    el(
      "p",
      { class: "field-hint" },
      document.createTextNode(
        "Java runtime used to launch the server. System default if none selected."
      )
    )
  );
  const port = makeField({
    id: "create-server-port",
    label: "Port",
    type: "number",
    value: "25565",
    hint: "TCP port for players to connect (1024–65535).",
    extraAttrs: { inputmode: "numeric" },
  });
  const maxPlayers = makeField({
    id: "create-server-max-players",
    label: "Max players",
    type: "number",
    value: "10",
    hint: "Maximum concurrent players (1–1000).",
    extraAttrs: { inputmode: "numeric" },
  });
  const difficultySelect = el(
    "select",
    { class: "select", id: "create-server-difficulty" },
    el("option", { value: "peaceful", text: "Peaceful" }),
    el("option", { value: "easy", text: "Easy" }),
    el("option", { value: "normal", selected: true, text: "Normal" }),
    el("option", { value: "hard", text: "Hard" })
  );
  const difficultyField = el(
    "div",
    { class: "field" },
    el("label", { attrs: { for: "create-server-difficulty" }, text: "Difficulty" }),
    difficultySelect
  );
  const gamemodeSelect = el(
    "select",
    { class: "select", id: "create-server-gamemode" },
    el("option", { value: "survival", selected: true, text: "Survival" }),
    el("option", { value: "creative", text: "Creative" }),
    el("option", { value: "adventure", text: "Adventure" }),
    el("option", { value: "spectator", text: "Spectator" })
  );
  const gamemodeField = el(
    "div",
    { class: "field" },
    el("label", { attrs: { for: "create-server-gamemode" }, text: "Gamemode" }),
    gamemodeSelect
  );
  const viewDistance = makeField({
    id: "create-server-view-distance",
    label: "View distance",
    type: "number",
    value: "10",
    hint: "Chunks rendered around each player (2–32).",
    extraAttrs: { inputmode: "numeric" },
  });
  const onlineMode = el(
    "input",
    {
      class: "checkbox",
      id: "create-server-online-mode",
      type: "checkbox",
      checked: true,
    }
  );
  const onlineModeField = el(
    "div",
    { class: "field" },
    el(
      "label",
      { attrs: { for: "create-server-online-mode" } },
      onlineMode,
      document.createTextNode(" Online mode")
    ),
    el("p", {
      class: "field-hint",
      text: "Require players to authenticate with Mojang servers.",
    })
  );
  const motd = makeField({
    id: "create-server-motd",
    label: "MOTD",
    value: "A Minecraft server",
    hint: "Message of the day shown in the server list (1–128 characters).",
  });

  const submit = el(
    "button",
    { class: "btn btn-primary", type: "submit" },
    icon("server", 15),
    document.createTextNode("Create server")
  );

  const cancel = el(
    "button",
    {
      class: "btn btn-ghost",
      type: "button",
      on: { click: () => (window.location.hash = "#/servers") },
    },
    icon("arrow-right", 14),
    document.createTextNode("Cancel")
  );

  const form = el(
    "form",
    { class: "form-grid", novalidate: true },
    errorBox,
    name.field,
    softwareField,
    version.field,
    javaRuntimeField,
    port.field,
    maxPlayers.field,
    difficultyField,
    gamemodeField,
    viewDistance.field,
    onlineModeField,
    motd.field,
    el(
      "div",
      { class: "form-actions" },
      cancel,
      submit
    )
  );

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearFormError(errorBox);

    const error = validateCreateServerInputs({
      name: name.input.value,
      port: port.input.value,
      maxPlayers: maxPlayers.input.value,
      viewDistance: viewDistance.input.value,
      motd: motd.input.value,
    });
    if (error) {
      showFormError(errorBox, error);
      return;
    }

    setBusy(submit, true);
    try {
      const created = await api("/servers", {
        method: "POST",
        body: buildCreateServerBody({
          name: name.input.value,
          softwareType: softwareSelect.value,
          version: version.input.value,
          port: port.input.value,
          maxPlayers: maxPlayers.input.value,
          onlineMode: onlineMode.checked,
          difficulty: difficultySelect.value,
          gamemode: gamemodeSelect.value,
          viewDistance: viewDistance.input.value,
          motd: motd.input.value,
          javaRuntimeId: javaRuntimeSelect.value,
        }),
      });
      toast(`Created ${created.name}.`);
      window.location.hash = `#/servers/${created.id}`;
    } catch (error) {
      showFormError(errorBox, error.message);
      setBusy(submit, false);
    }
  });

  view.replaceChildren(
    el(
      "div",
      { class: "stack-md" },
      pageHeader({
        eyebrow: "Server lifecycle",
        title: "Create server",
        description:
          "Set up a new Minecraft server. Server software is not installed automatically; place server.jar in the server directory before starting.",
      }),
      el("div", { class: "card" }, el("div", { class: "card-body" }, form))
    )
  );
}
