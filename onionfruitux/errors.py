"""Errors a person can act on."""


class OnionError(Exception):
    """A problem OnionFruitux can explain in one message."""


class DirectUnreachable(OnionError):
    """The computer still cannot reach the internet after settings were restored."""


# Prefix on a switch-job error so the window can offer the reset button.
RESET_MARKER = "[[reset-network]]"
