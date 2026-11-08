# Testing

## Test Tiers

1. **Unit and edge case tests** (`tests/test_store.py`): Exercises object hashing, pack generation, delta reconstruction, reference validation, tree writing, diffing, and CLI commands.
2. **Conformance tests** (`tests/test_conformance.py`): Validated against real `git verify-pack` and `git fsck --full`.