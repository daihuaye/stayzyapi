# Seven-day trial and one-time unlock

## Current behavior

Willow (`voice_willow`) is bundled in `WillowVoice.bundle`, selected for new profiles,
and substituted when loading legacy nil/system-voice selections. Existing `voice_`
identifiers remain intact. The bundle contains all 500 current companion phrases,
validated against catalog `3414ec190c3df9a4`, per-file SHA-256/size and ADTS AAC frame
validation. Its source is the existing Willow pack `2026.09.04.2101` (Coral). Preview
uses `ready.001`; no provider call or download is needed at runtime. The voice screen
discloses AI-generated speech. Downloaded packs and internal Apple speech fallback
continue to work. Willow itself requires no account or premium entitlement.

New purchases offered:

| Product | Type | US price |
| --- | --- | --- |
| `com.vistasolutions.stayzy.trial.seven_days` | Non-consumable, “7-day Trial” | Free |
| `com.vistasolutions.stayzy.premium.lifetime` | Non-consumable, full current-feature unlock | $9.99 |

`com.vistasolutions.stayzy.premium.monthly` remains recognized for existing
transactions, renewals, restoration, grace and refunds, but is not offered to new buyers.
Prices shown in the app come from StoreKit; the local configuration cannot change
App Store Connect prices.

The first new-session attempt presents access if neither trial nor paid access is
available. Guests activate the free trial through Apple, without a Stayzy account.
The immutable original purchase date plus 604800 seconds determines expiry. No
automatic charge follows. StoreKit's verified on-device transactions restore trial
access offline; its latest trial transaction also exposes revocation. Initial
activation/restoration through Apple's sheet may require a network connection.

Hosted downloads and the paid unlock still require Stayzy sign-in. On sign-in the
same trial transaction is submitted to the existing backend transaction endpoint.
Backend signatures give trial downloads/playback only until that original deadline,
without the legacy monthly playback grace. A purchase lineage can be linked to only
one account using the existing ledger ownership rules. Lifetime takes precedence.

New sessions, Start Again and reopening ended sessions require access. An already
in-progress session may finish, including its previously authorized downloaded voice.
History, settings, account management and restore remain accessible after expiry.
Future piano detection, practice time and additional customization subscriptions are
not implemented or included in this purchase's marketing promise.

## Release sequence

1. Deploy compatible `stayzyapi` code first. `trial` is added to the signed entitlement
   plan schema; the existing transaction ledger stores trials, so no database schema
   migration is needed. Configure `STAYZY_TRIAL_PRODUCT_ID` if overriding its default.
   Existing Apple signature/environment checks remain required.
2. In App Store Connect app 6808848074 create the free non-consumable with the exact
   trial ID/name above. Set the existing lifetime product's US base price to $9.99,
   review localized prices and update its name/description to current-feature unlock.
   Stop offering the monthly product to new buyers while preserving current owners.
3. Finish required product localization, availability/review metadata and any existing
   account agreements through the account holder. Confirm the free product appears
   in Sandbox. Do not substitute an auto-renewable introductory offer.
4. Run the hosted Sandbox purchase/download checks, then release the client. No
   production deployment, App Store metadata change or submission is implied by local
   StoreKit tests.

## Verification

- All 500 bundled phrases match the app's phrase IDs and hashes; AAC files validated
  before import. Runtime test checks the built bundle, checksums and preview decoding.
- Backend: full suite, 47 tests passed, including trial linking, expiry without grace,
  account ownership, refund handling and lifetime precedence.
- iOS: final full unit suite, 119 tests passed, including voice migration and camera-recovery access gates;
  local StoreKit tests activate a guest trial, restore its original date and verify
  account-linked signed access.
- UI: three targeted tests passed on iPhone and the same three on iPad, including
  large-text accessibility audits. Exported dark iPhone and light iPad voice/paywall
  screenshots were visually inspected. Full manual VoiceOver and physical-device
  hosted Sandbox acceptance remain external checks.

External release work remains pending until an authenticated App Store Connect /
Railway management session is available. A physical-device hosted Sandbox test is
also required; local StoreKit does not prove hosted Apple configuration.
