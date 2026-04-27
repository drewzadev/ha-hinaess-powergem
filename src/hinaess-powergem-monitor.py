#!/usr/bin/env python3
"""Shim - delegates to src.main.main() for backward compatibility."""

import os
import sys

# Ensure the project root is on sys.path so `from src import ...` works
# when this script is invoked directly (e.g. python3 src/hinaess-powergem-monitor.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.main import main

if __name__ == "__main__":
    main()
