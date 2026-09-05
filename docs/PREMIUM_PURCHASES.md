# Premium implementation and Sandbox runbook

## Module ownership

`stayzy/Features/Premium/` owns Foundation-only domain models and protocols,
StoreKit and backend adapters, signed access verification/caching, and paywall
presentation. AppEnvironment injects the account UUID and entitlement callback.
Voice playback and downloads consume PremiumCapabilities. The account service
owns authentication only. FastAPI's `app/billing/` owns Apple verification,
ledger operations, and entitlement aggregation; HTTP routes delegate to it.
Existing services modules re-export billing implementations for compatibility.

Product IDs:
- com.vistasolutions.stayzy.premium.monthly (one-month auto-renewable)
- com.vistasolutions.stayzy.premium.lifetime (non-consumable)

App Store Connect app 6808848074 was verified to use com.vistasolutions.stayzy,
team 7ATG4J8L4X. Lifetime record 6808852620 and Monthly record 6808854889
were created with English localization. Monthly is in Premium group 22360302.
Draft records are not ready for Sandbox until their remaining metadata,
availability, and user-approved prices are configured. No review submission or
Paid Applications agreement acceptance was performed by this change.

## Fast local test

Choose **Stayzy Local Purchases** in Xcode and run on Simulator. It injects a
local test account and a local backend that signs entitlements with an ephemeral
P-256 key. Its keychain namespace is isolated from real credentials. StoreKit
still verifies the local transactions. The fake backend never sends them to
Railway. Normal builds reject Xcode-environment transactions.

The .storekit prices ($2.99 monthly/$29.99 lifetime) are simulation fixtures only;
they do not set App Store pricing. Use Xcode's StoreKit transaction manager to
simulate approval, expiration, and refunds. LocalPremiumBackend is compiled only
in DEBUG, and the local configuration is excluded from Release resources.

Run PremiumControllerTests and StoreKitLocalTests. The latter purchases Lifetime
through StoreKit's local test session and verifies the resulting signed access.

## Sandbox testing on the existing Railway service

The **Stayzy Sandbox** scheme now uses the existing hosted API:
`https://stayzyapi-production.up.railway.app`, with email links at
`https://links.stayzyapp.com`. This is a temporary pre-launch testing setup.
The service name does not determine Apple's payment environment. Keep strict
Sandbox verification; do not bypass signatures or accept Xcode test transactions.

Set these overrides on that Railway service, keeping its existing database,
bucket, SendGrid, and signing credentials:

```dotenv
STAYZY_ENVIRONMENT=staging
STAYZY_APPLE_ENVIRONMENT=Sandbox
STAYZY_APPLE_TEAM_ID=7ATG4J8L4X
STAYZY_APPLE_BUNDLE_ID=com.vistasolutions.stayzy
STAYZY_APPLE_APP_ID=6808848074
STAYZY_MONTHLY_PRODUCT_ID=com.vistasolutions.stayzy.premium.monthly
STAYZY_LIFETIME_PRODUCT_ID=com.vistasolutions.stayzy.premium.lifetime
STAYZY_PUBLIC_APP_URL=https://links.stayzyapp.com
STAYZY_ALLOWED_HOSTS=localhost,127.0.0.1,stayzyapi-production.up.railway.app,links.stayzyapp.com
```

Merge any other required domains into allowed hosts. Provide
STAYZY_APPLE_API_KEY_ID, STAYZY_APPLE_API_ISSUER_ID, and
STAYZY_APPLE_API_PRIVATE_KEY from an App Store Server API key, plus Apple root
certificate paths in STAYZY_APPLE_ROOT_CERTIFICATE_PATHS. The deployed environment
requires these credentials and valid signing configuration.

Run `alembic upgrade head` as the Railway pre-deploy command before deploying the
billing changes. Do not reset the database or reseed the existing voice catalog.
The database will retain Sandbox purchase records and test account data.

Set App Store Connect's **Sandbox Notifications V2** URL to:
`https://stayzyapi-production.up.railway.app/v1/webhooks/app-store`.
Do not set the Production notification URL to a Sandbox-configured verifier.

In the iOS Staging build settings, set STAYZY_ENTITLEMENT_PUBLIC_KEY to the public
key corresponding to this service's STAYZY_JWT_PRIVATE_KEY. The existing signing
key must match; a different test key will fail entitlement verification. Never
include private keys in the app. Verify `/health/ready` and sign-in before buying.

These source changes do not update Railway environment variables or deploy the
service automatically. Do not switch a service serving real paying customers to
Sandbox. Before real sales, restore Production verification and move Sandbox
testing to a separate service/database with its own signing keys and domain.

## Build configuration

**Stayzy Sandbox** uses Staging configuration and bundles the existing Railway API URL,
so launching from the Home Screen keeps the staging service. It has no StoreKit
configuration selected and does not compile DEBUG purchase fakes. Config/Staging.entitlements uses links.stayzyapp.com. Normal stayzy scheme also has no StoreKit file selected.

Set STAYZY_ENTITLEMENT_PUBLIC_KEY to the hosted service’s corresponding public key (single-line
base64 DER works) in the app target build settings or a private xcconfig passed
to xcodebuild. Set STAYZY_PRIVACY_POLICY_URL to your published HTTPS policy.
Config/Stayzy-Info.plist embeds these values. Missing/invalid signing keys deny
Premium rather than trusting a JSON flag. Do not release a paywall without a
working privacy policy and Apple product metadata. Production uses Release and
a separate production public key; test fakes are unavailable there.

## No-charge end-to-end testing

1. Finish Apple product metadata and approved pricing. Monthly belongs in the
   Premium subscription group with a one-month duration; Family Sharing/trials
   remain off. Account holder completes any required business agreements.
2. Create a Sandbox Apple Account under Users and Access → Sandbox using an email
   not already registered as an Apple Account. The user enters credentials.
3. Run the development-signed Sandbox scheme on the iPhone, then use Settings →
   Developer → Sandbox Apple Account. Stayzy's magic-link login is a separate
   account from Apple's payment test identity.
4. Confirm the payment sheet says Sandbox. Buy Monthly/Lifetime, verify the
   entitlement, download a hosted voice pack, select it, and start a session.
5. Test pending/cancelled payments, network loss after payment, retry/relaunch,
   restore, account switching, renewal, expiration, grace, and refunds. Use
   separate tester accounts or clear Sandbox purchase history between cases.
6. TestFlight purchases also use Sandbox. Keep the staging configuration in the
   beta build; switching to production is an explicit later release step.

## Diagnostics and limits

Backend request IDs link iOS errors to Railway request diagnostics. Purchase
failures stay unfinished for retry; success finishes only after server commit and
valid signed entitlement acceptance. Duplicate callbacks are serialized. Online
refunds override older periods in the same purchase lineage. Apple billing grace
is stored separately from seven-day local playback grace.

Apple status reconciliation requires outbound access and Server API credentials;
network errors preserve cached access only until its signed deadline. PostgreSQL
advisory locks are used in production/staging; SQLite unit tests do not validate
multi-worker PostgreSQL concurrency. Validate that scenario in staging.

No actual staging service, DNS, test account, production deployment, or physical
Sandbox purchase is created merely by building the app. Those need the external
setup above. Preserve existing on-device history; no profile migration is added.

## Verification recorded for this change

- iOS: 110 unit tests passed; targeted purchase tests passed again after duplicate
  transaction suppression was added.
- Local StoreKit: both product definitions loaded, Lifetime purchased and finished
  only after signed test entitlement acceptance.
- UI: iPhone and iPad paywall tests passed; exported dark iPhone and light iPad
  screenshots were inspected. UI tests explicitly initialize SKTestSession so a
  fresh simulator does not depend on a previous test's StoreKit configuration.
- Backend: 42 tests passed, including Apple status adapter reconciliation,
  restored-account ownership, billing grace, and refund replay protection.
- Migration: 0001_initial -> 0002_billing_freshness passed in an isolated SQLite
  database. Existing configured/production databases were not migrated.
- Device compilation: Staging and Release builds succeeded without signing.

Pending external evidence: development-signed iPhone Sandbox purchase through
the hosted Sandbox-configured service, PostgreSQL multi-worker concurrency, bucket download/playback end to end,
and TestFlight. Large Dynamic Type and VoiceOver interactions still need manual
acceptance testing. Set a real privacy policy URL before releasing the paywall.

Apple references: [StoreKit automated testing](https://developer.apple.com/documentation/storekittest/sktestsession),
[Sandbox accounts](https://developer.apple.com/help/app-store-connect/test-in-app-purchases/create-a-sandbox-apple-account).
