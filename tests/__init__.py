
import os as _os

_os.environ.setdefault("NHL_ENGINE_TICKET_POLICY", "legacy")      # machinery tests run on the earlier rules; tests of the proposed policy select it explicitly (see tests/test_ticket_policy.py)
_os.environ.setdefault("NHL_ENGINE_ALLOW_SINGLES", "1")        # older accounting tests record single bets; tests of the exclusion turn this off explicitly
