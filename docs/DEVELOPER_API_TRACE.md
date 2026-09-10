# Developer API requests

Settings → Developer → API Requests is available only in local Debug builds
(`DEBUG` compilation condition). No login, email allowlist, or backend eligibility
response is required. Staging and Release builds exclude the viewer and capture
hooks. Select the Debug build configuration in Xcode to use it.

Recording starts automatically when the app environment is created, including the
initial public experiment request. The Track API requests toggle pauses and resumes
capture; a manual pause survives repeat initialization. Clear removes captured
requests without pausing recording. No account verification or startup-response
buffer is involved.

Only the most recent 100 requests are kept in memory, cleared when the app exits.
Credentials, purchase proofs, access tokens, email addresses, and signed URLs are
redacted. Apple-managed StoreKit requests are not intercepted. Flight refresh
behavior is unchanged. No backend deployment is needed to enable Developer tools.
