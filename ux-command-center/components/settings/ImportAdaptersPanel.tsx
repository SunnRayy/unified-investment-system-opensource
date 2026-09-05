import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { SettingsAPI, ImportAdapterStagedRow } from '../../src/services/api';
import { StepAdapter } from './wizard/StepAdapter';
import { StepFile } from './wizard/StepFile';
import { StepValidate } from './wizard/StepValidate';
import { StepStage } from './wizard/StepStage';
import { StepApprove } from './wizard/StepApprove';

type Step = 1 | 2 | 3 | 4 | 5;

export const ImportAdaptersPanel: React.FC = () => {
  const { t } = useTranslation('system');
  const [step, setStep] = useState<Step>(1);
  const [importType, setImportType] = useState<'holdings' | 'transactions' | 'accounts'>('holdings');
  const [file, setFile] = useState<File | null>(null);
  const [runId, setRunId] = useState<number | null>(null);
  const [sourceColumns, setSourceColumns] = useState<string[]>([]);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<string[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [sampleData, setSampleData] = useState<Record<string, any> | null>(null);
  const [stagedCount, setStagedCount] = useState(0);
  const [stagedRows, setStagedRows] = useState<ImportAdapterStagedRow[]>([]);
  const [headerRow, setHeaderRow] = useState(1);

  // Staging configuration
  const [sourceSystem, setSourceSystem] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [prefixes, setPrefixes] = useState('');
  const [baseCurrency, setBaseCurrency] = useState('USD');
  const [autoSync, setAutoSync] = useState(true);

  // Approve step options
  const [generateReader, setGenerateReader] = useState(true);
  const [generatedReaderKey, setGeneratedReaderKey] = useState<string | undefined>(undefined);
  const [readerWarning, setReaderWarning] = useState<string | undefined>(undefined);

  const [loading, setLoading] = useState(false);
  const [stageError, setStageError] = useState<string | null>(null);
  const [isExpanded, setIsExpanded] = useState(false);

  // Live client-side errors that update as the user adjusts mappings — mirrors backend required_fields()
  const liveMappingErrors = useMemo(() => {
    const mappedTargets = new Set(Object.values(mapping).filter(v => v && v !== 'ignore'));
    const required = importType === 'holdings'
      ? ['asset_id', 'quantity']
      : ['asset_id', 'transaction_date', 'transaction_type'];
    return required.filter(f => !mappedTargets.has(f)).map(f => `missing:${f}`);
  }, [mapping, importType]);

  const canContinue = useMemo(() => {
    if (step === 1) return !!importType;
    if (step === 2) return !!file;
    if (step === 3) return runId !== null && liveMappingErrors.length === 0;
    if (step === 4) return !!sourceSystem;
    return true;
  }, [step, importType, file, liveMappingErrors, runId, sourceSystem]);

  const handleNext = async () => {
    if (step === 1) {
      setStep(2);
    } else if (step === 2) {
      if (!file) return;
      setLoading(true);
      try {
        const upload = await SettingsAPI.uploadImportAdapterFile('custom', importType, file, headerRow - 1);
        setRunId(upload.run_id);
        setSourceColumns(upload.headers || []);
        // Backend returns {target_field: source_column} — invert to {source_column: target_field} for UI
        const backendMapping = upload.inferred_mapping || {};
        const uiMapping: Record<string, string> = {};
        // Initialize all headers as 'ignore'
        for (const h of (upload.headers || [])) {
          uiMapping[h] = 'ignore';
        }
        // Overlay inferred matches
        for (const [target, source] of Object.entries(backendMapping)) {
          if (source && typeof source === 'string') {
            uiMapping[source] = target;
          }
        }
        setMapping(uiMapping);
        setSampleData(upload.preview_rows?.[0] || null);
        
        // Initial validation
        const validation = await SettingsAPI.validateImportAdapter('custom', upload.run_id);
        setErrors(validation.errors || []);
        setWarnings(validation.warnings || []);
        setStep(3);
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    } else if (step === 3) {
      if (!runId) return;
      setLoading(true);
      setStageError(null);
      try {
        // Invert UI mapping {source: target} back to backend format {target: source}
        const backendMapping: Record<string, string> = {};
        for (const [source, target] of Object.entries(mapping)) {
          if (target && target !== 'ignore') {
            backendMapping[target] = source;
          }
        }
        await SettingsAPI.configureImportAdapter('custom', {
          run_id: runId,
          column_mapping: backendMapping,
          fx_rate: 1.0,
        });
        const res = await SettingsAPI.stageImportAdapter('custom', runId);
        setStagedCount(res.staged_rows);
        const staged = await SettingsAPI.getStagedRows('custom', runId);
        setStagedRows(staged.rows);
        setStep(4);
      } catch (e: any) {
        console.error(e);
        setStageError(e?.message || t('importAdaptersPanel.stagingFailed'));
      } finally {
        setLoading(false);
      }
    } else if (step === 4) {
      setStep(5);
    } else if (step === 5) {
      handleFinalApprove();
    }
  };

  const handleReAutoMap = async () => {
    if (!runId) return;
    setLoading(true);
    try {
      const validation = await SettingsAPI.validateImportAdapter('custom', runId);
      setErrors(validation.errors || []);
      setWarnings(validation.warnings || []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const handleFinalApprove = async () => {
    if (!sourceSystem) return;
    setLoading(true);
    setGeneratedReaderKey(undefined);
    setReaderWarning(undefined);
    try {
      const res = await SettingsAPI.approveImportAdapter('custom', {
        source_system: sourceSystem,
        asset_prefixes: prefixes.split(',').map(s => s.trim()).filter(Boolean),
        authority_priority: 50,
        generate_reader: generateReader,
        ...(displayName ? { display_name: displayName } : {}),
      });
      if (res.generated_reader_key) {
        setGeneratedReaderKey(res.generated_reader_key);
      }
      if (res.reader_warning) {
        setReaderWarning(res.reader_warning);
      }
      // Handle success - maybe redirect or reset
      window.location.reload(); // Quick way to refresh Data Sources table
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const steps = [
    { id: 1, label: t('importAdaptersPanel.step.adapter') },
    { id: 2, label: t('importAdaptersPanel.step.file') },
    { id: 3, label: t('importAdaptersPanel.step.validate') },
    { id: 4, label: t('importAdaptersPanel.step.stage') },
    { id: 5, label: t('importAdaptersPanel.step.approve') },
  ];

  return (
    <div className={`w-full bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark rounded-2xl overflow-hidden shadow-lg mb-8 transition-all duration-300 ${isExpanded ? '' : 'hover:border-primary/30 cursor-pointer'}`}
         onClick={() => !isExpanded && setIsExpanded(true)}>
      {/* Header */}
      <div className={`px-6 py-4 flex items-center justify-between ${isExpanded ? 'border-b border-slate-100 dark:border-slate-800/50' : ''}`}>
        <div className="flex items-center gap-3">
          <div className={`w-8 h-8 rounded-lg flex items-center justify-center transition-colors ${isExpanded ? 'bg-primary/10 text-primary' : 'bg-slate-100 dark:bg-slate-800 text-slate-400'}`}>
            <span className="material-symbols-outlined !text-[18px] filled-icon">upload_file</span>
          </div>
          <div>
            <h1 className="text-[15px] font-bold text-slate-900 dark:text-white leading-tight">{t('importAdaptersPanel.addDataSource')}</h1>
            <p className="text-[11px] text-slate-500 font-mono tracking-tight mt-0.5">
              {!isExpanded ? t('importAdaptersPanel.clickToExpand') : step === 1 ? t('importAdaptersPanel.chooseAdapterType') : t('importAdaptersPanel.fiveStepImport')}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isExpanded && (
            <button 
              onClick={(e) => {
                e.stopPropagation();
                setStep(1);
                setIsExpanded(false);
              }} 
              className="p-1.5 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 transition-all"
              title={t('importAdaptersPanel.closeWizard')}
            >
              <span className="material-symbols-outlined !text-[20px]">close</span>
            </button>
          )}
          {!isExpanded && (
            <span className="material-symbols-outlined text-slate-300 !text-[20px]">expand_more</span>
          )}
        </div>
      </div>

      {isExpanded && (
        <>
          {/* Progress Stepper */}
          <div className="px-8 py-5 bg-slate-50/30 dark:bg-slate-900/10 border-b border-slate-100 dark:border-slate-800/50">
            <div className="flex items-center justify-between max-w-3xl mx-auto relative px-2">
              {/* Connector Line */}
              <div className="absolute top-1/2 left-0 w-full h-0.5 bg-slate-200 dark:bg-slate-800 -translate-y-1/2 z-0"></div>
              <div 
                className="absolute top-1/2 left-0 h-0.5 bg-primary -translate-y-1/2 z-0 transition-all duration-500" 
                style={{ width: `${(step - 1) * 25}%` }}
              />

              {steps.map((s) => {
                const isCompleted = step > s.id;
                const isActive = step === s.id;
                return (
                  <div key={s.id} className="relative z-10 flex flex-col items-center gap-1.5">
                    <div className={`w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-bold transition-all duration-300 ${
                      isCompleted 
                        ? 'bg-emerald-500 text-white shadow-lg shadow-emerald-500/20' 
                        : isActive 
                        ? 'bg-primary text-white shadow-lg shadow-primary/20 scale-105' 
                        : 'bg-white border-2 border-slate-200 text-slate-400 dark:bg-card-dark dark:border-slate-800'
                    }`}>
                      {isCompleted ? <span className="material-symbols-outlined !text-[14px]">check</span> : s.id}
                    </div>
                    <span className={`text-[9px] font-bold uppercase tracking-widest ${
                      isActive ? 'text-slate-900 dark:text-white' : 'text-slate-400'
                    }`}>{s.label}</span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Step Content */}
          <div className="px-8">
            <div className="max-w-4xl mx-auto">
              {step === 1 && (
                <StepAdapter 
                  selectedType={importType} 
                  onSelect={setImportType} 
                />
              )}
              {step === 2 && (
                <StepFile
                  file={file}
                  onFileSelect={setFile}
                  importType={importType}
                  headerRow={headerRow}
                  onHeaderRowChange={setHeaderRow}
                />
              )}
              {step === 3 && (
                <>
                  <StepValidate
                    sourceColumns={sourceColumns}
                    mapping={mapping}
                    onMappingChange={setMapping}
                    errors={liveMappingErrors}
                    warnings={warnings}
                    sampleData={sampleData}
                    importType={importType}
                    onReAutoMap={handleReAutoMap}
                  />
                  {stageError && (
                    <div className="mb-4 p-3 rounded-xl border border-red-200 bg-red-50 dark:border-red-900/30 dark:bg-red-900/10 flex items-start gap-2">
                      <span className="material-symbols-outlined filled-icon !text-[16px] text-red-500 shrink-0 mt-0.5">error</span>
                      <p className="text-[12px] text-red-700 dark:text-red-400 font-medium">{stageError}</p>
                    </div>
                  )}
                </>
              )}
              {step === 4 && (
                <StepStage 
                  sourceSystem={sourceSystem}
                  setSourceSystem={setSourceSystem}
                  displayName={displayName}
                  setDisplayName={setDisplayName}
                  prefixes={prefixes}
                  setPrefixes={setPrefixes}
                  baseCurrency={baseCurrency}
                  setBaseCurrency={setBaseCurrency}
                  autoSync={autoSync}
                  setAutoSync={setAutoSync}
                />
              )}
              {step === 5 && (
                <StepApprove
                  importType={importType}
                  file={file}
                  mapping={mapping}
                  errors={liveMappingErrors}
                  warnings={warnings}
                  sourceSystem={sourceSystem}
                  displayName={displayName}
                  prefixes={prefixes}
                  autoSync={autoSync}
                  baseCurrency={baseCurrency}
                  onEdit={(s) => setStep(s as any)}
                  stagedCount={stagedCount}
                  stagedRows={stagedRows}
                  generateReader={generateReader}
                  onGenerateReaderChange={setGenerateReader}
                  generatedReaderKey={generatedReaderKey}
                  readerWarning={readerWarning}
                />
              )}
            </div>
          </div>

          {/* Footer Actions */}
          <div className="px-8 py-4 bg-slate-50/50 dark:bg-slate-900/30 border-t border-slate-100 dark:border-slate-800/50 flex items-center justify-between">
            <div className="flex items-center gap-3 text-[10px] font-bold text-slate-400 uppercase tracking-widest">
              <span>{t('importAdaptersPanel.stepOf5', { step })}</span>
              <span className="text-slate-200">|</span>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setStep(1);
                  setIsExpanded(false);
                }}
                className="hover:text-slate-600 dark:hover:text-slate-200 transition-colors"
              >
                {t('importAdaptersPanel.cancel')}
              </button>
            </div>

            <div className="flex items-center gap-2.5">
              {step > 1 && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setStep((step - 1) as Step);
                  }}
                  className="flex items-center gap-1.5 px-5 py-2 rounded-xl border border-slate-200 bg-white text-[12px] font-bold text-slate-700 hover:bg-slate-50 dark:border-slate-800 dark:bg-card-dark dark:text-slate-300 dark:hover:bg-slate-900 transition-all shadow-sm"
                >
                  <span className="material-symbols-outlined !text-[16px]">arrow_back</span>
                  {t('importAdaptersPanel.back')}
                </button>
              )}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  handleNext();
                }}
                disabled={!canContinue || loading}
                className={`flex items-center gap-1.5 px-6 py-2 rounded-xl text-[12px] font-bold text-white transition-all ${
                  canContinue && !loading 
                    ? 'bg-primary hover:bg-primary-hover shadow-md shadow-primary/10' 
                    : 'bg-slate-200 dark:bg-slate-800 text-slate-400 cursor-not-allowed'
                }`}
              >
                {loading && <span className="material-symbols-outlined !text-[16px] animate-spin">progress_activity</span>}
                {step === 5 ? t('importAdaptersPanel.approveAndImport') : t('importAdaptersPanel.continue')}
                {step < 5 && !loading && <span className="material-symbols-outlined !text-[16px]">arrow_forward</span>}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
};
