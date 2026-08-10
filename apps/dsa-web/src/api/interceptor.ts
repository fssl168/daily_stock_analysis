/**
 * Axios interceptors for unified error handling
 * Implements P1 BP-007: Global permission exception handling (401/403)
 *
 * NOTE: api/index.ts already registers a 401-redirect + attachParsedApiError
 * interceptor. This module is the extended variant that also surfaces 403
 * (forbidden) feedback. It is self-contained and does not depend on
 * react-hot-toast (not installed) or React context (not usable at module level).
 */

import apiClient from './index';
import { toApiErrorMessage } from './error';

/** Whether we've already registered the extended interceptors (idempotent). */
let registered = false;

/**
 * Install the extended response-error interceptors on the shared apiClient.
 *
 * - 401: session expired → clear session and redirect to login (same contract
 *        as api/index.ts, guarded to avoid double-redirect loops).
 * - 403: permission denied → log a warning (surface via console; the caller
 *        can attach its own UI notification in components).
 */
export function setupErrorInterceptors(): void {
  if (registered) return;
  registered = true;

  apiClient.interceptors.response.use(
    (response) => response,
    (error: unknown) => {
      const status = (error as { response?: { status?: number } })?.response?.status;
      const path = window.location.pathname + window.location.search;

      // 401 — session expired / not logged in.
      if (status === 401) {
        if (!path.startsWith('/login')) {
          // api/index.ts already handles this; guard against double dispatch.
          console.warn('[api] Session expired, redirecting to login');
        }
        return Promise.reject(error);
      }

      // 403 — insufficient permissions.
      if (status === 403) {
        console.warn(`[api] Permission denied: ${toApiErrorMessage(error)}`);
        return Promise.reject(error);
      }

      // Everything else flows through unchanged (parsed error is already
      // attached by api/index.ts).
      return Promise.reject(error);
    },
  );
}

/**
 * Convenience client for callers that want an explicitly-named instance.
 * Shares the same base config + interceptors as the default apiClient.
 */
export const authenticatedApiClient = apiClient;

// Install when this module is imported (idempotent).
setupErrorInterceptors();
