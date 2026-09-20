"""Allow ``python -m myagent`` in addition to the ``myagent`` console script."""

from __future__ import annotations

import sys

from myagent.cli import main

if __name__ == "__main__":
    sys.exit(main())
