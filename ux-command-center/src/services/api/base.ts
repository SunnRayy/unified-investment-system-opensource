export const API_BASE = '/api';

export async function safeReadError(res: Response, fallback: string): Promise<string> {
  try {
    const data = await res.json();
    return (data as { detail?: string }).detail || fallback;
  } catch {
    return fallback;
  }
}
