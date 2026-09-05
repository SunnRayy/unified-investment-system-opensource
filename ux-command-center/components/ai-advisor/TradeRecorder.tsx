import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AssetSearchResult, CreateTradeRequest, StrategyMemo, TradeLogEntry } from '../../src/services/api';
import { api } from '../../src/services/api';

const COMMON_CURRENCIES = ['USD', 'CNY', 'HKD'] as const;

const truncateMemoTitle = (title: string) => (
  title.length > 30 ? `${title.slice(0, 27)}...` : title
);

export const TradeRecorder: React.FC = () => {
  const { t } = useTranslation('aiAdvisor');
  const [trades, setTrades] = useState<TradeLogEntry[]>([]);
  const [memos, setMemos] = useState<StrategyMemo[]>([]);
  const [loadingList, setLoadingList] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const [logDate, setLogDate] = useState(new Date().toISOString().split('T')[0]);
  const [action, setAction] = useState<'Buy' | 'Sell'>('Buy');
  const [price, setPrice] = useState('');
  const [quantity, setQuantity] = useState('');
  const [reason, setReason] = useState('');
  const [currency, setCurrency] = useState<string>('USD');

  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<AssetSearchResult[]>([]);
  const [selectedAsset, setSelectedAsset] = useState<AssetSearchResult | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);

  const [memoSearchQuery, setMemoSearchQuery] = useState('');
  const [selectedMemo, setSelectedMemo] = useState<StrategyMemo | null>(null);
  const [showMemoDropdown, setShowMemoDropdown] = useState(false);

  const searchTimeoutRef = useRef<ReturnType<typeof window.setTimeout>>();
  const successTimeoutRef = useRef<ReturnType<typeof window.setTimeout>>();
  const searchContainerRef = useRef<HTMLDivElement>(null);
  const memoContainerRef = useRef<HTMLDivElement>(null);

  const parsedPrice = parseFloat(price);
  const parsedQty = parseFloat(quantity);
  const amount = (!Number.isNaN(parsedPrice) && !Number.isNaN(parsedQty)) ? parsedPrice * parsedQty : '';

  const filteredMemos = memos
    .filter((memo) => `${memo.date} ${memo.title}`.toLowerCase().includes(memoSearchQuery.toLowerCase()))
    .slice(0, 20);

  useEffect(() => {
    void fetchTrades();
    void fetchMemos();

    return () => {
      if (searchTimeoutRef.current) {
        window.clearTimeout(searchTimeoutRef.current);
      }
      if (successTimeoutRef.current) {
        window.clearTimeout(successTimeoutRef.current);
      }
    };
  }, []);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (searchContainerRef.current && !searchContainerRef.current.contains(event.target as Node)) {
        setShowDropdown(false);
      }
      if (memoContainerRef.current && !memoContainerRef.current.contains(event.target as Node)) {
        setShowMemoDropdown(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const fetchTrades = async () => {
    try {
      const result = await api.listTrades(50);
      setTrades(result.trades);
      setError(null);
    } catch (e) {
      console.error('Failed to fetch trades', e);
      setError(t('tradeRecorder.errors.loadTrades'));
    } finally {
      setLoadingList(false);
    }
  };

  const fetchMemos = async () => {
    try {
      const result = await api.getStrategyMemos();
      setMemos(result.memos);
    } catch (e) {
      console.error('Failed to fetch memos', e);
    }
  };

  const resetForm = () => {
    setSearchQuery('');
    setSearchResults([]);
    setSelectedAsset(null);
    setPrice('');
    setQuantity('');
    setReason('');
    setCurrency('USD');
    setMemoSearchQuery('');
    setSelectedMemo(null);
    setShowDropdown(false);
    setShowMemoDropdown(false);
  };

  const queueSuccessReset = () => {
    if (successTimeoutRef.current) {
      window.clearTimeout(successTimeoutRef.current);
    }
    successTimeoutRef.current = window.setTimeout(() => {
      setSuccessMessage(null);
      resetForm();
    }, 2500);
  };

  const selectAsset = (asset: AssetSearchResult) => {
    setSelectedAsset(asset);
    setSearchQuery(`${asset.display_name} (${asset.asset_id})`);
    setCurrency(asset.base_currency || 'USD');
    setShowDropdown(false);
  };

  const selectCustomAsset = () => {
    const customId = searchQuery.trim().toUpperCase();
    if (!customId) return;
    selectAsset({
      asset_id: customId,
      display_name: customId,
      asset_class: 'Unknown',
      base_currency: 'USD',
    });
  };

  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setSearchQuery(value);
    setSelectedAsset(null);

    if (value.length < 2) {
      setSearchResults([]);
      setShowDropdown(false);
      setIsSearching(false);
      return;
    }

    setShowDropdown(true);
    setIsSearching(true);

    if (searchTimeoutRef.current) {
      window.clearTimeout(searchTimeoutRef.current);
    }
    searchTimeoutRef.current = window.setTimeout(async () => {
      try {
        const res = await api.searchAssets(value);
        setSearchResults(res.assets);
      } catch (err) {
        console.error('Asset search failed:', err);
      } finally {
        setIsSearching(false);
      }
    }, 300);
  };

  const handleMemoSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setMemoSearchQuery(value);
    setSelectedMemo(null);
    setShowMemoDropdown(true);
  };

  const selectMemo = (memo: StrategyMemo) => {
    setSelectedMemo(memo);
    setMemoSearchQuery(`${memo.date} — ${memo.title}`);
    setShowMemoDropdown(false);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedAsset) {
      setError(t('tradeRecorder.errors.selectAsset'));
      return;
    }

    setSaving(true);
    setError(null);
    setSuccessMessage(null);

    try {
      const payload: CreateTradeRequest = {
        log_date: logDate,
        asset_id: selectedAsset.asset_id,
        asset_name: selectedAsset.display_name,
        action,
        price: Number.isNaN(parsedPrice) ? undefined : parsedPrice,
        quantity: Number.isNaN(parsedQty) ? undefined : parsedQty,
        amount: typeof amount === 'number' ? amount : undefined,
        currency,
        decision_reason: reason.trim() || undefined,
        memo_id: selectedMemo?.id,
      };

      await api.createTrade(payload);
      await fetchTrades();

      const message = selectedMemo
        ? t('tradeRecorder.recordedLinkedToMemo', { title: selectedMemo.title })
        : t('tradeRecorder.recordedAutoDetect');
      setSuccessMessage(message);
      queueSuccessReset();
    } catch (err) {
      console.error('Submit failed', err);
      setError(err instanceof Error ? err.message : t('tradeRecorder.errors.saveTrade'));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    if (!window.confirm(t('tradeRecorder.confirmDelete'))) return;
    setError(null);
    try {
      await api.deleteTrade(id);
      await fetchTrades();
    } catch (e) {
      console.error('Delete failed', e);
      setError(e instanceof Error ? e.message : t('tradeRecorder.errors.deleteTrade'));
    }
  };

  const memoTitleById = new Map(memos.map((memo) => [memo.id, memo.title]));

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-slate-200 bg-white p-5 dark:border-border-dark dark:bg-card-dark">
        <h3 className="mb-4 text-sm font-semibold text-slate-800 dark:text-slate-100">{t('tradeRecorder.recordTrade')}</h3>

        {successMessage && (
          <div className="mb-4 flex items-center gap-2 rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-700 dark:border-green-800 dark:bg-green-900/20 dark:text-green-300">
            <span className="material-symbols-outlined !text-[16px]">check_circle</span>
            {successMessage}
          </div>
        )}

        {error && (
          <div className="mb-4 flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-800 dark:bg-red-900/20 dark:text-red-400">
            <span className="material-symbols-outlined !text-[16px]">error</span>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
            <div>
              <label className="mb-1 block text-xs text-slate-500">{t('tradeRecorder.date')}</label>
              <input
                type="date"
                value={logDate}
                onChange={(e) => setLogDate(e.target.value)}
                required
                className="w-full rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-primary/50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
              />
            </div>

            <div className="relative lg:col-span-2" ref={searchContainerRef}>
              <label className="mb-1 block text-xs text-slate-500">{t('tradeRecorder.asset')}</label>
              <input
                type="text"
                value={searchQuery}
                onChange={handleSearchChange}
                onFocus={() => {
                  if (searchQuery.length >= 2) {
                    setShowDropdown(true);
                  }
                }}
                placeholder={t('tradeRecorder.searchAssetPlaceholder')}
                required
                className="w-full rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-primary/50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
              />

              {showDropdown && (
                <div className="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded-md border border-slate-200 bg-white py-1 shadow-lg dark:border-slate-700 dark:bg-slate-800">
                  {isSearching ? (
                    <div className="px-3 py-2 text-sm text-slate-500">{t('tradeRecorder.searching')}</div>
                  ) : searchResults.length === 0 ? (
                    <div className="px-3 py-2 text-sm text-slate-500">
                      <div>{t('tradeRecorder.noAssetsFound')}</div>
                      {searchQuery.trim().length >= 2 && (
                        <button
                          type="button"
                          onClick={selectCustomAsset}
                          className="mt-2 text-left text-sm font-medium text-primary hover:underline"
                        >
                          {t('tradeRecorder.useCustomAsset', { code: searchQuery.trim().toUpperCase() })}
                        </button>
                      )}
                    </div>
                  ) : (
                    searchResults.map((asset) => (
                      <button
                        key={asset.asset_id}
                        type="button"
                        onClick={() => selectAsset(asset)}
                        className="flex w-full flex-col px-3 py-2 text-left transition-colors hover:bg-slate-50 dark:hover:bg-slate-700"
                      >
                        <span className="text-sm font-medium text-slate-800 dark:text-slate-200">{asset.display_name}</span>
                        <div className="mt-0.5 flex items-center gap-2">
                          <span className="text-[10px] text-slate-500">{asset.asset_id}</span>
                          {asset.asset_class && (
                            <span className="rounded bg-slate-100 px-1.5 text-[10px] text-slate-600 dark:bg-slate-700 dark:text-slate-300">
                              {asset.asset_class}
                            </span>
                          )}
                        </div>
                      </button>
                    ))
                  )}
                </div>
              )}
            </div>

            <div>
              <label className="mb-1 block text-xs text-slate-500">{t('tradeRecorder.action')}</label>
              <div className="flex rounded-md shadow-sm">
                <button
                  type="button"
                  onClick={() => setAction('Buy')}
                  className={`flex-1 rounded-l-md border px-3 py-1.5 text-sm font-medium transition-colors ${
                    action === 'Buy'
                      ? 'border-green-200 bg-green-100 text-green-700 dark:border-green-800 dark:bg-green-900/40 dark:text-green-400'
                      : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300'
                  }`}
                >
                  {t('tradeRecorder.buy')}
                </button>
                <button
                  type="button"
                  onClick={() => setAction('Sell')}
                  className={`flex-1 rounded-r-md border-y border-r px-3 py-1.5 text-sm font-medium transition-colors ${
                    action === 'Sell'
                      ? 'border-red-200 bg-red-100 text-red-700 dark:border-red-800 dark:bg-red-900/40 dark:text-red-400'
                      : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300'
                  }`}
                >
                  {t('tradeRecorder.sell')}
                </button>
              </div>
            </div>

            <div>
              <label className="mb-1 block text-xs text-slate-500">{t('tradeRecorder.price')}</label>
              <input
                type="number"
                step="any"
                min="0"
                placeholder={t('tradeRecorder.price')}
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                className="w-full rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-primary/50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
              />
            </div>

            <div>
              <label className="mb-1 block text-xs text-slate-500">{t('tradeRecorder.quantity')}</label>
              <input
                type="number"
                step="any"
                min="0"
                placeholder={t('tradeRecorder.quantity')}
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                className="w-full rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-primary/50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
              />
            </div>

            <div className="lg:col-span-2">
              <label className="mb-1 block text-xs text-slate-500">{t('tradeRecorder.totalAmount')}</label>
              <div className="flex">
                <select
                  aria-label={t('tradeRecorder.currency')}
                  value={currency}
                  onChange={(e) => setCurrency(e.target.value)}
                  className="rounded-l-md border border-r-0 border-slate-200 bg-slate-50 px-3 py-1.5 text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-primary/50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                >
                  {COMMON_CURRENCIES.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
                <input
                  type="text"
                  readOnly
                  placeholder={t('tradeRecorder.amount')}
                  value={amount}
                  className="w-full rounded-r-md border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm text-slate-800 focus:outline-none dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                />
              </div>
            </div>

            <div className="md:col-span-2 lg:col-span-4">
              <label className="mb-1 block text-xs text-slate-500">{t('tradeRecorder.decisionReason')}</label>
              <textarea
                placeholder={t('tradeRecorder.reasonPlaceholder')}
                rows={2}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-primary/50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
              />
            </div>

            <div className="relative md:col-span-2 lg:col-span-4" ref={memoContainerRef}>
              <label className="mb-1 block text-xs text-slate-500">{t('tradeRecorder.relatedMemo')}</label>
              <input
                type="text"
                aria-label={t('tradeRecorder.relatedMemo')}
                value={memoSearchQuery}
                onChange={handleMemoSearchChange}
                onFocus={() => setShowMemoDropdown(true)}
                placeholder={t('tradeRecorder.searchMemoPlaceholder')}
                className="w-full rounded-md border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-primary/50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
              />

              {showMemoDropdown && (
                <div className="absolute z-10 mt-1 max-h-56 w-full overflow-auto rounded-md border border-slate-200 bg-white py-1 shadow-lg dark:border-slate-700 dark:bg-slate-800">
                  {filteredMemos.length === 0 ? (
                    <div className="px-3 py-2 text-sm text-slate-500">{t('tradeRecorder.noMatchingMemos')}</div>
                  ) : (
                    filteredMemos.map((memo) => (
                      <button
                        key={memo.id}
                        type="button"
                        onClick={() => selectMemo(memo)}
                        className="block w-full px-3 py-2 text-left text-sm text-slate-700 transition-colors hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-700"
                      >
                        {memo.date} — {memo.title}
                      </button>
                    ))
                  )}
                </div>
              )}
            </div>
          </div>

          <div className="pt-2 space-y-2">
            <p className="text-xs text-slate-500 dark:text-slate-400">
              {t('tradeRecorder.footerNote')}
            </p>
            <div className="flex justify-end">
              <button
                type="submit"
                disabled={saving || !selectedAsset}
                className="rounded-md bg-primary px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-primary/90 disabled:opacity-50"
              >
                {saving ? t('tradeRecorder.recording') : t('tradeRecorder.recordTrade')}
              </button>
            </div>
          </div>
        </form>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-5 dark:border-border-dark dark:bg-card-dark">
        <h3 className="mb-4 text-sm font-semibold text-slate-800 dark:text-slate-100">{t('tradeRecorder.recentTrades')}</h3>

        {loadingList ? (
          <div className="py-4 text-center text-sm text-slate-500">{t('tradeRecorder.loadingTrades')}</div>
        ) : trades.length === 0 ? (
          <div className="py-6 text-center text-sm text-slate-500">{t('tradeRecorder.noRecentTrades')}</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left">
              <thead>
                <tr className="border-b border-slate-100 dark:border-slate-800">
                  <th className="px-3 py-2 text-[10px] font-semibold uppercase text-slate-500">{t('tradeRecorder.date')}</th>
                  <th className="px-3 py-2 text-[10px] font-semibold uppercase text-slate-500">{t('tradeRecorder.asset')}</th>
                  <th className="px-3 py-2 text-[10px] font-semibold uppercase text-slate-500">{t('tradeRecorder.action')}</th>
                  <th className="px-3 py-2 text-[10px] font-semibold uppercase text-slate-500 text-right">{t('tradeRecorder.price')}</th>
                  <th className="px-3 py-2 text-[10px] font-semibold uppercase text-slate-500 text-right">{t('tradeRecorder.qty')}</th>
                  <th className="px-3 py-2 text-[10px] font-semibold uppercase text-slate-500 text-right">{t('tradeRecorder.amount')}</th>
                  <th className="px-3 py-2 text-[10px] font-semibold uppercase text-slate-500">{t('tradeRecorder.memo')}</th>
                  <th className="w-8 px-3 py-2 text-[10px] font-semibold uppercase text-slate-500" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {trades.map((trade) => {
                  const linkedMemoTitle = trade.linked_memo_id ? memoTitleById.get(trade.linked_memo_id) : null;

                  return (
                    <tr key={trade.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/40">
                      <td className="whitespace-nowrap px-3 py-2.5 text-xs text-slate-600 dark:text-slate-400">
                        {trade.log_date}
                      </td>
                      <td className="px-3 py-2.5">
                        <div className="text-xs font-medium text-slate-800 dark:text-slate-200">{trade.asset_name || trade.asset_id}</div>
                        <div className="text-[10px] text-slate-400">{trade.asset_id}</div>
                        {trade.decision_reason && (
                          <div className="mt-0.5 max-w-[200px] truncate text-[10px] text-slate-500" title={trade.decision_reason}>
                            {trade.decision_reason}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2.5">
                        <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${
                          trade.action === 'Buy'
                            ? 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400'
                            : 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400'
                        }`}>
                          {trade.action}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-right text-xs text-slate-600 dark:text-slate-400">
                        {trade.price != null ? trade.price.toFixed(2) : '-'}
                      </td>
                      <td className="px-3 py-2.5 text-right text-xs text-slate-600 dark:text-slate-400">
                        {trade.quantity != null ? trade.quantity.toFixed(2) : '-'}
                      </td>
                      <td className="px-3 py-2.5 text-right text-xs font-medium text-slate-700 dark:text-slate-300">
                        {trade.amount != null ? `${trade.amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${trade.currency ? ' ' + trade.currency : ''}` : '-'}
                      </td>
                      <td className="px-3 py-2.5 text-xs text-slate-600 dark:text-slate-400">
                        {trade.linked_memo_id && linkedMemoTitle ? (
                          <span className="inline-flex max-w-[220px] rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                            {truncateMemoTitle(linkedMemoTitle)}
                          </span>
                        ) : (
                          '—'
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        {trade.suggestion_source === 'manual' && (
                          <button
                            type="button"
                            onClick={(e) => handleDelete(e, trade.id)}
                            className="transition-colors hover:text-red-500"
                            title={t('tradeRecorder.deleteTrade')}
                          >
                            <span className="material-symbols-outlined !text-[14px]">delete</span>
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
