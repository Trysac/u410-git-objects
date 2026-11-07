# git-objects

[![PyPI version](https://img.shields.io/pypi/v/trysac-git-objects)](https://pypi.org/project/trysac-git-objects/)
[![License: MIT](https://img.shields.io/pypi/l/trysac-git-objects)](LICENSE)
[![CI](https://github.com/Trysac/u410-git-objects/actions/workflows/ci.yml/badge.svg)](https://github.com/Trysac/u410-git-objects/actions/workflows/ci.yml)

Pure Python zero-dependency reader and writer for Git object stores, packed refs, and packfiles v2.

## Problem

Describe in two or three sentences the concrete problem this utility solves, who hits it, and what
they do today without it. Replace this paragraph when the first real feature lands.

## Install

```sh
pip install trysac-git-objects
# or
uv add trysac-git-objects
```

Requires Python 3.10 or newer. No runtime dependencies.

## Usage

```python
import git_objects

# normalize() trims surrounding whitespace and lower-cases the text.
print(git_objects.normalize("  Hello World "))  # -> "hello world"

# Blank input is rejected explicitly instead of returning an empty string.
try:
    git_objects.normalize("   ")
except ValueError as exc:
    print(f"rejected: {exc}")
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) and [decisions](docs/decisions/)
- [Testing](docs/TESTING.md) and [security](docs/SECURITY.md)
- [Project record](docs/PROJECT.md) and [stack](docs/STACK.md)
- [Changelog](CHANGELOG.md) and [contributing guide](CONTRIBUTING.md)

## License

[MIT](LICENSE) (c) 2026 Miguel Gomez
