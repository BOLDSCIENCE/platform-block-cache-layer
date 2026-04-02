/**
 * HTTP client for the Cache Layer API.
 *
 * Unwraps the {data, meta} response envelope and validates with Zod.
 */

import type { z } from "zod";

let _baseUrl = "";
let _apiKey = "";

/** Configure the API client. Called once at app init. */
export function configure(baseUrl: string, apiKey: string) {
  _baseUrl = baseUrl.replace(/\/$/, "");
  _apiKey = apiKey;
}

async function request<T>(
  method: string,
  path: string,
  schema: z.ZodType<T>,
  options?: { body?: unknown; params?: Record<string, string> },
): Promise<T> {
  const url = new URL(`${_baseUrl}${path}`);
  if (options?.params) {
    for (const [k, v] of Object.entries(options.params)) {
      url.searchParams.set(k, v);
    }
  }

  const res = await fetch(url.toString(), {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": _apiKey,
    },
    body: options?.body ? JSON.stringify(options.body) : undefined,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err?.error?.message ?? `HTTP ${res.status}`);
  }

  const json = await res.json();
  const data = json.data ?? json;
  return schema.parse(data);
}

export const api = {
  get: <T>(path: string, schema: z.ZodType<T>, params?: Record<string, string>) =>
    request("GET", path, schema, { params }),

  post: <T>(path: string, schema: z.ZodType<T>, body: unknown) =>
    request("POST", path, schema, { body }),

  put: <T>(path: string, schema: z.ZodType<T>, body: unknown) =>
    request("PUT", path, schema, { body }),

  delete: <T>(path: string, schema: z.ZodType<T>, params?: Record<string, string>) =>
    request("DELETE", path, schema, { params }),
};
