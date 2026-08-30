import i18n from '../../i18n';
import { authFetch, createAuthSSE } from '../authFetch';
import { API_BASE } from './base';
import type {
    FullLLMSettings, FullLLMSettingsUpdate, ChannelTestResult,
    PromptsData, PromptUpdatePayload, PromptPreviewResult,
    SourceRegistryResponse, SourceRegistryUpdateRequest, SourceTestResult,
    SourceHealthResponse, UploadResult, SourceFilesResponse, UploadHistoryResponse,
    MarketDataStatusResponse, MarketDataRefreshResult,
    ImportAdapterRun, ImportAdapterValidationResponse, ImportAdapterApprovalRequest,
    ImportAdapterApprovalResponse, ImportAdapterStagedRow, SyncStatus,
    FetchResult, SourceEventsResponse, LLMUsageResponse,
} from './types';

// Investor profile types — local to this module to avoid shared types.ts conflicts
export interface InvestorPhilosophy {
  goal?: string;
  horizon?: string;
  risk_tolerance?: string;
  core_weakness?: string;
  portfolio_structure?: string;
}

export interface InvestorProfile {
  display_name: string;
  avatar_url: string | null;
  philosophy: InvestorPhilosophy;
}

export interface InvestorProfileUpdate {
  display_name?: string;
  avatar_url?: string;
  philosophy?: InvestorPhilosophy;
}

export const SettingsAPI = {
  getLLMSettings: (): Promise<FullLLMSettings> =>
    authFetch('/api/settings/llm').then(r => r.json()),

  getLLMUsage: (): Promise<LLMUsageResponse> =>
    authFetch('/api/settings/llm/usage').then(r => {
      if (!r.ok) throw new Error(i18n.t('errors:settings.llmUsage', { status: r.status }));
      return r.json();
    }),

  updateLLMSettings: (settings: FullLLMSettingsUpdate): Promise<FullLLMSettings> =>
    authFetch('/api/settings/llm', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    }).then(r => {
      if (!r.ok) throw new Error(i18n.t('errors:settings.saveFailed', { status: r.status }));
      return r.json();
    }),

  testChannel: (req: { provider: string; model: string; api_key: string }): Promise<ChannelTestResult> =>
    authFetch('/api/settings/llm/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    }).then(r => r.json()),

  getPrompts: async (): Promise<PromptsData> => {
    const res = await authFetch('/api/settings/prompts');
    if (!res.ok) throw new Error(i18n.t('errors:settings.loadPrompts', { status: res.status }));
    return res.json();
  },

  updatePrompts: async (payload: PromptUpdatePayload): Promise<PromptsData> => {
    const res = await authFetch('/api/settings/prompts', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error((err as any).detail || i18n.t('errors:settings.saveFailed', { status: res.status }));
    }
    return res.json();
  },

  previewPrompt: async (
    prompt_type: 'brief' | 'review' | 'review_questions',
    shared_persona?: string | null,
    instructions?: string | null,
  ): Promise<PromptPreviewResult> => {
    const res = await authFetch('/api/settings/prompts/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt_type, shared_persona, instructions }),
    });
    if (!res.ok) throw new Error(i18n.t('errors:settings.previewFailed', { status: res.status }));
    return res.json();
  },

  resetPrompts: async (keys: string[]): Promise<PromptsData> => {
    const res = await authFetch('/api/settings/prompts/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keys }),
    });
    if (!res.ok) throw new Error(i18n.t('errors:settings.resetFailed', { status: res.status }));
    return res.json();
  },

  getSources: async (): Promise<SourceRegistryResponse> => {
    const res = await authFetch('/api/settings/sources');
    if (!res.ok) throw new Error(i18n.t('errors:settings.loadSources', { statusText: res.statusText }));
    return res.json();
  },
  getSourceHealth: async (): Promise<SourceHealthResponse> => {
    const response = await authFetch('/api/settings/sources/health');
    if (!response.ok) throw new Error(i18n.t('errors:settings.sourceHealth'));
    return response.json();
  },
  getMarketDataStatus: async (): Promise<MarketDataStatusResponse> => {
    const response = await authFetch('/api/market-data/status');
    if (!response.ok) throw new Error(i18n.t('errors:settings.marketDataStatus'));
    return response.json();
  },
  refreshMarketData: async (): Promise<MarketDataRefreshResult> => {
    const response = await authFetch('/api/market-data/refresh', { method: 'POST' });
    if (!response.ok) throw new Error(i18n.t('errors:settings.refreshMarketData'));
    return response.json();
  },
  getRefreshSchedule: async (): Promise<{ enabled: boolean; interval_minutes: number }> => {
    const response = await authFetch('/api/market-data/refresh/schedule');
    if (!response.ok) throw new Error(i18n.t('errors:settings.refreshSchedule'));
    return response.json();
  },
  updateRefreshSchedule: async (enabled: boolean, interval_minutes: number): Promise<void> => {
    const response = await authFetch('/api/market-data/refresh/schedule', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled, interval_minutes }),
    });
    if (!response.ok) throw new Error(i18n.t('errors:settings.updateRefreshSchedule'));
  },
  updateSources: async (req: SourceRegistryUpdateRequest): Promise<SourceRegistryResponse> => {
    const res = await authFetch('/api/settings/sources', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error((err as any).detail || i18n.t('errors:settings.updateSources', { statusText: res.statusText }));
    }
    return res.json();
  },
  testSource: async (reader: string, data_dir?: string | null): Promise<SourceTestResult> => {
    const body = data_dir ? JSON.stringify({ data_dir }) : undefined;
    const res = await authFetch(`/api/settings/sources/test/${reader}`, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body,
    });
    if (!res.ok) throw new Error(i18n.t('errors:settings.testFailed', { statusText: res.statusText }));
    return res.json();
  },
  testAllSources: async (): Promise<SourceTestResult[]> => {
    const res = await authFetch('/api/settings/sources/test-all', { method: 'POST' });
    if (!res.ok) throw new Error(i18n.t('errors:settings.testAllFailed', { statusText: res.statusText }));
    return res.json();
  },
  uploadSourceFile: async (reader: string, file: File): Promise<UploadResult> => {
    const formData = new FormData();
    formData.append('file', file);
    const response = await authFetch(`/api/settings/sources/upload/${reader}`, {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(err.detail || i18n.t('errors:settings.uploadFailed'));
    }
    return response.json();
  },

  startSync: async (): Promise<{ status: string; message: string }> => {
    const res = await authFetch(`${API_BASE}/sync/start`, { method: 'POST' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error((err as any).detail || i18n.t('errors:settings.syncStartFailed', { status: res.status }));
    }
    return res.json();
  },

  startSyncReader: async (reader: string): Promise<{ status: string; message: string }> => {
    const res = await authFetch(`${API_BASE}/sync/start/${encodeURIComponent(reader)}`, { method: 'POST' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error((err as any).detail || i18n.t('errors:settings.syncStartFailed', { status: res.status }));
    }
    return res.json();
  },

  getSourceFiles: async (reader: string): Promise<SourceFilesResponse> => {
    const res = await authFetch(`${API_BASE}/settings/sources/files/${encodeURIComponent(reader)}`);
    if (!res.ok) throw new Error(i18n.t('errors:settings.sourceFiles', { status: res.status }));
    return res.json();
  },

  getUploadHistory: async (reader: string, limit = 20): Promise<UploadHistoryResponse> => {
    const res = await authFetch(`${API_BASE}/settings/sources/upload-history/${encodeURIComponent(reader)}?limit=${limit}`);
    if (!res.ok) throw new Error(i18n.t('errors:settings.uploadHistory', { status: res.status }));
    return res.json();
  },

  fetchSource: async (reader: string): Promise<FetchResult> => {
    const res = await authFetch(`${API_BASE}/settings/sources/fetch/${encodeURIComponent(reader)}`, {
      method: 'POST',
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error((err as any).detail || i18n.t('errors:settings.fetchFailed', { status: res.status }));
    }
    return res.json();
  },

  getSourceEvents: async (reader?: string): Promise<SourceEventsResponse> => {
    const url = reader
      ? `${API_BASE}/settings/sources/events/${encodeURIComponent(reader)}`
      : `${API_BASE}/settings/sources/events`;
    const res = await authFetch(url);
    if (!res.ok) throw new Error(i18n.t('errors:settings.sourceEvents', { status: res.status }));
    return res.json();
  },
  getImportAdapters: async (): Promise<{ adapters: Array<Record<string, unknown>> }> => {
    const res = await authFetch('/api/settings/import-adapters');
    if (!res.ok) throw new Error(i18n.t('errors:settings.importAdapters', { status: res.status }));
    return res.json();
  },
  uploadImportAdapterFile: async (adapterKey: string, importType: string, file: File, headerRow = 0): Promise<ImportAdapterRun> => {
    const fd = new FormData();
    fd.append('file', file);
    const params = new URLSearchParams({ import_type: importType, header_row: String(headerRow) });
    const res = await authFetch(`/api/settings/import-adapters/${encodeURIComponent(adapterKey)}/upload?${params}`, {
      method: 'POST',
      body: fd,
    });
    if (!res.ok) throw new Error(i18n.t('errors:settings.adapterUploadFailed', { status: res.status }));
    return res.json();
  },
  configureImportAdapter: async (adapterKey: string, payload: { run_id: number; column_mapping: Record<string, string>; fx_rate?: number | null }): Promise<{ ok: boolean }> => {
    const res = await authFetch(`/api/settings/import-adapters/${encodeURIComponent(adapterKey)}/configure`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(i18n.t('errors:settings.configureFailed', { status: res.status }));
    return res.json();
  },
  validateImportAdapter: async (adapterKey: string, runId: number): Promise<ImportAdapterValidationResponse> => {
    const res = await authFetch(`/api/settings/import-adapters/${encodeURIComponent(adapterKey)}/validate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_id: runId }),
    });
    if (!res.ok) throw new Error(i18n.t('errors:settings.validateFailed', { status: res.status }));
    return res.json();
  },
  stageImportAdapter: async (adapterKey: string, runId: number): Promise<{ staged_rows: number }> => {
    const res = await authFetch(`/api/settings/import-adapters/${encodeURIComponent(adapterKey)}/stage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_id: runId }),
    });
    if (!res.ok) throw new Error(i18n.t('errors:settings.stageFailed', { status: res.status }));
    return res.json();
  },
  getStagedRows: async (adapterKey: string, runId: number, limit = 50): Promise<{ rows: ImportAdapterStagedRow[]; count: number }> => {
    const res = await authFetch(`/api/settings/import-adapters/${encodeURIComponent(adapterKey)}/staged-rows?run_id=${runId}&limit=${limit}`);
    if (!res.ok) throw new Error(i18n.t('errors:settings.getStagedRowsFailed', { status: res.status }));
    return res.json();
  },
  approveImportAdapter: async (adapterKey: string, payload: ImportAdapterApprovalRequest): Promise<ImportAdapterApprovalResponse> => {
    const res = await authFetch(`/api/settings/import-adapters/${encodeURIComponent(adapterKey)}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(i18n.t('errors:settings.approveFailed', { status: res.status }));
    return res.json();
  },

  getProfile: async (): Promise<InvestorProfile> => {
    const res = await authFetch('/api/settings/profile');
    if (!res.ok) throw new Error(i18n.t('errors:settings.loadProfile', { status: res.status }));
    return res.json();
  },

  updateProfile: async (body: InvestorProfileUpdate): Promise<InvestorProfile> => {
    const res = await authFetch('/api/settings/profile', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error((err as any).detail || i18n.t('errors:settings.profileSaveFailed', { status: res.status }));
    }
    return res.json();
  },

  getSyncStatus: async (): Promise<SyncStatus> => {
    const res = await authFetch(`${API_BASE}/sync/status`);
    if (!res.ok) throw new Error(i18n.t('errors:settings.syncStatus', { status: res.status }));
    return res.json();
  },

  streamSyncLogs: (
    onLog: (msg: string) => void,
    onEnd: (success: boolean) => void
  ): (() => void) => {
    let successSeen = false;
    let pollInterval: ReturnType<typeof setInterval> | null = null;
    let completed = false;
    // esRef is set once the async ticket fetch resolves so the cleanup function
    // can close the EventSource even if it was created after cleanup() was called.
    let esRef: EventSource | null = null;

    const complete = (success: boolean) => {
      if (completed) return;
      completed = true;
      if (pollInterval !== null) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
      onEnd(success);
    };

    // createAuthSSE is async (fetches a ticket first), so we use .then() to
    // wire up the EventSource listeners while preserving the synchronous return
    // type of streamSyncLogs (the React useEffect cleanup pattern requires it).
    createAuthSSE(`${API_BASE}/sync/stream`).then(es => {
      esRef = es;

      es.addEventListener('log', (e: MessageEvent) => {
        const msg = e.data as string;
        if (msg.includes('SYNC COMPLETED. Success: True')) successSeen = true;
        onLog(msg);
      });

      es.addEventListener('end', () => {
        es.close();
        complete(successSeen);
      });

      es.onerror = () => {
        if (pollInterval !== null) return;
        es.close();
        // Switch to polling fallback
        pollInterval = setInterval(async () => {
          try {
            const status = await SettingsAPI.getSyncStatus();
            if (!status.running) {
              complete(successSeen);
            }
          } catch {
            complete(false);
          }
        }, 2000);
      };
    }).catch(() => {
      // Ticket fetch failed (e.g. auth error, network); fall through to onEnd(false).
      complete(false);
    });

    return () => {
      completed = true;
      if (esRef !== null) {
        esRef.close();
      }
      if (pollInterval !== null) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
    };
  },
};
