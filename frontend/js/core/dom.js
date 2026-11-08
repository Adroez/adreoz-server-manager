/* Minimal DOM helpers. User-controlled strings must always use text props. */

const SVG_NS = "http://www.w3.org/2000/svg";

export function el(tag, props = {}, ...children) {
  if (tag === "svg" || tag === "path") {
    return svgEl(tag, props, ...children);
  }

  const node = document.createElement(tag);
  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (value === null || value === undefined || value === false) continue;

      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (key === "attrs") {
        for (const [name, attrValue] of Object.entries(value)) {
          if (attrValue === null || attrValue === undefined || attrValue === false) {
            continue;
          }
          node.setAttribute(name, attrValue === true ? "" : String(attrValue));
        }
      } else if (key === "on") {
        for (const [event, handler] of Object.entries(value)) {
          if (typeof handler === "function") {
            node.addEventListener(event, handler);
          }
        }
      } else if (key === "html") {
        // Only for trusted, static markup (icons). Never user data.
        node.innerHTML = value;
      } else if (key === "style") {
        Object.assign(node.style, value);
      } else {
        try {
          node[key] = value;
        } catch {
          node.setAttribute(key, String(value));
        }
      }
    }
  }
  appendChildren(node, children);
  return node;
}

function svgEl(tag, props = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "on") {
      for (const [event, handler] of Object.entries(value)) {
        node.addEventListener(event, handler);
      }
    } else {
      node.setAttribute(key, String(value));
    }
  }
  appendChildren(node, children);
  return node;
}

function appendChildren(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    if (typeof child === "string" || typeof child === "number") {
      node.append(document.createTextNode(String(child)));
    } else if (child instanceof Node) {
      node.append(child);
    }
  }
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

export function mount(host, ...nodes) {
  host.replaceChildren(...nodes.filter(Boolean));
  return host;
}

export function avatar(name, large = false) {
  const node = el("span", {
    class: `avatar${large ? " avatar-lg" : ""}`,
    attrs: { "aria-hidden": "true" },
    text: initials(name),
  });
  return node;
}

function initials(name) {
  if (!name) return "?";
  const parts = String(name).trim().split(/[\s._-]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
}

export function setBusy(button, busy) {
  if (!button) return;
  button.classList.toggle("is-loading", busy);
  button.disabled = busy;
  button.setAttribute("aria-busy", busy ? "true" : "false");
}

export function focusFirst(container) {
  const target = container.querySelector(
    "input:not([type=hidden]):not([disabled]), select, textarea"
  );
  if (target) target.focus();
}
