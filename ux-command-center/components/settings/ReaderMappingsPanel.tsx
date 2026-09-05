import React, { useCallback, useEffect, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import {
  ActionBtn, ChipBtn, IconBtn, StatusPill, OpsTable, ModalShell, FieldLabel, FormInput, FormSelect,
} from '../operations';
import type { ColDef } from '../operations';
import { readerMappingsApi, TaxonomyAPI } from '../../src/services/api';
import { ALLOWED_TRANSACTION_TYPES } from '../../src/services/api/types';
import type {
  ReaderMapping, ReaderMappingListResponse, AnyPreviewResponse,
  ReaderMappingPatchRequest, UnmappedColumn,
} from '../../src/services/api/types';

// ADR-023/ADR-023 (docs/plans/2026-07-18-reader-mapping-management.md,
// docs/api-specs/reader-mappings.md). Renders inside DataSourceManager's
// "Manage assets" expander. Generalized across every mapping_kind shipped so
// far:
//   fs_column      — financial_summary (WS-A)
//   id_field_map   — gold / insurance / rsu (WS-B)
//   known_etf / symbol_norm / action_map — schwab (WS-C)
//   type_map       — cn_fund (WS-C)
// A reader with more than one kind (schwab) renders a segmented kind switcher
// (mirrors CashFlowClassification's ChipBtn tab bar) above the table.

interface ReaderMappingsPanelProps {
  reader: string;
}

interface ActionState { loading: boolean; message: string; ok: boolean }

type ModalMode = 'add' | 'edit' | null;

type Tx = (key: string, opts?: Record<string, unknown>) => string;

// reader -> ordered list of (kind, tab label). Single-entry readers render no
// tab bar at all — the one kind is used directly.
function readerKinds(t: Tx): Record<string, { value: string; label: string }[]> {
  return {
    financial_summary: [{ value: 'fs_column', label: t('readerMappingsPanel.kindLabel.fsColumns') }],
    gold: [{ value: 'id_field_map', label: t('readerMappingsPanel.kindLabel.fieldMap') }],
    insurance: [{ value: 'id_field_map', label: t('readerMappingsPanel.kindLabel.fieldMap') }],
    rsu: [{ value: 'id_field_map', label: t('readerMappingsPanel.kindLabel.fieldMap') }],
    schwab: [
      { value: 'known_etf', label: t('readerMappingsPanel.kindLabel.knownEtfs') },
      { value: 'symbol_norm', label: t('readerMappingsPanel.kindLabel.symbolNormalization') },
      { value: 'action_map', label: t('readerMappingsPanel.kindLabel.actionMap') },
    ],
    cn_fund: [{ value: 'type_map', label: t('readerMappingsPanel.kindLabel.typeMap') }],
  };
}

// Fallback id_template field options per WS-B reader (docs/api-specs/reader-mappings.md
// Section C2's table) — used to seed the Field select when the current mapping
// list is empty (insurance's fresh-DB defaults_only=true case) or to widen the
// options beyond whatever fields happen to already have rows.
const FALLBACK_ID_TEMPLATE_FIELDS: Record<string, string[]> = {
  gold: ['asset_name', 'account'],
  insurance: ['product_name', 'policy_name'],
  rsu: ['asset_name'],
};

// 'transfer' (Attribution & Flows WS-3.1, V79) is a Schwab-only pseudo-type:
// resolved to transfer_out/transfer_in by quantity sign in the Schwab
// transactions hook. Only action_map may target it — no other reader hook
// resolves it, so a literal 'transfer' would persist on (e.g.) CN-fund rows.
// The backend enforces the same kind-scoped rule (422 in _validate_vocab_value);
// this filter keeps the type_map dropdown from offering an option the API
// would reject.
const ACTION_MAP_TYPE_OPTIONS = ALLOWED_TRANSACTION_TYPES.map(t => ({ value: t, label: t }));
const TYPE_MAP_TYPE_OPTIONS = ALLOWED_TRANSACTION_TYPES
  .filter(t => t !== 'transfer')
  .map(t => ({ value: t, label: t }));

interface MappingFormState {
  map_key: string;   // fs_column: Excel column. known_etf/symbol_norm/action_map/type_map: raw label.
  field: string;     // id_field_map only
  label: string;      // id_field_map only — the label half of "field:label"
  asset_id: string;
  asset_name: string;
  currency: string;
  code: string;       // id_field_map
  to: string;         // symbol_norm
  type: string;       // action_map / type_map
}
const EMPTY_FORM: MappingFormState = {
  map_key: '', field: '', label: '', asset_id: '', asset_name: '', currency: 'CNY',
  code: '', to: '', type: 'other',
};

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  return iso.replace('T', ' ').slice(0, 16);
}

/** Validation-gate pattern (pages/ValueTrapReviews.tsx getSaveExplain). */
function getSaveGate(kind: string, mode: ModalMode, form: MappingFormState, t: Tx): { text: string; blocked: boolean } {
  if (kind === 'fs_column') {
    if (mode === 'add' && !form.map_key.trim()) return { text: t('readerMappingsPanel.saveGate.excelColumnRequired'), blocked: true };
    if (!form.asset_id.trim()) return { text: t('readerMappingsPanel.saveGate.assetIdRequired'), blocked: true };
    if (!form.asset_name.trim()) return { text: t('readerMappingsPanel.saveGate.displayNameRequired'), blocked: true };
    if (form.currency !== 'CNY') return { text: t('readerMappingsPanel.saveGate.currencyMustBeCny'), blocked: true };
    return { text: '', blocked: false };
  }
  if (kind === 'id_field_map') {
    if (mode === 'add' && !form.field.trim()) return { text: t('readerMappingsPanel.saveGate.fieldRequired'), blocked: true };
    if (mode === 'add' && !form.label.trim()) return { text: t('readerMappingsPanel.saveGate.sourceLabelRequired'), blocked: true };
    if (!form.code.trim()) return { text: t('readerMappingsPanel.saveGate.codeRequired'), blocked: true };
    if (!form.code.trim().match(/^\S+$/) || !/^[\x00-\x7F]+$/.test(form.code.trim())) {
      return { text: t('readerMappingsPanel.saveGate.codeAsciiSafe'), blocked: true };
    }
    return { text: '', blocked: false };
  }
  if (kind === 'known_etf') {
    if (mode === 'add' && !form.map_key.trim()) return { text: t('readerMappingsPanel.saveGate.tickerRequired'), blocked: true };
    return { text: '', blocked: false };
  }
  if (kind === 'symbol_norm') {
    if (mode === 'add' && !form.map_key.trim()) return { text: t('readerMappingsPanel.saveGate.rawSymbolRequired'), blocked: true };
    if (!form.to.trim()) return { text: t('readerMappingsPanel.saveGate.normalizedToRequired'), blocked: true };
    return { text: '', blocked: false };
  }
  if (kind === 'action_map' || kind === 'type_map') {
    if (mode === 'add' && !form.map_key.trim()) return { text: t('readerMappingsPanel.saveGate.rawLabelRequired'), blocked: true };
    if (!form.type.trim()) return { text: t('readerMappingsPanel.saveGate.transactionTypeRequired'), blocked: true };
    return { text: '', blocked: false };
  }
  return { text: '', blocked: false };
}

function buildMapKey(kind: string, form: MappingFormState): string {
  if (kind === 'id_field_map') return `${form.field.trim()}:${form.label.trim()}`;
  return form.map_key.trim();
}

function buildCreateValue(kind: string, form: MappingFormState): Record<string, any> {
  switch (kind) {
    case 'fs_column':
      return { asset_id: form.asset_id.trim(), asset_name: form.asset_name.trim(), currency: form.currency };
    case 'id_field_map':
      return { code: form.code.trim() };
    case 'known_etf':
      return { etf: true };
    case 'symbol_norm':
      return { to: form.to.trim() };
    case 'action_map':
    case 'type_map':
      return { type: form.type.trim() };
    default:
      return {};
  }
}

function modalTitle(kind: string, t: Tx): string {
  switch (kind) {
    case 'id_field_map': return t('readerMappingsPanel.modalTitle.fieldMap');
    case 'known_etf': return t('readerMappingsPanel.modalTitle.etf');
    case 'symbol_norm': return t('readerMappingsPanel.modalTitle.symbolNormalization');
    case 'action_map': return t('readerMappingsPanel.modalTitle.actionMapping');
    case 'type_map': return t('readerMappingsPanel.modalTitle.typeMapping');
    default: return t('readerMappingsPanel.modalTitle.mapping');
  }
}

export const ReaderMappingsPanel: React.FC<ReaderMappingsPanelProps> = ({ reader }) => {
  const { t } = useTranslation('system');
  const kinds = readerKinds(t)[reader] ?? [{ value: 'fs_column', label: t('readerMappingsPanel.kindLabel.mappings') }];
  const [activeKind, setActiveKind] = useState<string>(kinds[0].value);

  const [data, setData] = useState<ReaderMappingListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Add / edit modal
  const [modalMode, setModalMode] = useState<ModalMode>(null);
  const [editTarget, setEditTarget] = useState<ReaderMapping | null>(null);
  const [form, setForm] = useState<MappingFormState>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Per-row action state (archive / restore / delete)
  const [rowStatus, setRowStatus] = useState<Record<number, ActionState>>({});

  // Per-column action state (ignore / unignore, fs_column only; "+ Map"
  // pre-fill status for id_field_map candidates) — keyed by column/map_key.
  const [columnStatus, setColumnStatus] = useState<Record<string, ActionState>>({});
  const [notMeltedOpen, setNotMeltedOpen] = useState(false);

  // Archive -> "also deactivate asset?" chaining (Section B of the api-spec) — fs_column only.
  const [deactivatePrompt, setDeactivatePrompt] = useState<{ mapping: ReaderMapping; assetId: string } | null>(null);
  const [deactivating, setDeactivating] = useState(false);
  const [deactivateError, setDeactivateError] = useState<string | null>(null);

  // Preview against file
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewData, setPreviewData] = useState<AnyPreviewResponse | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await readerMappingsApi.list(reader, activeKind);
      setData(res);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : t('readerMappingsPanel.error.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [reader, activeKind]);

  useEffect(() => { load(); }, [load]);

  const kind = data?.mapping_kind ?? activeKind;

  const openAdd = (prefill?: { map_key?: string; field?: string; label?: string }) => {
    setModalMode('add');
    setEditTarget(null);
    // id_field_map: default the Field select to the reader's first documented
    // id_template field so the rendered select value and form state agree
    // (otherwise the save gate would block on an "empty" field the UI appears
    // to have already chosen).
    const defaultField = kind === 'id_field_map'
      ? (FALLBACK_ID_TEMPLATE_FIELDS[reader]?.[0] ?? '')
      : '';
    setForm({
      ...EMPTY_FORM,
      map_key: prefill?.map_key ?? '',
      field: prefill?.field ?? defaultField,
      label: prefill?.label ?? '',
    });
    setFormError(null);
  };

  const openEdit = (m: ReaderMapping) => {
    setModalMode('edit');
    setEditTarget(m);
    if (kind === 'fs_column') {
      setForm({
        ...EMPTY_FORM,
        map_key: m.map_key,
        asset_id: m.map_value.asset_id,
        asset_name: m.map_value.asset_name,
        currency: m.map_value.currency,
      });
    } else if (kind === 'id_field_map') {
      const [f, ...rest] = m.map_key.split(':');
      setForm({ ...EMPTY_FORM, field: f, label: rest.join(':'), code: m.map_value.code });
    } else if (kind === 'symbol_norm') {
      setForm({ ...EMPTY_FORM, map_key: m.map_key, to: m.map_value.to });
    } else if (kind === 'action_map' || kind === 'type_map') {
      setForm({ ...EMPTY_FORM, map_key: m.map_key, type: m.map_value.type });
    } else {
      setForm({ ...EMPTY_FORM, map_key: m.map_key });
    }
    setFormError(null);
  };

  const closeModal = () => {
    setModalMode(null);
    setEditTarget(null);
    setFormError(null);
  };

  const handleSave = async () => {
    const gate = getSaveGate(kind, modalMode, form, t);
    if (gate.blocked) { setFormError(gate.text); return; }
    setSaving(true);
    setFormError(null);
    try {
      if (modalMode === 'add') {
        await readerMappingsApi.create(reader, {
          kind,
          map_key: buildMapKey(kind, form),
          value: buildCreateValue(kind, form),
        });
      } else if (modalMode === 'edit' && editTarget) {
        let value: Record<string, any> = {};
        if (kind === 'fs_column') {
          if (form.asset_name.trim() !== editTarget.map_value.asset_name) value.asset_name = form.asset_name.trim();
          if (form.asset_id.trim() !== editTarget.map_value.asset_id) value.asset_id = form.asset_id.trim();
        } else if (kind === 'id_field_map') {
          if (form.code.trim() !== editTarget.map_value.code) value.code = form.code.trim();
        } else if (kind === 'symbol_norm') {
          if (form.to.trim() !== editTarget.map_value.to) value.to = form.to.trim();
        } else if (kind === 'action_map' || kind === 'type_map') {
          if (form.type.trim() !== editTarget.map_value.type) value.type = form.type.trim();
        }
        const body: ReaderMappingPatchRequest = Object.keys(value).length > 0 ? { value } : {};
        await readerMappingsApi.patch(reader, editTarget.id, body);
      }
      closeModal();
      await load();
    } catch (e) {
      setFormError(e instanceof Error ? e.message : t('readerMappingsPanel.error.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const archiveConfirmText = (m: ReaderMapping): string => {
    if (kind === 'fs_column') {
      return t('readerMappingsPanel.confirm.archiveFsColumn', { mapKey: m.map_key, assetId: m.map_value.asset_id });
    }
    return t('readerMappingsPanel.confirm.archiveGeneric', { mapKey: m.map_key });
  };

  const handleArchive = async (m: ReaderMapping) => {
    if (!window.confirm(archiveConfirmText(m))) return;
    setRowStatus(s => ({ ...s, [m.id]: { loading: true, message: '', ok: false } }));
    try {
      const res = await readerMappingsApi.archive(reader, m.id);
      setRowStatus(s => ({ ...s, [m.id]: { loading: false, message: '', ok: true } }));
      if (res.asset_has_holdings && res.deactivate_hint) {
        setDeactivateError(null);
        setDeactivatePrompt({ mapping: res.mapping, assetId: res.deactivate_hint.asset_id });
      }
      await load();
    } catch (e) {
      setRowStatus(s => ({
        ...s,
        [m.id]: { loading: false, message: e instanceof Error ? e.message : t('readerMappingsPanel.error.archiveFailed'), ok: false },
      }));
    }
  };

  const handleRestore = async (m: ReaderMapping) => {
    if (!window.confirm(t('readerMappingsPanel.confirm.restore', { mapKey: m.map_key }))) return;
    setRowStatus(s => ({ ...s, [m.id]: { loading: true, message: '', ok: false } }));
    try {
      await readerMappingsApi.restore(reader, m.id);
      setRowStatus(s => ({ ...s, [m.id]: { loading: false, message: '', ok: true } }));
      await load();
    } catch (e) {
      setRowStatus(s => ({
        ...s,
        [m.id]: { loading: false, message: e instanceof Error ? e.message : t('readerMappingsPanel.error.restoreFailed'), ok: false },
      }));
    }
  };

  const handleDelete = async (m: ReaderMapping) => {
    if (!window.confirm(t('readerMappingsPanel.confirm.delete', { mapKey: m.map_key }))) return;
    setRowStatus(s => ({ ...s, [m.id]: { loading: true, message: '', ok: false } }));
    try {
      await readerMappingsApi.remove(reader, m.id);
      await load();
    } catch (e) {
      setRowStatus(s => ({
        ...s,
        [m.id]: { loading: false, message: e instanceof Error ? e.message : t('readerMappingsPanel.error.deleteFailed'), ok: false },
      }));
    }
  };

  const closeDeactivatePrompt = () => {
    setDeactivatePrompt(null);
    setDeactivateError(null);
  };

  const handleDeactivateConfirm = async () => {
    if (!deactivatePrompt) return;
    setDeactivating(true);
    setDeactivateError(null);
    try {
      await TaxonomyAPI.deactivateAsset(deactivatePrompt.assetId);
      setDeactivatePrompt(null);
    } catch (e) {
      setDeactivateError(e instanceof Error ? e.message : t('readerMappingsPanel.error.deactivateFailed'));
    } finally {
      setDeactivating(false);
    }
  };

  const handleIgnoreColumn = async (column: string) => {
    setColumnStatus(s => ({ ...s, [column]: { loading: true, message: '', ok: false } }));
    try {
      await readerMappingsApi.ignoreColumn(reader, { map_key: column });
      await load();
    } catch (e) {
      setColumnStatus(s => ({
        ...s,
        [column]: { loading: false, message: e instanceof Error ? e.message : t('readerMappingsPanel.error.ignoreFailed'), ok: false },
      }));
    }
  };

  const handleUnignoreColumn = async (c: UnmappedColumn) => {
    if (c.mapping_id == null) return;
    setColumnStatus(s => ({ ...s, [c.column]: { loading: true, message: '', ok: false } }));
    try {
      await readerMappingsApi.unignore(reader, c.mapping_id);
      await load();
    } catch (e) {
      setColumnStatus(s => ({
        ...s,
        [c.column]: { loading: false, message: e instanceof Error ? e.message : t('readerMappingsPanel.error.unignoreFailed'), ok: false },
      }));
    }
  };

  const handlePreview = async () => {
    setPreviewOpen(true);
    setPreviewLoading(true);
    setPreviewError(null);
    try {
      const res = await readerMappingsApi.preview(reader, activeKind);
      setPreviewData(res);
    } catch (e) {
      setPreviewError(e instanceof Error ? e.message : t('readerMappingsPanel.error.previewFailed'));
    } finally {
      setPreviewLoading(false);
    }
  };

  // ── Table columns, per kind ──────────────────────────────────────────────
  const cols: ColDef<ReaderMapping>[] = (() => {
    const dim = (m: ReaderMapping, node: React.ReactNode) => (
      <span style={{ opacity: m.status === 'archived' ? 0.55 : 1 }}>{node}</span>
    );
    const statusCol: ColDef<ReaderMapping> = {
      label: t('readerMappingsPanel.col.status'), width: 90,
      render: (m) => <StatusPill status={m.status === 'active' ? 'ok' : 'missing'}>{m.status}</StatusPill>,
    };
    const updatedCol: ColDef<ReaderMapping> = {
      label: t('readerMappingsPanel.col.updated'), mono: true, size: 11, width: 130,
      render: (m) => fmtDate(m.updated_at),
    };
    const actionsCol: ColDef<ReaderMapping> = {
      label: t('readerMappingsPanel.col.actions'), align: 'right', width: 190,
      render: (m) => {
        const st = rowStatus[m.id];
        return (
          <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end', alignItems: 'center' }}>
            {st?.message && (
              <span style={{ fontSize: 10, color: st.ok ? 'var(--zone-green-fg)' : 'var(--color-danger)' }}>
                {st.message}
              </span>
            )}
            {kind !== 'known_etf' && <IconBtn icon="edit" title={t('readerMappingsPanel.action.edit')} onClick={() => openEdit(m)} />}
            {m.status === 'active' ? (
              <ActionBtn icon="archive" onClick={() => handleArchive(m)} disabled={st?.loading}>
                {t('readerMappingsPanel.action.archive')}
              </ActionBtn>
            ) : (
              <ActionBtn icon="unarchive" onClick={() => handleRestore(m)} disabled={st?.loading}>
                {t('readerMappingsPanel.action.restore')}
              </ActionBtn>
            )}
            {m.status === 'archived' && (
              <IconBtn icon="delete" title={t('readerMappingsPanel.action.delete')} danger onClick={() => handleDelete(m)} />
            )}
          </div>
        );
      },
    };

    if (kind === 'fs_column') {
      return [
        { label: t('readerMappingsPanel.col.excelColumn'), mono: true, width: 180, render: (m) => dim(m, m.map_key) },
        { label: t('readerMappingsPanel.col.assetId'), mono: true, width: 180, render: (m) => dim(m, m.map_value.asset_id) },
        { label: t('readerMappingsPanel.col.displayName'), width: 200, render: (m) => dim(m, m.map_value.asset_name) },
        statusCol, updatedCol, actionsCol,
      ];
    }
    if (kind === 'id_field_map') {
      return [
        {
          label: t('readerMappingsPanel.col.field'), mono: true, width: 140,
          render: (m) => dim(m, m.map_key.split(':')[0]),
        },
        {
          label: t('readerMappingsPanel.col.sourceLabel'), mono: true, width: 180,
          render: (m) => dim(m, m.map_key.split(':').slice(1).join(':')),
        },
        { label: t('readerMappingsPanel.col.code'), mono: true, width: 120, render: (m) => dim(m, m.map_value.code) },
        statusCol, updatedCol, actionsCol,
      ];
    }
    if (kind === 'known_etf') {
      return [
        { label: t('readerMappingsPanel.col.ticker'), mono: true, width: 160, render: (m) => dim(m, m.map_key) },
        { label: t('readerMappingsPanel.col.etf'), width: 100, render: (m) => dim(m, m.map_value.etf ? t('readerMappingsPanel.yes') : t('readerMappingsPanel.no')) },
        statusCol, updatedCol, actionsCol,
      ];
    }
    if (kind === 'symbol_norm') {
      return [
        { label: t('readerMappingsPanel.col.rawSymbol'), mono: true, width: 160, render: (m) => dim(m, m.map_key) },
        { label: t('readerMappingsPanel.col.normalizesTo'), mono: true, width: 160, render: (m) => dim(m, m.map_value.to) },
        statusCol, updatedCol, actionsCol,
      ];
    }
    // action_map / type_map
    return [
      { label: t('readerMappingsPanel.col.rawLabel'), mono: true, width: 220, render: (m) => dim(m, m.map_key) },
      { label: t('readerMappingsPanel.col.transactionType'), mono: true, width: 160, render: (m) => dim(m, m.map_value.type) },
      statusCol, updatedCol, actionsCol,
    ];
  })();

  const unmappedColumns = data?.unmapped_columns ?? [];
  // ADR-023 A4.1 — only 'candidate' is genuinely actionable; everything else
  // (native/computed/liability/ignored) is "not melted by design". Only
  // fs_column ever produces the non-candidate categories.
  const candidateColumns = unmappedColumns.filter(c => c.category === 'candidate');
  const notMeltedColumns = unmappedColumns.filter(c => c.category !== 'candidate');
  const saveGate = getSaveGate(kind, modalMode, form, t);

  // id_field_map "Field" select options: union of fields already present in
  // the current mapping list, plus the spec's documented fallback fields for
  // this reader (covers insurance's zero-rows fresh-DB case) — the list
  // endpoint doesn't expose a distinct valid-fields array, so this is derived
  // rather than server-provided (see docs/api-specs/reader-mappings.md C2).
  const idFieldOptions = (() => {
    if (kind !== 'id_field_map') return [];
    const fromRows = (data?.mappings ?? []).map(m => m.map_key.split(':')[0]);
    const fallback = FALLBACK_ID_TEMPLATE_FIELDS[reader] ?? [];
    return Array.from(new Set([...fallback, ...fromRows]));
  })();

  return (
    <div className="px-1 py-1">
      {/* Kind switcher — only rendered when a reader has more than one kind (schwab) */}
      {kinds.length > 1 && (
        <div className="flex items-center gap-1.5 mb-3">
          {kinds.map(k => (
            <React.Fragment key={k.value}>
              <ChipBtn primary={activeKind === k.value} onClick={() => setActiveKind(k.value)}>
                {k.label}
              </ChipBtn>
            </React.Fragment>
          ))}
        </div>
      )}

      {/* Panel header */}
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div>
          <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 dark:text-slate-500">
            {kinds.length > 1 ? (kinds.find(k => k.value === activeKind)?.label ?? t('readerMappingsPanel.kindLabel.mappings')) : t('readerMappingsPanel.assetMappings')}
          </div>
          <div className="text-[11px] text-slate-500 dark:text-slate-400 font-mono mt-0.5">
            {kind} · {t('readerMappingsPanel.mappingCount', { count: data?.mappings.length ?? 0 })}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <ActionBtn icon="visibility" onClick={handlePreview} disabled={previewLoading}>
            {previewLoading ? t('readerMappingsPanel.action.previewing') : t('readerMappingsPanel.action.previewAgainstFile')}
          </ActionBtn>
          <ActionBtn variant="primary" icon="add" onClick={() => openAdd()}>
            {t('readerMappingsPanel.action.addMapping')}
          </ActionBtn>
        </div>
      </div>

      {loadError && (
        <div className="mb-3 text-[12px] text-red-600 dark:text-red-400">{loadError}</div>
      )}

      {/* Unmapped strip — candidates only. fs_column: Excel columns.
          id_field_map: unmapped "field:label" values, "+ Map" pre-fills field+label. */}
      {candidateColumns.length > 0 && (
        <div className="mb-3 px-3.5 py-2.5 rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-300/60 dark:border-amber-700/50">
          <div className="text-[11px] font-bold text-amber-800 dark:text-amber-400 mb-1.5">
            {kind === 'fs_column'
              ? t('readerMappingsPanel.unmappedColumnsFound', { count: candidateColumns.length })
              : t('readerMappingsPanel.unmappedLabelsFound', { count: candidateColumns.length })}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {candidateColumns.map((c) => {
              const st = columnStatus[c.column];
              const [f, ...rest] = c.column.split(':');
              const prefill = kind === 'id_field_map'
                ? { field: f, label: rest.join(':') }
                : { map_key: c.column };
              return (
                <div key={c.column} className="inline-flex items-center gap-1">
                  <button
                    onClick={() => openAdd(prefill)}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono bg-white dark:bg-slate-800 text-amber-700 dark:text-amber-300 border border-amber-300 dark:border-amber-700 hover:bg-amber-100 dark:hover:bg-amber-900/40 transition-colors"
                  >
                    <span className="material-symbols-outlined !text-[12px]">add</span>
                    {c.column}
                  </button>
                  {kind === 'fs_column' && (
                    <button
                      onClick={() => handleIgnoreColumn(c.column)}
                      disabled={st?.loading}
                      title={t('readerMappingsPanel.ignoreColumnTitle')}
                      className="px-1.5 py-0.5 rounded text-[10px] font-mono text-slate-400 dark:text-slate-500 border border-transparent hover:border-slate-300 dark:hover:border-slate-600 hover:text-slate-600 dark:hover:text-slate-300 transition-colors disabled:opacity-50"
                    >
                      {st?.loading ? '…' : t('readerMappingsPanel.action.ignore')}
                    </button>
                  )}
                  {st?.message && (
                    <span className="text-[10px] text-red-600 dark:text-red-400">{st.message}</span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Collapsible: structural non-asset columns (native/computed/liability/ignored) — fs_column only */}
      {notMeltedColumns.length > 0 && (
        <div className="mb-3">
          <button
            onClick={() => setNotMeltedOpen(o => !o)}
            className="flex items-center gap-1 text-[11px] text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 transition-colors"
          >
            <span className="material-symbols-outlined !text-[14px]">
              {notMeltedOpen ? 'expand_more' : 'chevron_right'}
            </span>
            {t('readerMappingsPanel.notMeltedByDesign', { count: notMeltedColumns.length })}
          </button>
          {notMeltedOpen && (
            <div className="mt-2 flex flex-wrap gap-1.5 pl-1">
              {notMeltedColumns.map((c) => {
                const st = columnStatus[c.column];
                return (
                  <div
                    key={c.column}
                    className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-mono bg-slate-50 dark:bg-slate-800/60 text-slate-500 dark:text-slate-400 border border-slate-200 dark:border-slate-700"
                  >
                    <span className="text-[9px] uppercase tracking-wider text-slate-400 dark:text-slate-500 font-mono">
                      {c.category}
                    </span>
                    {c.column}
                    {c.category === 'ignored' && c.mapping_id != null && (
                      <button
                        onClick={() => handleUnignoreColumn(c)}
                        disabled={st?.loading}
                        className="text-[10px] text-sky-600 dark:text-sky-400 hover:underline disabled:opacity-50"
                      >
                        {st?.loading ? '…' : t('readerMappingsPanel.action.unignore')}
                      </button>
                    )}
                    {st?.message && (
                      <span className="text-[10px] text-red-600 dark:text-red-400">{st.message}</span>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Mappings table */}
      <div className="rounded-lg border border-slate-200 dark:border-border-dark overflow-x-auto bg-white dark:bg-card-dark">
        <OpsTable<ReaderMapping> cols={cols} rows={data?.mappings ?? []} rowKey={(m) => m.id} density="dense" />
      </div>
      {!loading && (data?.mappings.length ?? 0) === 0 && (
        <div className="px-4 py-6 text-center text-[12px] text-slate-400 dark:text-slate-500 italic">
          {data?.defaults_only ? t('readerMappingsPanel.noMappingsYetDefaultsOnly') : t('readerMappingsPanel.noMappingsYet')}
        </div>
      )}

      {/* Add / Edit modal */}
      {modalMode && (
        <ModalShell
          title={`${modalMode === 'add' ? t('readerMappingsPanel.action.add') : t('readerMappingsPanel.action.edit')} ${modalTitle(kind, t)}`}
          subtitle={reader}
          onClose={closeModal}
          footer={
            <>
              <ActionBtn variant="secondary" onClick={closeModal}>{t('readerMappingsPanel.action.cancel')}</ActionBtn>
              <ActionBtn variant="primary" onClick={handleSave} disabled={saving || saveGate.blocked}>
                {saving ? t('readerMappingsPanel.action.saving') : t('readerMappingsPanel.action.save')}
              </ActionBtn>
            </>
          }
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {kind === 'fs_column' && (
              <>
                <div>
                  <FieldLabel>{t('readerMappingsPanel.col.excelColumn')}</FieldLabel>
                  {modalMode === 'add' ? (
                    <FormInput
                      value={form.map_key}
                      onChange={(v) => setForm((f) => ({ ...f, map_key: v }))}
                      placeholder={t('readerMappingsPanel.placeholder.excelColumn')}
                      mono
                    />
                  ) : (
                    <div style={{
                      fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--color-fg-2)',
                      padding: '8px 10px', background: 'var(--color-border-soft)', borderRadius: 6,
                    }}>
                      {form.map_key}
                      <span style={{ marginLeft: 8, fontSize: 10, color: 'var(--color-fg-4)' }}>
                        {t('readerMappingsPanel.notEditableRenameColumn')}
                      </span>
                    </div>
                  )}
                </div>
                <div>
                  <FieldLabel>{t('readerMappingsPanel.col.assetId')}</FieldLabel>
                  <FormInput
                    value={form.asset_id}
                    onChange={(v) => setForm((f) => ({ ...f, asset_id: v }))}
                    placeholder={t('readerMappingsPanel.placeholder.assetId')}
                    mono
                  />
                  <div style={{ marginTop: 4, fontSize: 10, color: 'var(--color-fg-4)' }}>
                    <Trans t={t} i18nKey="readerMappingsPanel.fsColumn.prefixConventions" components={{ code: <code /> }} />
                    {modalMode === 'edit' && <>{' '}{t('readerMappingsPanel.fsColumn.assetIdEditOnly')}</>}
                  </div>
                </div>
                <div>
                  <FieldLabel>{t('readerMappingsPanel.col.displayName')}</FieldLabel>
                  <FormInput
                    value={form.asset_name}
                    onChange={(v) => setForm((f) => ({ ...f, asset_name: v }))}
                    placeholder={t('readerMappingsPanel.placeholder.displayName')}
                  />
                </div>
                <div>
                  <FieldLabel>{t('readerMappingsPanel.fieldLabel.currency')}</FieldLabel>
                  <FormSelect
                    value={form.currency}
                    onChange={() => { /* locked — fs_column is always CNY */ }}
                    options={[{ value: 'CNY', label: 'CNY' }]}
                  />
                  <div style={{ marginTop: 4, fontSize: 10, color: 'var(--color-fg-4)' }}>
                    {t('readerMappingsPanel.fsColumn.currencyLockedNote')}
                  </div>
                </div>
              </>
            )}

            {kind === 'id_field_map' && (
              <>
                <div>
                  <FieldLabel>{t('readerMappingsPanel.col.field')}</FieldLabel>
                  {modalMode === 'add' ? (
                    idFieldOptions.length > 0 ? (
                      <FormSelect
                        value={form.field}
                        onChange={(v) => setForm((f) => ({ ...f, field: v }))}
                        options={idFieldOptions.map(f => ({ value: f, label: f }))}
                      />
                    ) : (
                      <FormInput
                        value={form.field}
                        onChange={(v) => setForm((f) => ({ ...f, field: v }))}
                        placeholder={t('readerMappingsPanel.placeholder.field')}
                        mono
                      />
                    )
                  ) : (
                    <div style={{
                      fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--color-fg-2)',
                      padding: '8px 10px', background: 'var(--color-border-soft)', borderRadius: 6,
                    }}>
                      {form.field}
                    </div>
                  )}
                  <div style={{ marginTop: 4, fontSize: 10, color: 'var(--color-fg-4)' }}>
                    {t('readerMappingsPanel.idFieldMap.fieldHelperNote', { reader })}
                  </div>
                </div>
                <div>
                  <FieldLabel>{t('readerMappingsPanel.col.sourceLabel')}</FieldLabel>
                  {modalMode === 'add' ? (
                    <FormInput
                      value={form.label}
                      onChange={(v) => setForm((f) => ({ ...f, label: v }))}
                      placeholder={t('readerMappingsPanel.placeholder.sourceLabelIdField')}
                      mono
                    />
                  ) : (
                    <div style={{
                      fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--color-fg-2)',
                      padding: '8px 10px', background: 'var(--color-border-soft)', borderRadius: 6,
                    }}>
                      {form.label}
                      <span style={{ marginLeft: 8, fontSize: 10, color: 'var(--color-fg-4)' }}>
                        {t('readerMappingsPanel.notEditableRename')}
                      </span>
                    </div>
                  )}
                </div>
                <div>
                  <FieldLabel>{t('readerMappingsPanel.col.code')}</FieldLabel>
                  <FormInput
                    value={form.code}
                    onChange={(v) => setForm((f) => ({ ...f, code: v }))}
                    placeholder={t('readerMappingsPanel.placeholder.code')}
                    mono
                  />
                  <div style={{ marginTop: 4, fontSize: 10, color: 'var(--color-fg-4)' }}>
                    {t('readerMappingsPanel.idFieldMap.codeHelperNote')}
                  </div>
                </div>
              </>
            )}

            {kind === 'known_etf' && (
              <div>
                <FieldLabel>{t('readerMappingsPanel.col.ticker')}</FieldLabel>
                {modalMode === 'add' ? (
                  <FormInput
                    value={form.map_key}
                    onChange={(v) => setForm((f) => ({ ...f, map_key: v.toUpperCase() }))}
                    placeholder={t('readerMappingsPanel.placeholder.tickerExample')}
                    mono
                  />
                ) : (
                  <div style={{
                    fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--color-fg-2)',
                    padding: '8px 10px', background: 'var(--color-border-soft)', borderRadius: 6,
                  }}>
                    {form.map_key}
                  </div>
                )}
                <div style={{ marginTop: 4, fontSize: 10, color: 'var(--color-fg-4)' }}>
                  {t('readerMappingsPanel.knownEtf.helperNote')}
                </div>
              </div>
            )}

            {kind === 'symbol_norm' && (
              <>
                <div>
                  <FieldLabel>{t('readerMappingsPanel.col.rawSymbol')}</FieldLabel>
                  {modalMode === 'add' ? (
                    <FormInput
                      value={form.map_key}
                      onChange={(v) => setForm((f) => ({ ...f, map_key: v.toUpperCase() }))}
                      placeholder={t('readerMappingsPanel.placeholder.rawSymbolExample')}
                      mono
                    />
                  ) : (
                    <div style={{
                      fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--color-fg-2)',
                      padding: '8px 10px', background: 'var(--color-border-soft)', borderRadius: 6,
                    }}>
                      {form.map_key}
                    </div>
                  )}
                </div>
                <div>
                  <FieldLabel>{t('readerMappingsPanel.col.normalizesTo')}</FieldLabel>
                  <FormInput
                    value={form.to}
                    onChange={(v) => setForm((f) => ({ ...f, to: v.toUpperCase() }))}
                    placeholder={t('readerMappingsPanel.placeholder.normalizesToExample')}
                    mono
                  />
                </div>
              </>
            )}

            {(kind === 'action_map' || kind === 'type_map') && (
              <>
                <div>
                  <FieldLabel>{kind === 'action_map' ? t('readerMappingsPanel.fieldLabel.rawAction') : t('readerMappingsPanel.fieldLabel.rawType')}</FieldLabel>
                  {modalMode === 'add' ? (
                    <FormInput
                      value={form.map_key}
                      onChange={(v) => setForm((f) => ({ ...f, map_key: v }))}
                      placeholder={kind === 'action_map' ? t('readerMappingsPanel.placeholder.rawActionExample') : t('readerMappingsPanel.placeholder.rawTypeExample')}
                      mono
                    />
                  ) : (
                    <div style={{
                      fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--color-fg-2)',
                      padding: '8px 10px', background: 'var(--color-border-soft)', borderRadius: 6,
                    }}>
                      {form.map_key}
                    </div>
                  )}
                </div>
                <div>
                  <FieldLabel>{t('readerMappingsPanel.col.transactionType')}</FieldLabel>
                  <FormSelect
                    value={form.type}
                    onChange={(v) => setForm((f) => ({ ...f, type: v }))}
                    options={kind === 'action_map' ? ACTION_MAP_TYPE_OPTIONS : TYPE_MAP_TYPE_OPTIONS}
                  />
                </div>
              </>
            )}

            {formError && (
              <div style={{ fontSize: 11, color: 'var(--color-danger)' }}>{formError}</div>
            )}
          </div>
        </ModalShell>
      )}

      {/* Archive -> deactivate-asset follow-up — fs_column only */}
      {deactivatePrompt && (
        <ModalShell
          title={t('readerMappingsPanel.deactivatePrompt.title')}
          subtitle={deactivatePrompt.assetId}
          onClose={closeDeactivatePrompt}
          footer={
            <>
              <ActionBtn variant="secondary" onClick={closeDeactivatePrompt} disabled={deactivating}>
                {t('readerMappingsPanel.action.keepHistory')}
              </ActionBtn>
              <ActionBtn variant="primary" onClick={handleDeactivateConfirm} disabled={deactivating}>
                {deactivating ? t('readerMappingsPanel.action.deactivating') : t('readerMappingsPanel.action.deactivateAsset')}
              </ActionBtn>
            </>
          }
        >
          <p style={{ fontSize: 12, color: 'var(--color-fg-2)', lineHeight: 1.5, margin: 0 }}>
            <Trans
              t={t}
              i18nKey="readerMappingsPanel.deactivatePrompt.body"
              values={{ mapKey: deactivatePrompt.mapping.map_key, assetId: deactivatePrompt.assetId }}
              components={{ code: <code />, b: <b /> }}
            />
          </p>
          {deactivateError && (
            <div style={{ marginTop: 10, fontSize: 11, color: 'var(--color-danger)' }}>{deactivateError}</div>
          )}
        </ModalShell>
      )}

      {/* Preview against file — response shape depends on mapping_kind */}
      {previewOpen && (
        <ModalShell
          title={t('readerMappingsPanel.previewModal.title')}
          subtitle={previewData?.file_path ?? reader}
          onClose={() => setPreviewOpen(false)}
        >
          {previewLoading && (
            <div style={{ fontSize: 12, color: 'var(--color-fg-4)' }}>{t('readerMappingsPanel.previewModal.running')}</div>
          )}
          {previewError && (
            <div style={{ fontSize: 12, color: 'var(--color-danger)' }}>{previewError}</div>
          )}
          {previewData && !previewLoading && (
            <>
              {previewData.file_path == null && (
                <div style={{ fontSize: 12, color: 'var(--color-fg-4)', marginBottom: 10 }}>
                  {t('readerMappingsPanel.previewModal.noFileResolved')}
                </div>
              )}

              {/* fs_column: per-mapping match stats against the melt output */}
              {previewData.mapping_kind === 'fs_column' && 'results' in previewData && (
                <div className="overflow-x-auto" style={{ maxHeight: 360, overflowY: 'auto' }}>
                  <table className="w-full text-left border-collapse text-[11px]">
                    <thead>
                      <tr className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider border-b border-slate-100 dark:border-slate-800/50">
                        <th className="px-2 py-2">{t('readerMappingsPanel.previewModal.col.column')}</th>
                        <th className="px-2 py-2 text-center">{t('readerMappingsPanel.previewModal.col.found')}</th>
                        <th className="px-2 py-2 text-right">{t('readerMappingsPanel.previewModal.col.rows')}</th>
                        <th className="px-2 py-2 text-right">{t('readerMappingsPanel.previewModal.col.latestValue')}</th>
                        <th className="px-2 py-2">{t('readerMappingsPanel.previewModal.col.latestDate')}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800/50">
                      {previewData.results.map((r) => (
                        <tr
                          key={r.map_key}
                          className={r.column_found ? '' : 'bg-rose-50 dark:bg-rose-900/20'}
                        >
                          <td className="px-2 py-1.5 font-mono text-slate-700 dark:text-slate-300">{r.map_key}</td>
                          <td className="px-2 py-1.5 text-center">
                            {r.column_found
                              ? <span className="text-emerald-600 dark:text-emerald-400">✓</span>
                              : <span className="text-rose-600 dark:text-rose-400">✗</span>}
                          </td>
                          <td className="px-2 py-1.5 text-right font-mono text-slate-600 dark:text-slate-300">{r.nonzero_rows}</td>
                          <td className="px-2 py-1.5 text-right font-mono text-slate-600 dark:text-slate-300">
                            {r.latest_value != null ? r.latest_value.toLocaleString() : '—'}
                          </td>
                          <td className="px-2 py-1.5 font-mono text-slate-500 dark:text-slate-400">{r.latest_date ?? '—'}</td>
                        </tr>
                      ))}
                      {previewData.results.length === 0 && (
                        <tr>
                          <td colSpan={5} className="px-2 py-4 text-center text-slate-400 dark:text-slate-500 italic">
                            {t('readerMappingsPanel.previewModal.noMappingsToPreview')}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              )}

              {/* id_field_map: label-scan results (mapped ✓ / unmapped candidate) */}
              {previewData.mapping_kind === 'id_field_map' && 'items' in previewData && (
                <div className="overflow-x-auto" style={{ maxHeight: 360, overflowY: 'auto' }}>
                  <table className="w-full text-left border-collapse text-[11px]">
                    <thead>
                      <tr className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider border-b border-slate-100 dark:border-slate-800/50">
                        <th className="px-2 py-2">{t('readerMappingsPanel.col.field')}</th>
                        <th className="px-2 py-2">{t('readerMappingsPanel.previewModal.col.label')}</th>
                        <th className="px-2 py-2 text-center">{t('readerMappingsPanel.previewModal.col.mapped')}</th>
                        <th className="px-2 py-2">{t('readerMappingsPanel.col.code')}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800/50">
                      {previewData.items.map((it: any) => (
                        <tr key={it.map_key} className={it.mapped ? '' : 'bg-amber-50 dark:bg-amber-900/10'}>
                          <td className="px-2 py-1.5 font-mono text-slate-700 dark:text-slate-300">{it.field}</td>
                          <td className="px-2 py-1.5 font-mono text-slate-700 dark:text-slate-300">{it.label}</td>
                          <td className="px-2 py-1.5 text-center">
                            {it.mapped
                              ? <span className="text-emerald-600 dark:text-emerald-400">{t('readerMappingsPanel.previewModal.mapped')}</span>
                              : <span className="text-amber-600 dark:text-amber-400">{t('readerMappingsPanel.previewModal.unmapped')}</span>}
                          </td>
                          <td className="px-2 py-1.5 font-mono text-slate-500 dark:text-slate-400">{it.code ?? '—'}</td>
                        </tr>
                      ))}
                      {previewData.items.length === 0 && (
                        <tr>
                          <td colSpan={4} className="px-2 py-4 text-center text-slate-400 dark:text-slate-500 italic">
                            {t('readerMappingsPanel.previewModal.noLabelsFound')}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              )}

              {/* known_etf / symbol_norm / action_map / type_map: generic value scan */}
              {previewData.mapping_kind !== 'fs_column' && previewData.mapping_kind !== 'id_field_map' && 'items' in previewData && (
                <div className="overflow-x-auto" style={{ maxHeight: 360, overflowY: 'auto' }}>
                  <table className="w-full text-left border-collapse text-[11px]">
                    <thead>
                      <tr className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider border-b border-slate-100 dark:border-slate-800/50">
                        <th className="px-2 py-2">{t('readerMappingsPanel.previewModal.col.value')}</th>
                        <th className="px-2 py-2 text-center">{t('readerMappingsPanel.previewModal.col.mapped')}</th>
                        <th className="px-2 py-2">{t('readerMappingsPanel.previewModal.col.mappedValue')}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800/50">
                      {(previewData as any).items.map((it: any) => (
                        <tr key={it.value} className={it.mapped ? '' : 'bg-amber-50 dark:bg-amber-900/10'}>
                          <td className="px-2 py-1.5 font-mono text-slate-700 dark:text-slate-300">{it.value}</td>
                          <td className="px-2 py-1.5 text-center">
                            {it.mapped
                              ? <span className="text-emerald-600 dark:text-emerald-400">{t('readerMappingsPanel.previewModal.mapped')}</span>
                              : <span className="text-amber-600 dark:text-amber-400">{t('readerMappingsPanel.previewModal.unmapped')}</span>}
                          </td>
                          <td className="px-2 py-1.5 font-mono text-slate-500 dark:text-slate-400">
                            {it.mapped_value ? JSON.stringify(it.mapped_value) : '—'}
                          </td>
                        </tr>
                      ))}
                      {(previewData as any).items.length === 0 && (
                        <tr>
                          <td colSpan={3} className="px-2 py-4 text-center text-slate-400 dark:text-slate-500 italic">
                            {t('readerMappingsPanel.previewModal.noValuesFound')}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              )}

              {previewData.unmapped_columns.length > 0 && (
                <div className="mt-3 text-[11px] text-slate-400 dark:text-slate-500">
                  {t('readerMappingsPanel.previewModal.unmappedPrefix')} {previewData.unmapped_columns.filter(c => c.category === 'candidate').map(c => c.column).join(', ') || t('readerMappingsPanel.previewModal.none')}
                </div>
              )}
            </>
          )}
        </ModalShell>
      )}
    </div>
  );
};
