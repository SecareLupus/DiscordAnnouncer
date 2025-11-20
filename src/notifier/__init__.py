"""discord-webhook-notifier public package interface."""

from .cli import run_cli

__all__ = ["run_cli", "launch_gui"]


def launch_gui() -> None:
    """Entry point for the Tk GUI (imported lazily to avoid Tk init on import)."""
    from .gui_tk import launch

    launch()
