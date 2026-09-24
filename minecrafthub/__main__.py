"""Entry point for ``python3 -m bol`` and the packaged zipapp (.pyz)."""
# SPDX-License-Identifier: MIT
import sys

from .cli import main

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        sys.exit(130)
