#!/usr/bin/env bash
set -euo pipefail

# Verification script for conformance tools in CI
command -v git >/dev/null 2>&1 || {
    echo "Git binary required for conformance"
    exit 1
}
echo "Git available: $(git --version)"
