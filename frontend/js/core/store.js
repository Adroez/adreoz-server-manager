/* Tiny observable session store. */

const state = {
  user: null,
  csrfToken: null,
  expiresAt: null,
  setupRequired: false,
  appName: "Adreoz Server Manager",
  version: "",
};

const listeners = new Set();

function emit() {
  for (const listener of listeners) {
    try {
      listener(state);
    } catch (error) {
      console.error("Store listener failed", error);
    }
  }
}

export function getState() {
  return state;
}

export function setSession(payload) {
  state.user = payload.user;
  state.csrfToken = payload.csrf_token;
  state.expiresAt = payload.expires_at;
  state.setupRequired = false;
  emit();
}

export function clearSession() {
  state.user = null;
  state.csrfToken = null;
  state.expiresAt = null;
  emit();
}

export function setSetupStatus(payload) {
  state.setupRequired = Boolean(payload.setup_required);
  state.appName = payload.app_name || state.appName;
  state.version = payload.version || state.version;
  emit();
}

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
