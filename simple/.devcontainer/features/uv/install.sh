#!/usr/bin/env bash
#-------------------------------------------------------------------------------------------------------------
# Local vendored equivalent of ghcr.io/astral-sh/devcontainer-features/uv:1.
# The astral-sh/devcontainer-features repo was removed, so the feature is installed from the
# official uv standalone GitHub release instead of the (now unreachable from this environment)
# ghcr.io feature artifact.
#-------------------------------------------------------------------------------------------------------------
set -e

UV_VERSION="${VERSION:-latest}"

ARCH="$(uname -m)"
case "${ARCH}" in
    x86_64) UV_ARCH="x86_64" ;;
    aarch64|arm64) UV_ARCH="aarch64" ;;
    *) echo "(!) Unsupported architecture: ${ARCH}" >&2; exit 1 ;;
esac

TARBALL="uv-${UV_ARCH}-unknown-linux-gnu.tar.gz"
if [ "${UV_VERSION}" = "latest" ]; then
    URL="https://github.com/astral-sh/uv/releases/latest/download/${TARBALL}"
else
    URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${TARBALL}"
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "(!) Script must be run as root." >&2
    exit 1
fi

TMP="$(mktemp -d)"
trap "rm -rf ${TMP}" EXIT

echo "Downloading ${URL}"
curl -fsSL "${URL}" -o "${TMP}/${TARBALL}"
tar -xzf "${TMP}/${TARBALL}" -C "${TMP}"

install -m 0755 "${TMP}/uv-${UV_ARCH}-unknown-linux-gnu/uv" /usr/local/bin/uv
install -m 0755 "${TMP}/uv-${UV_ARCH}-unknown-linux-gnu/uvx" /usr/local/bin/uvx

echo "uv installed: $(/usr/local/bin/uv --version)"
