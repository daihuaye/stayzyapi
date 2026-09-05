#!/usr/bin/env bash
# Generate Stayzy's P-256 signing keys. Never writes keys into the repository.
set -euo pipefail
umask 077

if ! command -v openssl >/dev/null 2>&1; then
  printf 'Error: OpenSSL is required. Install it before running this script.\n' >&2
  exit 1
fi

# A fresh private directory prevents overwriting an existing signing key pair.
signing_dir="$(mktemp -d "${HOME}/stayzy-signing.XXXXXX")"

openssl genpkey \
  -algorithm EC \
  -pkeyopt ec_paramgen_curve:prime256v1 \
  -out "$signing_dir/private.pem"

openssl pkey \
  -in "$signing_dir/private.pem" \
  -pubout \
  -out "$signing_dir/public.pem"

openssl pkey \
  -pubin \
  -in "$signing_dir/public.pem" \
  -outform DER |
  openssl base64 -A > "$signing_dir/ios-public-key.txt"

printf '\nKeys saved in: %s\n\n' "$signing_dir"
printf 'Railway STAYZY_JWT_PRIVATE_KEY:     %s/private.pem\n' "$signing_dir"
printf 'Railway STAYZY_JWT_PUBLIC_KEY:      %s/public.pem\n' "$signing_dir"
printf 'Xcode STAYZY_ENTITLEMENT_PUBLIC_KEY: %s/ios-public-key.txt\n' "$signing_dir"
printf '\nPaste complete PEM contents into Railway, including header and footer lines.\n'
printf 'Keep private.pem private. Never put it in Xcode, Git, or chat.\n'
printf 'This script does not update Railway or Xcode.\n'
printf 'Run it once for setup; each additional run creates a different key pair.\n'
