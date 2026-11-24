#!/bin/sh

set -eu
cd "$(dirname "$0")/.."

if [ "$(uname -s)" != FreeBSD ]; then
    printf '%s\n' 'Build the package on FreeBSD 15.1 or OPNsense 26.7.' >&2
    exit 1
fi

build_dir=$(mktemp -d)
trap 'rm -rf "$build_dir"' EXIT

case "${1:-}" in
    --fetch-deps)
        # FreeBSD ports can use a different default Python flavor.
        # Use the official OPNsense repository and the pinned core fingerprint.
        repos="$build_dir/repos"
        fingerprints="$build_dir/fingerprints"
        mkdir -p "$repos" "$fingerprints/trusted" "$fingerprints/revoked"
        core=228ab68d54513046f79bc36d7f583936922d1951
        signer=pkg.opnsense.org.20260608
        fetch -qo "$fingerprints/trusted/$signer" \
            "https://raw.githubusercontent.com/opnsense/core/$core/src/etc/pkg/fingerprints/OPNsense/trusted/$signer"
        cat > "$repos/OPNsense.conf" <<EOF_REPO
OPNsense: {
  url: "https://pkg.opnsense.org/FreeBSD:15:amd64/26.7/latest",
  signature_type: "fingerprints",
  fingerprints: "$fingerprints",
  enabled: yes
}
EOF_REPO
        pkg -o REPOS_DIR="$repos" install -y -r OPNsense python313 py313-dnspython py313-Jinja2
        ;;
    '') ;;
    *) printf '%s\n' 'Usage: sh scripts/build-package.sh [--fetch-deps]' >&2; exit 64 ;;
esac

git clone --depth 1 --branch 26.7.9 https://github.com/opnsense/plugins.git "$build_dir/plugins"
test "$(git -C "$build_dir/plugins" rev-parse HEAD)" = a01a2e8cef738b3762830a7bbfc6e857384328f1

plugin="$build_dir/plugins/dns/unbound-catalog"
mkdir -p "$plugin" dist
cp -R Makefile pkg-descr src +PRE_DEINSTALL.pre +POST_DEINSTALL.post +POST_INSTALL.post "$plugin/"
find "$plugin/src" -name __pycache__ -type d -exec rm -rf {} +
chmod -R a+rX "$plugin/src"
make -C "$plugin" PLUGIN_ABI=26.7 PLUGIN_PHP=85 \
    PLUGIN_HASH="$(git rev-parse --short=9 HEAD)" package

for source in "$plugin"/work/pkg/os-unbound-catalog-*.pkg; do
    package=$(basename "$source")
    cp "$source" "dist/$package"
    (cd dist && pkg info -F "$package" && sha256 -r "$package" > "$package.sha256")
done
