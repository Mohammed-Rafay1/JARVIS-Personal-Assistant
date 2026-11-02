"""Tools Jarvis can use. Importing this package registers them all."""
from types import SimpleNamespace

# filled in at startup: cfg, apps (AppIndex), gmail (Gmail | None), contacts
CTX = SimpleNamespace(cfg={}, apps=None, gmail=None, contacts={})


def load_all() -> None:
    from . import apps, browser, claude_code, files, gmail, media, spotify, system, whatsapp, youtube  # noqa: F401
