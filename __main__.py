#!/usr/bin/env python3
"""Allow running MounThor as a module: python3 -m MounThor"""
"""
MounThor - Simple GTK4/libadwaita CIFS mount manager.

Configuration:
    ~/.config/mounthor/mounts.json

Entry point module that imports and runs the application.
"""

from .app import main

if __name__ == "__main__":
    import sys
    sys.exit(main())
