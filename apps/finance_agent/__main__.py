"""Package entrypoint — makes `python -m apps.finance_agent` work.

Forwards to apps.finance_agent.main.main(). Both invocation styles
(with or without the trailing `.main`) are equivalent.
"""

from .main import main

raise SystemExit(main())
