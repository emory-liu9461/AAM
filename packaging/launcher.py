"""Entry point used by PyInstaller to build the desktop executable."""

import sys

from am_accounting.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
