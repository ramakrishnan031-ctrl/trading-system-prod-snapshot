#!/usr/bin/env bash
# VM Security Manager Phase 2 — copy-protection installer (run ON the VM).
#
#   bash deploy/security/install_copy_protection.sh          # install + verify
#   bash deploy/security/install_copy_protection.sh --uninstall
#
# Idempotent. Installs the copy-guard wrappers into /usr/local/bin and loads the
# auditd `copy_attempt` rules. SSH is git-over-ssh / interactive ssh — NEITHER
# is touched here (we only shadow scp/sftp/rsync), so this cannot lock you out.
# Uninstall = remove the symlinks (the real /usr/bin binaries are untouched).
set -euo pipefail

ROOT="/home/ubuntu/systems/trading-system"
GUARD="$ROOT/deploy/security/bin/copy-guard"
REQ="$ROOT/deploy/security/bin/request-copy"
RULES_SRC="$ROOT/deploy/security/trading-security.rules"
RULES_DST="/etc/audit/rules.d/trading-security.rules"
TOOLS=(scp sftp rsync)

uninstall() {
    echo "== Uninstalling copy protection =="
    for t in "${TOOLS[@]}"; do
        if [[ -L "/usr/local/bin/$t" ]]; then
            sudo rm -f "/usr/local/bin/$t"
            echo "  removed /usr/local/bin/$t"
        fi
    done
    [[ -L /usr/local/bin/request-copy ]] && sudo rm -f /usr/local/bin/request-copy && echo "  removed /usr/local/bin/request-copy"
    echo "Wrappers removed. The auditd copy_attempt rules were LEFT in place"
    echo "(harmless, alert-only). To drop them too: edit $RULES_DST and run"
    echo "  sudo augenrules --load"
    echo "Real binaries: $(PATH=/usr/bin:/bin command -v scp) (untouched)."
}

install() {
    echo "== Installing copy protection (Phase 2) =="
    chmod +x "$GUARD" "$REQ"

    # 1) Shadow the copy clients with the guard wrapper.
    for t in "${TOOLS[@]}"; do
        sudo ln -sfn "$GUARD" "/usr/local/bin/$t"
        echo "  /usr/local/bin/$t -> $GUARD"
    done
    # 2) request-copy launcher.
    sudo ln -sfn "$REQ" /usr/local/bin/request-copy
    echo "  /usr/local/bin/request-copy -> $REQ"

    # 3) auditd bypass-detection rules.
    if command -v augenrules >/dev/null 2>&1; then
        sudo cp "$RULES_SRC" "$RULES_DST"
        sudo augenrules --load
        echo "  auditd rules loaded from $RULES_SRC"
    else
        echo "  WARN: augenrules not found — is auditd installed? (Phase 1 installed it.)"
    fi

    echo
    echo "== Verify =="
    hash -r 2>/dev/null || true
    echo "  which scp        : $(command -v scp)         (expect /usr/local/bin/scp)"
    echo "  real scp         : $(PATH=/usr/bin:/bin command -v scp)"
    echo "  copy_attempt rule: $(sudo auditctl -l 2>/dev/null | grep -c copy_attempt) entr(ies)"
    echo "  gate status      :"
    PYTHONPATH="$ROOT" /home/ubuntu/systems/venv/bin/python \
        "$ROOT/scripts/copy_gate.py" --status | sed 's/^/    /'
    echo
    echo "Done. Smoke test:"
    echo "  scp /etc/hostname someone@pc:/tmp/   # should DENY (no token)"
    echo "  request-copy 'pulling a report'      # mint a 15-min token"
    echo "  scp ...                              # now ALLOWED until expiry"
    echo "NB: git push / interactive ssh are unaffected (ssh is not wrapped)."
}

if [[ "${1:-}" == "--uninstall" ]]; then
    uninstall
else
    install
fi
