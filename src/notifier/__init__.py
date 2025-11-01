"""discord-webhook-notifier public package interface."""

from .cli import run_cli
from .gui_tk import launch as launch_gui

__all__ = ["run_cli", "launch_gui"]
