"""What other packages may use from the vault: the checks that ask a service whether a credential works."""

from superaide.identity.vault.check import CheckFailed, check_slack, check_telegram

__all__ = ["CheckFailed", "check_slack", "check_telegram"]
