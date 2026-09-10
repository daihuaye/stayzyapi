# Developer API request viewer

Settings → Developer → API Requests is available only when both conditions hold:

- The app is a local **Debug** build (`DEBUG` compilation condition).
- The current authenticated, active account has `developer_access: true` in the
  backend `GET /v1/me` response.

The backend calculates eligibility from the verified account email, never from
client input or the masked display email. Its exact allowlist is:

- daihua.ye@gmail.com
- raymond2026ye@gmail.com
- anna2012wan@gmail.com
- eli.zhankun.ye@gmail.com
- raymond2015ye@gmail.com
- anna2006wan@gmail.com
- daye@vistasolutionsllc.com
- vistavalueconstruction@gmail.com
- vistasolutions.company@gmail.com

Matching ignores surrounding whitespace and letter case; it does not expand
aliases. Guests, other accounts, local StoreKit mock accounts, and Release builds
cannot enable tracing. Older backends that omit the eligibility field default to
no developer access. Deploy the backend account-response change before testing
with a real allowlisted login. No database migration is needed for this addition.

## Capture behavior

Turn on **Track API requests**, then use the app normally. The list updates live
and shows the most recent 100 requests, newest first. Open a row for its method,
endpoint, start time, status, duration, selected headers, request body, and backend
response body. HTTP failures, transport failures, decoding errors, and retry
attempts are included. Existing request/correlation headers link the trace to
backend logs. Turning on tracing does not force a flight refresh or change its
10-minute throttle.

Capture covers every request through StayzyAPIClient and metadata for voice-pack
archive downloads. Apple-managed StoreKit traffic and AVPlayer's internal media
requests are not intercepted. Binary downloads show their byte count, not content.

Tracking starts off on every launch. Data is memory-only, never uploaded or saved
to logs/files. Clear removes existing entries without stopping recording. Turning
tracking off retains completed entries for inspection but stops capturing pending
responses. Sign-out, account deletion attempts, switching accounts, starting a new
magic-link verification, or losing eligibility clears the trace and turns it off.
Restoring eligibility requires opting in again.

## Redaction and limits

Authentication bodies are omitted. Other JSON bodies redact credential, token,
email, signed-payload, and URL fields recursively; recognizable email addresses,
URLs, bearer values, and JWTs in strings are also redacted. Only a small header
allowlist is retained; authorization and cookie headers are never captured.
Endpoint query strings and URL credentials are removed. Download object paths
are hidden. Non-JSON bodies and bodies larger than 256 KiB are omitted; displayed
JSON is capped at 16,384 characters. Response payloads are diagnostic copies and do
not alter the data used by the app.
