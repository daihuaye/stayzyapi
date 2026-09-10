# Developer API requests

Settings → Developer → API Requests is available in Debug and Staging builds. It starts recording automatically without an email allowlist or customer account. The stayzy Release scheme excludes the viewer and capture hooks; use Stayzy Sandbox for development on a device.

A manual pause survives repeat initialization. Only the most recent 100 requests are kept in memory, cleared when the app exits. Credentials, purchase proofs, access tokens and signed URLs are redacted. Apple-managed StoreKit requests are not intercepted.

Cold-start experiments remain visible immediately; no customer verification is needed to reveal them. The endpoint is public and keeps its existing startup and refresh policy.
