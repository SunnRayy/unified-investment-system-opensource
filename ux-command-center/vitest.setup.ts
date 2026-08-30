import '@testing-library/jest-dom';
import { beforeEach } from 'vitest';

const storageData = new Map<string, string>();

const storageMock: Storage = {
  get length() {
    return storageData.size;
  },
  clear: () => {
    storageData.clear();
  },
  getItem: (key: string) => (storageData.has(key) ? storageData.get(key)! : null),
  key: (index: number) => Array.from(storageData.keys())[index] ?? null,
  removeItem: (key: string) => {
    storageData.delete(key);
  },
  setItem: (key: string, value: string) => {
    storageData.set(key, value);
  },
};

function installStorageMock() {
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: storageMock,
    writable: true,
  });
}

// Install the mock BEFORE i18next loads, hence the dynamic import below rather than a
// hoisted `import i18n from './src/i18n'`. i18next-browser-languagedetector probes
// `window.localStorage` exactly once, at init, and caches the result forever; if the mock
// arrives afterwards the detector has already given up on storage and nothing in the suite
// can exercise the persisted-language path.
installStorageMock();

const { default: i18n } = await import('./src/i18n');

beforeEach(() => {
  installStorageMock();
  window.localStorage.clear();

  // Pin the test locale to English. The suite asserts on ~304 English literals and every
  // EN catalog value is byte-identical to the literal it replaced, so this makes the
  // existing suite a correctness harness for the extraction (ADR-028). It also keeps a
  // test that flips to zh-CN — see tests/i18n-zh-canary.test.tsx — from leaking into the
  // next test, since i18next language is module-global.
  if (i18n.language !== 'en') {
    void i18n.changeLanguage('en');
  }
});

if (typeof window.ResizeObserver === 'undefined') {
  class ResizeObserverMock {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  window.ResizeObserver = ResizeObserverMock as unknown as typeof ResizeObserver;
}
