"""Settings for a live local run: the normal settings with the LLM kill switch turned on for this process only.

Use `scripts/dev.sh --live`. LLM_ENABLED stays False in config/tunables.py, so nothing else is affected.
The budget caps, the circuit breaker and the ledger all still apply.
"""

from config.settings import *  # noqa: F401,F403

LLM_ENABLED = True
