#!/usr/bin/env python3
"""Allow running MounThor as a module: python3 -m MounThor"""

from .app import main

if __name__ == "__main__":
    import sys
    sys.exit(main())
