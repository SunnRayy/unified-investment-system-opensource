import React, { useRef, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';

interface StepFileProps {
  onFileSelect: (file: File) => void;
  file: File | null;
  importType: string;
  headerRow: number;
  onHeaderRowChange: (row: number) => void;
}

export const StepFile: React.FC<StepFileProps> = ({ onFileSelect, file, importType, headerRow, onHeaderRowChange }) => {
  const { t } = useTranslation('system');
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => {
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const droppedFile = e.dataTransfer.files?.[0];
    if (droppedFile) onFileSelect(droppedFile);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0];
    if (selectedFile) onFileSelect(selectedFile);
  };

  return (
    <div className="py-4">
      <h2 className="text-[14px] font-bold text-slate-900 dark:text-white mb-0.5">{t('wizard.stepFile.title')}</h2>
      <p className="text-[12px] text-slate-500 mb-5">{t('wizard.stepFile.subtitle')}</p>

      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
        className={`relative group cursor-pointer border-2 border-dashed rounded-2xl p-8 flex flex-col items-center justify-center transition-all ${
          isDragging
            ? 'border-primary bg-blue-50/50 dark:bg-blue-900/10'
            : file
            ? 'border-emerald-200 bg-emerald-50/30 dark:border-emerald-900/30 dark:bg-emerald-900/10'
            : 'border-slate-200 hover:border-slate-300 bg-white dark:border-slate-800 dark:bg-card-dark'
        }`}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,.xlsx,.xls,.tsv"
          className="hidden"
          onChange={handleFileChange}
        />

        <div className={`w-12 h-12 rounded-xl flex items-center justify-center mb-4 transition-transform group-hover:scale-110 ${
          file ? 'bg-emerald-500 text-white' : 'bg-blue-50 text-primary dark:bg-blue-900/30'
        }`}>
          <span className="material-symbols-outlined !text-[24px]">
            {file ? 'check_circle' : 'cloud_upload'}
          </span>
        </div>

        <div className="text-center">
          {file ? (
            <>
              <p className="text-[13px] font-bold text-slate-900 dark:text-white mb-0.5">{file.name}</p>
              <p className="text-[11px] text-emerald-600 font-medium">{t('wizard.stepFile.readyForProcessing', { size: (file.size / 1024).toFixed(1) })}</p>
            </>
          ) : (
            <>
              <p className="text-[13px] font-bold text-slate-900 dark:text-white mb-0.5">{t('wizard.stepFile.dropFileHere')}</p>
              <p className="text-[12px] text-slate-500">
                <Trans t={t} i18nKey="wizard.stepFile.orBrowse" components={{ strong: <span className="text-primary font-semibold" /> }} />
              </p>
            </>
          )}
        </div>

        <div className="mt-6 flex flex-wrap justify-center gap-2">
          {(['csv', 'xlsx', 'xls', 'tsv'] as const).map(ext => (
            <span key={ext} className="px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-[9px] font-bold text-slate-500 uppercase tracking-tight">
              {t(`wizard.stepFile.extension.${ext}`)}
            </span>
          ))}
          <span className="px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-[9px] font-bold text-slate-500 uppercase tracking-tight">
            {t('wizard.stepFile.maxSize')}
          </span>
        </div>
      </div>

      {file && (
        <div className="mt-3 flex items-start gap-4">
          {/* Adapter type info */}
          <div className="flex-1 flex items-center gap-2 p-2.5 rounded-lg bg-blue-50/50 border border-blue-100 dark:bg-blue-900/10 dark:border-blue-800/50">
            <span className="material-symbols-outlined text-primary !text-[16px]">info</span>
            <p className="text-[11px] text-slate-600 dark:text-slate-400">
              <Trans
                t={t}
                i18nKey="wizard.stepFile.importingAs"
                values={{ importType }}
                components={{ strong: <span className="font-bold text-slate-900 dark:text-white uppercase tracking-tight" /> }}
              />
            </p>
            <button
              onClick={(e) => { e.stopPropagation(); fileInputRef.current?.click(); }}
              className="ml-auto text-[11px] font-bold text-primary hover:underline whitespace-nowrap"
            >
              {t('wizard.stepFile.changeFile')}
            </button>
          </div>

          {/* Header row control */}
          <div
            className="flex items-center gap-2 p-2.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-card-dark"
            onClick={(e) => e.stopPropagation()}
          >
            <span className="material-symbols-outlined text-slate-400 !text-[16px]">table_rows</span>
            <label className="text-[11px] font-bold text-slate-500 uppercase tracking-widest whitespace-nowrap">{t('wizard.stepFile.headerRow')}</label>
            <input
              type="number"
              min={1}
              max={20}
              value={headerRow}
              onChange={(e) => onHeaderRowChange(Math.max(1, parseInt(e.target.value, 10) || 1))}
              className="w-14 bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg px-2 py-1 text-[12px] font-mono text-slate-900 dark:text-white text-center focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none"
            />
            <span className="text-[10px] text-slate-400 whitespace-nowrap">
              {headerRow === 1 ? t('wizard.stepFile.default') : t('wizard.stepFile.skippingRows', { count: headerRow - 1 })}
            </span>
          </div>
        </div>
      )}
    </div>
  );
};
