/* JSON API client with CSRF and session-expiry handling. */

import { getState } from "./store.js";

export class ApiError extends Error {
  constructor(message, status = 0, details = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.details = details;
  }
}

function extractMessage(data, status) {
  if (data && typeof data.detail === "string") {
    return data.detail;
  }
  if (data && Array.isArray(data.detail) && data.detail.length > 0) {
    return data.detail.map((item) => item.msg || "Invalid value").join(" ");
  }
  if (status === 401) return "Authentication required.";
  if (status === 403) return "You do not have permission to do that.";
  if (status === 429) return "Too many attempts. Please wait and try again.";
  if (status >= 500) return "The server encountered an unexpected error.";
  return "The request could not be completed.";
}

export async function api(path, options = {}) {
  const {
    method = "GET",
    body,
    handleUnauthorized = true,
    headers: extraHeaders = {},
  } = options;

  const headers = { Accept: "application/json", ...extraHeaders };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  const csrfToken = getState().csrfToken;
  if (!["GET", "HEAD"].includes(method.toUpperCase()) && csrfToken) {
    headers["X-CSRF-Token"] = csrfToken;
  }

  let response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(
      "Cannot reach the panel. Check that the server is running.",
      0
    );
  }

  let data = null;
  if (response.status !== 204) {
    const text = await response.text();
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = null;
      }
    }
  }

  if (!response.ok) {
    if (response.status === 401 && handleUnauthorized) {
      window.dispatchEvent(new CustomEvent("asm:unauthorized"));
    }
    throw new ApiError(extractMessage(data, response.status), response.status, data);
  }

  return data;
}
