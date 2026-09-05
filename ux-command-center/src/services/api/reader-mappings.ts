import i18n from '../../i18n';
import { authFetch } from '../authFetch';
import { API_BASE } from './base';
import type {
    ReaderMappingListResponse, ReaderMappingCreateRequest, ReaderMappingPatchRequest,
    ReaderMapping, ReaderMappingArchiveResponse, ReaderMappingDeleteResponse,
    ReaderMappingPreviewRequest, AnyPreviewResponse,
    ReaderMappingIgnoreColumnRequest, ReaderMappingUnignoreResponse, UnmappedColumn,
} from './types';

/** Parse a Rule-12 error body: {"detail": "..."} (HTTPException 404/422/409)
 *  or {"error": {"message": "..."}} (api_error_response 500/503). */
async function readErrorDetail(res: Response, fallback: string): Promise<string> {
    const errorData = await res.json().catch(() => null);
    return errorData?.detail || errorData?.error?.message || fallback;
}

// docs/api-specs/reader-mappings.md (ADR-023/ADR-023 — WS-A/WS-B/WS-C).
// `kind` is optional for single-kind readers (financial_summary, gold,
// insurance, rsu) — defaults server-side to the reader's only kind. It is
// REQUIRED for multi-kind readers (schwab: known_etf/symbol_norm/action_map;
// cn_fund: type_map today) — omitting it 422s.
export const readerMappingsApi = {
    list: async (reader: string, kind?: string): Promise<ReaderMappingListResponse> => {
        const url = kind
            ? `${API_BASE}/settings/sources/${encodeURIComponent(reader)}/mappings?kind=${encodeURIComponent(kind)}`
            : `${API_BASE}/settings/sources/${encodeURIComponent(reader)}/mappings`;
        const res = await authFetch(url);
        if (!res.ok) throw new Error(await readErrorDetail(res, i18n.t('errors:readerMappings.list')));
        return res.json();
    },

    create: async (reader: string, body: ReaderMappingCreateRequest): Promise<ReaderMapping> => {
        const res = await authFetch(`${API_BASE}/settings/sources/${encodeURIComponent(reader)}/mappings`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(await readErrorDetail(res, i18n.t('errors:readerMappings.create')));
        return res.json();
    },

    patch: async (reader: string, id: number, body: ReaderMappingPatchRequest): Promise<ReaderMapping> => {
        const res = await authFetch(`${API_BASE}/settings/sources/${encodeURIComponent(reader)}/mappings/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(await readErrorDetail(res, i18n.t('errors:readerMappings.update')));
        return res.json();
    },

    archive: async (reader: string, id: number): Promise<ReaderMappingArchiveResponse> => {
        const res = await authFetch(`${API_BASE}/settings/sources/${encodeURIComponent(reader)}/mappings/${id}/archive`, {
            method: 'POST',
        });
        if (!res.ok) throw new Error(await readErrorDetail(res, i18n.t('errors:readerMappings.archive')));
        return res.json();
    },

    restore: async (reader: string, id: number): Promise<ReaderMapping> => {
        const res = await authFetch(`${API_BASE}/settings/sources/${encodeURIComponent(reader)}/mappings/${id}/restore`, {
            method: 'POST',
        });
        if (!res.ok) throw new Error(await readErrorDetail(res, i18n.t('errors:readerMappings.restore')));
        return res.json();
    },

    remove: async (reader: string, id: number): Promise<ReaderMappingDeleteResponse> => {
        const res = await authFetch(`${API_BASE}/settings/sources/${encodeURIComponent(reader)}/mappings/${id}`, {
            method: 'DELETE',
        });
        if (!res.ok) throw new Error(await readErrorDetail(res, i18n.t('errors:readerMappings.delete')));
        return res.json();
    },

    preview: async (
        reader: string, kind?: string, body?: ReaderMappingPreviewRequest,
    ): Promise<AnyPreviewResponse> => {
        const url = kind
            ? `${API_BASE}/settings/sources/${encodeURIComponent(reader)}/mappings/preview?kind=${encodeURIComponent(kind)}`
            : `${API_BASE}/settings/sources/${encodeURIComponent(reader)}/mappings/preview`;
        const res = await authFetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body ?? {}),
        });
        if (!res.ok) throw new Error(await readErrorDetail(res, i18n.t('errors:readerMappings.preview')));
        return res.json();
    },

    // ADR-023 A4.1 — "not melted by design" column-ignore mechanism.
    ignoreColumn: async (reader: string, body: ReaderMappingIgnoreColumnRequest): Promise<UnmappedColumn> => {
        const res = await authFetch(`${API_BASE}/settings/sources/${encodeURIComponent(reader)}/mappings/ignore-column`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(await readErrorDetail(res, i18n.t('errors:readerMappings.ignoreColumn')));
        return res.json();
    },

    unignore: async (reader: string, id: number): Promise<ReaderMappingUnignoreResponse> => {
        const res = await authFetch(`${API_BASE}/settings/sources/${encodeURIComponent(reader)}/mappings/${id}/unignore`, {
            method: 'POST',
        });
        if (!res.ok) throw new Error(await readErrorDetail(res, i18n.t('errors:readerMappings.unignoreColumn')));
        return res.json();
    },
};
