"""Facebook Local Auto-Poster.

Phase 1 covers only the Chrome debug profile and the CDP connection to it.
See README.md for the full roadmap.
"""

__version__ = "0.1.0"

import logging as _logging

# Silent until `applog.start()` (only the real app calls it) gives the log a
# file. Without this, a warning logged in a test would be printed.
_logging.getLogger(__name__).addHandler(_logging.NullHandler())
