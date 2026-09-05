// Canonical app version for the frontend.
//
// SINGLE SOURCE OF TRUTH: the value below MUST equal the repo-root `VERSION`
// file. The drift check in `scripts/verify.sh` fails the build if they diverge.
// Bump both together on release. Do NOT hard-code the version string anywhere
// else in the frontend — import APP_VERSION_DISPLAY instead.
export const APP_VERSION = '0.1.0';
export const APP_VERSION_DISPLAY = `V${APP_VERSION}`;
