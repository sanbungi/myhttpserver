#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_PATH="${1:-$ROOT_DIR/server.crt}"
KEY_PATH="${2:-$ROOT_DIR/server.key}"
DAYS="${DAYS:-365}"
COMMON_NAME="${COMMON_NAME:-localhost}"
SUBJECT="${SUBJECT:-/C=AU/ST=Some-State/O=Internet Widgits Pty Ltd/CN=${COMMON_NAME}}"
SAN_ENTRIES="${SAN_ENTRIES:-DNS:localhost,DNS:example.com,IP:127.0.0.1}"

if ! command -v openssl >/dev/null 2>&1; then
  echo "[ERROR] openssl command not found" >&2
  exit 1
fi

mkdir -p "$(dirname "$CERT_PATH")" "$(dirname "$KEY_PATH")"

OPENSSL_CONFIG="$(mktemp)"
trap 'rm -f "$OPENSSL_CONFIG"' EXIT

cat >"$OPENSSL_CONFIG" <<EOF
[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = ${COMMON_NAME}

[v3_req]
subjectAltName = ${SAN_ENTRIES}
EOF

openssl req \
  -x509 \
  -nodes \
  -newkey rsa:2048 \
  -sha256 \
  -days "$DAYS" \
  -keyout "$KEY_PATH" \
  -out "$CERT_PATH" \
  -subj "$SUBJECT" \
  -config "$OPENSSL_CONFIG" \
  -extensions v3_req

echo "Generated:"
echo "  cert: $CERT_PATH"
echo "  key : $KEY_PATH"
