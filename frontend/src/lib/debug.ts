/**
 * Development-only console wrappers.
 *
 * Silent in production to keep user consoles clean; logs full info in dev.
 * Use these instead of raw `console.warn` / `console.error` for fetch catches
 * and non-critical diagnostic output.
 */

const IS_DEV = process.env.NODE_ENV === "development";

export const debugWarn = (...args: unknown[]): void => {
  if (IS_DEV) console.warn(...args);
};

export const debugError = (...args: unknown[]): void => {
  if (IS_DEV) console.error(...args);
};

export const debugLog = (...args: unknown[]): void => {
  if (IS_DEV) console.log(...args);
};
