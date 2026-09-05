const AUTH_STORAGE_KEY = 'uis-auth-token';

export function getAuthToken(): string | null {
  return localStorage.getItem(AUTH_STORAGE_KEY);
}

let _isHandling401 = false;

export async function authFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const token = getAuthToken();
  const headers: HeadersInit = { ...(init?.headers ?? {}) };
  if (token) {
    (headers as Record<string, string>)['Authorization'] = `Bearer ${token}`;
  }
  const response = await window.fetch(input, { ...init, headers });
  if (response.status === 401 && !_isHandling401) {
    _isHandling401 = true;
    localStorage.removeItem(AUTH_STORAGE_KEY);
    window.location.href = '/';
  }
  return response;
}

export async function createAuthSSE(path: string): Promise<EventSource> {
  // POST to the ticket endpoint (with the normal Authorization header) to obtain
  // a short-lived, stream-scoped ticket.  The ticket — not the password — then
  // goes in the URL, so the password never appears in server logs.
  const ticketPath = `${path}-ticket`;
  const res = await authFetch(ticketPath, { method: 'POST' });
  if (!res.ok) {
    throw new Error(`Failed to obtain stream ticket: ${res.status}`);
  }
  const { ticket } = await res.json() as { ticket: string };
  return new EventSource(`${path}?ticket=${encodeURIComponent(ticket)}`);
}
