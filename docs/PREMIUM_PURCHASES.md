# Trial, lifetime, and staging testing

Stayzy has exactly two supported App Store products. Both are non-consumables:

| Access | Product ID | Behavior |
| --- | --- | --- |
| 7-day Trial | `com.vistasolutions.stayzy.trial.seven_days` | Free; expires seven days after Apple's original purchase date; never renews or charges automatically. |
| Lifetime | `com.vistasolutions.stayzy.premium.lifetime` | One-time unlock of all current features; remains active unless refunded or revoked. |

Monthly products, renewal handling, subscription management, and subscription grace are removed. Existing database rows are retained for audit, but monthly transactions no longer grant access. Do not deploy this removal to customers with paid monthly entitlements without a migration plan.

## Run on your iPhone

Use the **stayzy** scheme and select your device. Its Run action uses **Staging**, the hosted API, and real Apple Sandbox purchases. Local StoreKit configuration and `STAYZY_LOCAL_PURCHASES` are absent. The **Stayzy Sandbox** scheme uses the same setup and also archives Staging for TestFlight. The normal **stayzy** Archive action still uses Release.

The **Stayzy Local Purchases** scheme is only for offline StoreKit tests. Its fake backend cannot authorize hosted voice downloads.

The current Staging API URL is `https://stayzyapi-production.up.railway.app`. The word `production` in that generated hostname does not select the Apple verification environment. For the current prelaunch setup, configure that service as follows; keep all existing database, bucket, email, and signing credentials:

```dotenv
STAYZY_ENVIRONMENT=staging
STAYZY_APPLE_ENVIRONMENT=Sandbox
STAYZY_APPLE_TEAM_ID=7ATG4J8L4X
STAYZY_APPLE_BUNDLE_ID=com.vistasolutions.stayzy
STAYZY_APPLE_APP_ID=6808848074
STAYZY_TRIAL_PRODUCT_ID=com.vistasolutions.stayzy.trial.seven_days
STAYZY_LIFETIME_PRODUCT_ID=com.vistasolutions.stayzy.premium.lifetime
STAYZY_PUBLIC_APP_URL=https://links.stayzyapp.com
STAYZY_ALLOWED_HOSTS=localhost,127.0.0.1,stayzyapi-production.up.railway.app,links.stayzyapp.com
```

Remove the unused `STAYZY_MONTHLY_PRODUCT_ID` override. Apple Server API credentials and root certificates are still required. The Staging build's `STAYZY_ENTITLEMENT_PUBLIC_KEY` must match the service's signing public key. Keep signature verification enabled.

Railway deployment config runs `alembic upgrade head` before deployment and uses `/health/ready` to check database and bucket availability. Deploy the updated backend source as well as the variables. Local changes do not automatically update Railway.

If production must remain live, provision a separate Railway service and database instead. Update Staging's API URL and signing public key in Xcode build settings, including the Sandbox scheme's API URL. Route staging login links to that service. Do not point a Sandbox build at a Production verifier.

## Apple setup

In App Store Connect, configure the two product IDs above, including pricing, availability, localization, and required metadata. The trial must be a free non-consumable named **7-day Trial**, rather than a subscription introductory offer. Configure **Sandbox Notifications V2** to `https://stayzyapi-production.up.railway.app/v1/webhooks/app-store` for the current shared prelaunch API. Use a Sandbox Apple Account on the device.

Apple documentation: [Sandbox testing](https://developer.apple.com/help/app-store-connect/test-in-app-purchases/overview-of-testing-in-sandbox), [App Review Guidelines, 3.1.1](https://developer.apple.com/app-store/review/guidelines/).

## End-to-end acceptance

1. Run **stayzy** on the iPhone. Confirm both products load and the Apple payment sheet identifies Sandbox.
2. Start the free trial without a Stayzy login. A focus session should start; the displayed deadline should be seven days after original activation.
3. Sign in through the email link. Restore purchases and download an additional voice. Downloads require a signed server entitlement.
4. Restore again or reinstall: the trial deadline must stay unchanged. An expired trial cannot start a new session or authorize downloads; an already running session may finish.
5. While signed in, buy lifetime in Sandbox. Verify the server reports lifetime, download a voice, and restore the purchase.
6. Test refunds/revocations and another Stayzy account: revoked purchases must not grant access, and a purchase linked to one account must not be stolen by another.

Automated tests cover purchase retries, trial deadlines, refunds, account binding, signed entitlements, and download authorization with fake Apple/email/storage adapters. Passing them does not prove the deployed credentials, real Apple products, email delivery, or device purchases work; the steps above remain necessary.
