"""Translations for messages from core (errors and warnings shown to the user).

Uses the stdlib gettext functions, not the builtins installed by the launcher
nor gi, so core stays importable on the host. The launcher binds the "tarseer"
domain; without that (tests) messages stay in English.
"""

from gettext import gettext as _, ngettext

__all__ = ["_", "ngettext"]
