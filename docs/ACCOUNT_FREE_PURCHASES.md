# Account-free access and Family Sharing

Stayzy customers do not create accounts. The Apple App Store account supplies verified trial, purchased lifetime, or family-shared lifetime transactions. Local profiles and focus history are independent of purchases and remain on the device.

## Application behavior

- Access shows Trial, Lifetime, Shared with your family, or Not unlocked.
- Launch and foreground reconcile StoreKit current entitlements without prompting. Restore Purchases explicitly calls AppStore.sync; purchasing explicitly opens Apple's confirmation.
- Successful empty snapshots clear local access and download credentials. Verification failures preserve the last verified in-memory state. No stored account entitlement grants access on launch.
- Lifetime grants authorize focus and downloaded voices; purchased grants take display precedence over family grants. Revocation of one grant does not remove another valid grant.
- The individual trial ends seven days after original activation, with no automatic charge. Trial expiry may let an already-started session finish; revocation does not.
- Online catalogs and downloads work without customer sign-in. New download URLs still require server verification.
- Debug/Staging diagnostics start enabled and redact purchase proofs, tokens, and signed URLs. Release has no developer access.

## API contract

`POST /v1/iap/apple/transactions` accepts `signed_transaction` without a customer Authorization header. Apple signature verification and server reconciliation validate bundle, environment, product, ownership, recipient transaction identity and revocation.

An active response includes `feature`, `status`, `plan`, `valid_until`, `ownership_type`, `access_token`, and `expires_in: 900`. The token's audience is `stayzy-purchases`; its subject identifies a ledger grant. It cannot authorize administrator operations. Inactive responses do not issue a token.

The app uses the token for `/v1/entitlements`, catalog requests and `/v1/voice-packs/{voice_id}/download`. Entitlement reads do not extend token expiry. After expiration the app exchanges its current StoreKit proof again. Each protected request checks the ledger; revoked grants cannot obtain a download URL. Sensitive responses use `Cache-Control: no-store`.

The ledger keys transactions by environment and transaction ID and stores Apple ownership and optional app transaction ID. Apple family transactions need no appAccountToken. No email, Apple sign-in identity, or family-group membership is inferred from the purchase.

## Backend cutover

This is a coordinated breaking change for older internal builds. No deployment or live data deletion is performed merely by building the app.

1. Back up the production database and verify the backup before retirement.
2. Stage the new API and migration with the current database at revision 0006. Run `python -m app.jobs.revoke_customer_apple_tokens` to inspect the number of pending credentials. If nonzero, provide the legacy Sign in with Apple key settings securely and run the same job with `--execute`. It removes each identity only after Apple's revocation succeeds; it never prints secrets.
3. Run Alembic upgrade through `0007_account_free`. The migration refuses to retire accounts if Apple credentials remain. It preserves purchase records, timestamps, and revocations; removes customer foreign keys and tables; and leaves administrator data intact. A downgrade cannot reconstruct deleted personal data: rollback requires the backup.
4. Deploy the matching API and account-free app together. Customer `/v1/auth/*`, `/v1/me`, `/v1/account`, email-link landing pages and customer webhooks are retired. Existing internal builds must update.
5. Remove legacy Apple sign-in/email-link secrets after successful retirement. Keep administrator reset email credentials, purchase-token signing keys, storage credentials, and App Store server verification credentials.

## Store configuration and pending live verification

Local StoreKit configuration enables sharing for `com.vistasolutions.stayzy.premium.lifetime` only. `com.vistasolutions.stayzy.trial.seven_days` stays a non-shared zero-price non-consumable with an app-enforced seven-day duration.

On September 9, 2026, lifetime **Family Sharing** was enabled and verified in App Store Connect; trial sharing remains disabled. Both products remain in Prepare for Submission and have not been submitted or published. The sharing switch is irreversible. Before release, verify products, bundle ID `com.vistasolutions.stayzy`, App Store server notification URL `/v1/webhooks/app-store`, Apple root certificates, app ID and API key configuration in each environment. Sandbox proofs must never be accepted by a Production verifier. The Xcode-only Local Purchases adapter cannot authorize hosted downloads.

App Store Connect confirms bundle ID `com.vistasolutions.stayzy`. Its sandbox notification URL is `https://stayzyapi-production.up.railway.app/v1/webhooks/app-store`; its production notification URL is unset. Configure production routing after validating the hosted production verifier. Separate-device family tests remain pending. Local backend configuration is development/Sandbox and does not contain App Store verification keys or root-certificate paths; this does not establish the hosted service's configuration.

Validation: the signed Release device build passed, 176 ordinary app tests passed, and all 82 backend tests passed. Two local StoreKit integration tests failed because the simulator StoreKit test service rejected configuration with `notEntitled`; the same failure reproduced on a fresh simulator. Local purchase and physical-device Family Sharing verification therefore remain incomplete. No live backend migration or deployment has been performed.

Physical-device acceptance: purchase once, restore on another family member's Apple account without Stayzy sign-in, download a voice, reinstall and restore, disable sharing, verify revoked access, refund, and verify a separately purchased lifetime grant remains usable. Do not report production Family Sharing as verified until these checks pass.
