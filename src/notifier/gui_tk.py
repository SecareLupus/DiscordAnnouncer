from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import requests
from jinja2 import Environment, meta

try:  # Optional dependency for richer previews
    from PIL import Image, ImageTk  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    Image = None
    ImageTk = None

from .env import load_environment

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = Path.home() / ".discord-webhook-notifier"
STATE_FILE = STATE_DIR / "state.json"
TEMPLATE_DIR = PROJECT_ROOT / "templates"
TEMPLATE_METADATA_FILE = TEMPLATE_DIR / "metadata.json"
RESERVED_TEMPLATE_VARS = {
    "loop",
    "cycler",
    "namespace",
    "message",
    "message_prefix",
    "now_iso",
    "has_attachments",
    "BOT_USERNAME",
    "BOT_AVATAR_URL",
    "DEFAULT_COLOR",
    "TWITCH_CHANNEL",
    "embed_footer",
    "embed_field",
    "embed_timestamp",
}
CONFIG_FIELDS: List[Tuple[str, Dict[str, str]]] = [
    ("BOT_USERNAME", {"label": "Bot Username"}),
    ("BOT_AVATAR_URL", {"label": "Bot Avatar URL", "type": "image"}),
    ("TWITCH_CHANNEL", {"label": "Twitch Channel Name"}),
    ("DEFAULT_COLOR", {"label": "Default Color", "type": "color"}),
]


class Tooltip:
    """Lightweight tooltip helper for Tk widgets."""

    def __init__(
        self, widget: tk.Widget, text_provider: Callable[[], str], *, delay: int = 500
    ) -> None:
        self.widget = widget
        self.text_provider = text_provider
        self.delay = delay
        self._after_id: Optional[str] = None
        self._tipwindow: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._schedule_show)
        widget.bind("<Leave>", self._hide)
        widget.bind("<ButtonPress>", self._hide)

    def _schedule_show(self, _event: tk.Event) -> None:
        self._cancel_scheduled()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel_scheduled(self) -> None:
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self) -> None:
        self._after_id = None
        text = self.text_provider()
        if not text:
            return
        if self._tipwindow is not None:
            self._tipwindow.destroy()
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.wm_geometry(f"+{x}+{y}")
        label = ttk.Label(
            window,
            text=text,
            background="#ffffe1",
            relief="solid",
            borderwidth=1,
            padding=(6, 3),
        )
        label.pack()
        self._tipwindow = window

    def _hide(self, _event: Optional[tk.Event] = None) -> None:
        self._cancel_scheduled()
        if self._tipwindow is not None:
            self._tipwindow.destroy()
            self._tipwindow = None


@dataclass
class GuiState:
    webhook_history: List[Dict[str, str]] = field(default_factory=list)
    last_webhook: str = ""
    last_webhook_label: str = ""
    template_path: str = ""
    message: str = ""
    everyone: bool = False
    variables: Dict[str, str] = field(default_factory=dict)
    config: Dict[str, str] = field(default_factory=dict)
    attachments: List[str] = field(default_factory=list)

    @classmethod
    def load(cls) -> "GuiState":
        if not STATE_FILE.exists():
            return cls()
        try:
            data = json.loads(STATE_FILE.read_text())
            raw_history = data.get("webhook_history", [])
            history: List[Dict[str, str]] = []
            for item in raw_history:
                if isinstance(item, dict):
                    url = item.get("url") or item.get("value") or ""
                    label = item.get("label") or item.get("name") or url
                elif isinstance(item, str):
                    url = item
                    label = item
                else:
                    continue
                if not url:
                    continue
                history.append({"label": label, "url": url})
            config_data = data.get("config", {})
            if not isinstance(config_data, dict):
                config_data = {}
            variables_data = data.get("variables", {})
            if not isinstance(variables_data, dict):
                variables_data = {}
            return cls(
                webhook_history=history,
                last_webhook=data.get("last_webhook", ""),
                last_webhook_label=data.get("last_webhook_label", ""),
                template_path=data.get("template_path", ""),
                message=data.get("message", ""),
                everyone=data.get("everyone", False),
                variables=variables_data,
                config=config_data,
                attachments=data.get("attachments", []),
            )
        except Exception:
            return cls()

    def save(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "webhook_history": self.webhook_history[-10:],
            "last_webhook": self.last_webhook,
            "last_webhook_label": self.last_webhook_label,
            "template_path": self.template_path,
            "message": self.message,
            "everyone": self.everyone,
            "variables": self.variables,
            "config": self.config,
            "attachments": self.attachments,
        }
        STATE_FILE.write_text(json.dumps(payload, indent=2))


class NotifierGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Discord Webhook Notifier")
        self.state = GuiState.load()
        self.template_choices = self._discover_template_choices()
        self.var_entries: Dict[str, tk.StringVar] = {}
        self.var_definitions: Dict[str, Dict[str, object]] = {}
        self._list_summary_vars: Dict[str, tk.StringVar] = {}
        self._template_refresh_job: Optional[str] = None
        self._env_defaults = self._load_env_defaults()
        self.template_metadata = self._load_template_metadata()
        self._template_display_map: Dict[str, str] = {}
        self._suspend_template_display = False
        self._build_widgets()
        self._load_state()

    def _build_widgets(self) -> None:
        main = ttk.Frame(self, padding=12)
        main.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)

        ttk.Label(main, text="Webhook:").grid(row=0, column=0, sticky="w")
        self.webhook_var = tk.StringVar()
        self.webhook_display_var = tk.StringVar()
        self.webhook_combo = ttk.Combobox(
            main,
            textvariable=self.webhook_display_var,
            values=self._build_webhook_display_values(),
        )
        self.webhook_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.webhook_combo.bind("<<ComboboxSelected>>", self._on_webhook_selected)
        self.webhook_combo.bind("<FocusOut>", lambda _event: self._sync_webhook_value())
        self.webhook_tooltip = Tooltip(self.webhook_combo, self._webhook_tooltip_text)

        manage_webhook_btn = ttk.Button(
            main, text="Manage", command=self._open_webhook_manager
        )
        manage_webhook_btn.grid(row=0, column=2, sticky="ew")

        ttk.Label(main, text="Template:").grid(row=2, column=0, sticky="w")
        self.template_path_var = tk.StringVar()
        self.template_display_var = tk.StringVar()
        self.template_display_var.trace_add("write", self._on_template_display_changed)
        self.template_path_var.trace_add("write", self._on_template_var_changed)
        self.template_combo = ttk.Combobox(
            main,
            textvariable=self.template_display_var,
            values=self._build_template_display_values(),
        )
        self.template_combo.grid(row=2, column=1, sticky="ew", padx=(0, 8))

        browse_btn = ttk.Button(main, text="Browse", command=self._choose_template)
        browse_btn.grid(row=2, column=2, sticky="ew")

        self.everyone_var = tk.BooleanVar()
        everyone_check = ttk.Checkbutton(
            main, text="@everyone", variable=self.everyone_var
        )
        everyone_check.grid(row=3, column=0, sticky="w")

        ttk.Label(main, text="Message:").grid(row=4, column=0, sticky="nw")
        self.message_text = tk.Text(main, height=5, width=60)
        self.message_text.grid(row=4, column=1, columnspan=2, sticky="nsew", pady=4)

        ttk.Label(main, text="Variables:").grid(row=5, column=0, sticky="nw")
        self.vars_frame = ttk.Frame(main)
        self.vars_frame.grid(row=5, column=1, columnspan=2, sticky="nsew", pady=4)
        self.vars_frame.columnconfigure(1, weight=1)

        ttk.Label(main, text="Embed Assets:").grid(row=6, column=0, sticky="nw")
        attachments_frame = ttk.Frame(main)
        attachments_frame.grid(row=6, column=1, columnspan=2, sticky="nsew")
        attachments_frame.columnconfigure(0, weight=1)

        self.attachment_list = tk.Listbox(
            attachments_frame, height=4, selectmode=tk.EXTENDED
        )
        self.attachment_list.grid(row=0, column=0, sticky="nsew")
        Tooltip(
            self.attachment_list,
            lambda: "Files uploaded for attachment:// references. Use the CLI --upload flag for regular attachments.",
        )

        attachment_buttons = ttk.Frame(attachments_frame)
        attachment_buttons.grid(row=0, column=1, sticky="nsw", padx=(6, 0))
        ttk.Button(attachment_buttons, text="Add", command=self._add_attachment).grid(
            row=0, column=0, sticky="ew", pady=2
        )
        ttk.Button(
            attachment_buttons, text="Remove", command=self._remove_attachment
        ).grid(row=1, column=0, sticky="ew", pady=2)

        ttk.Label(main, text="Preview / Log:").grid(row=7, column=0, sticky="nw")
        self.preview_text = tk.Text(main, height=12, state="disabled")
        self.preview_text.grid(
            row=7, column=1, columnspan=2, sticky="nsew", pady=(4, 0)
        )

        button_bar = ttk.Frame(main)
        button_bar.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        button_bar.columnconfigure(1, weight=1)

        ttk.Button(button_bar, text="Preview", command=self._preview).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(
            button_bar, text="Configuration", command=self._open_config_dialog
        ).grid(row=0, column=1)
        ttk.Button(button_bar, text="Send", command=self._send).grid(
            row=0, column=2, padx=(8, 0)
        )

    def _load_state(self) -> None:
        webhook_value = self.state.last_webhook or self._env_defaults.get(
            "DISCORD_WEBHOOK_URL", ""
        )
        self.webhook_var.set(webhook_value)
        self.webhook_combo["values"] = self._build_webhook_display_values()
        display_value = self.state.last_webhook_label or webhook_value or ""
        self.webhook_display_var.set(display_value)
        self._sync_webhook_value()

        self._refresh_template_combo_values()
        self.template_path_var.set(self.state.template_path)
        self._update_template_display_from_path()

        self.message_text.insert("1.0", self.state.message)
        for attachment in self.state.attachments:
            self.attachment_list.insert(tk.END, attachment)
        self.everyone_var.set(self.state.everyone)
        self._refresh_template_variables()

    def _persist_state(self, variables: Dict[str, str]) -> None:
        attachments = list(self.attachment_list.get(0, tk.END))
        webhook = self.webhook_var.get().strip()
        self.state.last_webhook = webhook
        self.state.last_webhook_label = self._current_webhook_label()
        self.state.template_path = self.template_path_var.get().strip()
        self.state.message = self.message_text.get("1.0", tk.END).strip()
        self.state.everyone = self.everyone_var.get()
        self.state.variables = variables
        self.state.attachments = attachments
        self.state.save()

    def _choose_template(self) -> None:
        path = filedialog.askopenfilename(
            title="Select template",
            filetypes=[("JSON Templates", "*.json.j2"), ("All", "*.*")],
        )
        if path:
            resolved = str(Path(path).expanduser().resolve())
            if resolved not in self.template_choices:
                self.template_choices.append(resolved)
                self.template_choices.sort()
                self._refresh_template_combo_values()
            self.template_path_var.set(resolved)
            self._update_template_display_from_path()

    def _add_attachment(self) -> None:
        path = filedialog.askopenfilename(title="Select attachment")
        if path:
            self.attachment_list.insert(tk.END, path)
            self._refresh_template_variables()

    def _remove_attachment(self) -> None:
        selection = list(reversed(self.attachment_list.curselection()))
        for idx in selection:
            self.attachment_list.delete(idx)
        if selection:
            self._refresh_template_variables()

    def _build_webhook_display_values(self) -> List[str]:
        return [entry["label"] for entry in self.state.webhook_history]

    def _current_webhook_label(self) -> str:
        return self.webhook_display_var.get().strip()

    def _sync_webhook_value(self) -> None:
        label = self.webhook_display_var.get().strip()
        entry = next(
            (
                candidate
                for candidate in self.state.webhook_history
                if candidate["label"] == label
            ),
            None,
        )
        if entry:
            self.webhook_var.set(entry["url"])
        else:
            self.webhook_var.set(label)

    def _on_webhook_selected(self, *_args) -> None:
        self._sync_webhook_value()

    def _webhook_tooltip_text(self) -> str:
        label = self.webhook_display_var.get().strip()
        entry = next(
            (
                candidate
                for candidate in self.state.webhook_history
                if candidate["label"] == label
            ),
            None,
        )
        if entry:
            return entry["url"]
        return ""

    def _open_webhook_manager(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Saved Webhooks")
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("420x260")

        columns = ("Label", "URL")
        tree = ttk.Treeview(
            dialog, columns=columns, show="headings", selectmode="browse"
        )
        tree.heading("Label", text="Label")
        tree.heading("URL", text="URL")
        tree.column("Label", width=160)
        tree.column("URL", width=240)
        tree.grid(row=0, column=0, columnspan=3, sticky="nsew", padx=8, pady=8)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)

        def refresh_tree() -> None:
            for item in tree.get_children():
                tree.delete(item)
            for entry in self.state.webhook_history:
                tree.insert("", "end", values=(entry["label"], entry["url"]))
            self.webhook_combo["values"] = self._build_webhook_display_values()

        def selected_entry() -> Optional[Dict[str, str]]:
            selection = tree.selection()
            if not selection:
                return None
            idx = tree.index(selection[0])
            if idx >= len(self.state.webhook_history):
                return None
            return self.state.webhook_history[idx]

        def add_or_edit(entry: Optional[Dict[str, str]] = None) -> None:
            initial_label = entry["label"] if entry else ""
            initial_url = entry["url"] if entry else ""
            label = simpledialog.askstring(
                "Webhook Label",
                "Friendly name:",
                parent=dialog,
                initialvalue=initial_label,
            )
            if not label:
                return
            url = simpledialog.askstring(
                "Webhook URL",
                "Webhook URL:",
                parent=dialog,
                initialvalue=initial_url or self.webhook_var.get(),
            )
            if not url:
                return
            existing = next(
                (item for item in self.state.webhook_history if item["label"] == label),
                None,
            )
            if existing and existing is not entry:
                existing["url"] = url
            elif entry:
                entry["label"] = label
                entry["url"] = url
            else:
                self.state.webhook_history.append({"label": label, "url": url})
            self.state.save()
            refresh_tree()

        def delete_selected() -> None:
            entry = selected_entry()
            if not entry:
                return
            self.state.webhook_history.remove(entry)
            self.state.save()
            refresh_tree()

        def use_selected() -> None:
            entry = selected_entry()
            if not entry:
                return
            self.webhook_display_var.set(entry["label"])
            self.webhook_var.set(entry["url"])
            self.state.last_webhook = entry["url"]
            self.state.last_webhook_label = entry["label"]
            dialog.destroy()

        button_frame = ttk.Frame(dialog)
        button_frame.grid(row=1, column=0, columnspan=3, pady=(0, 8))

        ttk.Button(button_frame, text="Add", command=lambda: add_or_edit()).grid(
            row=0, column=0, padx=4
        )
        ttk.Button(
            button_frame,
            text="Edit",
            command=lambda: add_or_edit(selected_entry()),
        ).grid(row=0, column=1, padx=4)
        ttk.Button(button_frame, text="Delete", command=delete_selected).grid(
            row=0, column=2, padx=4
        )
        ttk.Button(button_frame, text="Use Selected", command=use_selected).grid(
            row=0, column=3, padx=4
        )
        ttk.Button(button_frame, text="Close", command=dialog.destroy).grid(
            row=0, column=4, padx=4
        )

        refresh_tree()

    def _build_cli_base(self) -> List[str]:
        module = "src.main"
        return [sys.executable, "-m", module]

    def _gather_cli_args(
        self, variables: Dict[str, str], json_variables: Dict[str, str]
    ) -> List[str]:
        args = []
        template = self.template_path_var.get().strip()
        if template:
            args.extend(["--template", template])
        webhook = self.webhook_var.get().strip()
        if webhook:
            args.extend(["--webhook", webhook])
        message = self.message_text.get("1.0", tk.END).strip()
        if message:
            args.extend(["--message", message])
        if self.everyone_var.get():
            args.append("--everyone")
        for key, value in variables.items():
            args.extend(["--var", f"{key}={value}"])
        for key, value in json_variables.items():
            args.extend(["--json-var", f"{key}={value}"])
        for item in self.attachment_list.get(0, tk.END):
            args.extend(["--file", item])
        return args

    def _preview(self) -> None:
        self._execute_cli(dry_run=True)

    def _send(self) -> None:
        self._execute_cli(dry_run=False)

    def _execute_cli(self, *, dry_run: bool) -> None:
        template_variables, template_json_variables = self._collect_variable_entries()
        config_overrides = self._collect_config_overrides()
        combined_variables = {**config_overrides, **template_variables}

        self._persist_state({**template_variables, **template_json_variables})
        base_cmd = self._build_cli_base()
        args = self._gather_cli_args(combined_variables, template_json_variables)
        if dry_run:
            args.append("--dry-run")
        cmd = base_cmd + args

        try:
            completed = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        except Exception as exc:
            messagebox.showerror("Execution error", str(exc))
            return

        output = []
        if completed.stdout:
            output.append(completed.stdout.strip())
        if completed.stderr:
            output.append(completed.stderr.strip())

        status = f"Exit code: {completed.returncode}"
        output.append(status)

        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.insert("1.0", "\n\n".join(part for part in output if part))
        self.preview_text.configure(state="disabled")

        if completed.returncode == 0 and not dry_run:
            messagebox.showinfo("Success", "Message sent successfully.")
        elif completed.returncode != 0:
            messagebox.showwarning(
                "Error", f"Command exited with {completed.returncode}. See logs above."
            )

    def _on_template_var_changed(self, *_args) -> None:
        if self._template_refresh_job is not None:
            self.after_cancel(self._template_refresh_job)
        self._template_refresh_job = self.after(300, self._apply_template_refresh)

    def _apply_template_refresh(self) -> None:
        self._template_refresh_job = None
        self._refresh_template_variables()

    def _refresh_template_variables(self) -> None:
        preserved: Dict[str, str] = dict(self.state.variables)
        for key, var in self.var_entries.items():
            preserved[key] = var.get()
        template_path = self.template_path_var.get().strip()
        template_vars = self._detect_template_variables(template_path)
        variable_defs = self._build_variable_definitions(template_path, template_vars)
        self._populate_variable_entries(variable_defs, preserved)

    def _populate_variable_entries(
        self,
        variable_defs: Sequence[Dict[str, object]],
        preserved_values: Dict[str, str],
    ) -> None:
        for child in self.vars_frame.winfo_children():
            child.destroy()
        self.var_entries = {}
        self.var_definitions = {}
        self._list_summary_vars = {}

        if not variable_defs:
            msg = "Select a template to load its variables."
            ttk.Label(self.vars_frame, text=msg).grid(row=0, column=0, sticky="w")
            return

        for row, definition in enumerate(variable_defs):
            name = str(definition.get("name", ""))
            if not name:
                continue
            self.var_definitions[name] = definition
            label_text = str(
                definition.get("label") or self._humanize_variable_name(name)
            )
            field_type = str(definition.get("type") or "")
            label = ttk.Label(self.vars_frame, text=f"{label_text}:")
            label.grid(row=row, column=0, sticky="w", pady=1, padx=(0, 6))
            initial_value = self._resolve_initial_value(
                name, definition, preserved_values
            )
            var = tk.StringVar(value=initial_value)
            self.var_entries[name] = var

            if field_type == "list":
                summary = tk.StringVar()
                self._list_summary_vars[name] = summary
                self._update_list_summary(name)
                ttk.Label(self.vars_frame, textvariable=summary).grid(
                    row=row, column=1, sticky="w", pady=1
                )
                ttk.Button(
                    self.vars_frame,
                    text="Edit…",
                    command=lambda target=name: self._open_list_editor(target),
                ).grid(row=row, column=2, sticky="w", padx=(6, 0))
                var.trace_add(
                    "write", lambda *_args, key=name: self._update_list_summary(key)
                )
                continue

            if self._is_image_field(name, field_type):
                entry = ttk.Combobox(
                    self.vars_frame,
                    textvariable=var,
                    values=self._attachment_suggestions(),
                )
            else:
                entry = ttk.Entry(self.vars_frame, textvariable=var)
            entry.grid(row=row, column=1, sticky="ew", pady=1)
            column = 2
            if self._is_color_field(name, field_type):
                ttk.Button(
                    self.vars_frame,
                    text="Pick Color",
                    command=lambda target=var: self._pick_color_into(target),
                ).grid(row=row, column=column, padx=(4, 0))
                column += 1
            if self._is_image_field(name, field_type):
                ttk.Button(
                    self.vars_frame,
                    text="Preview",
                    command=lambda target=var: self._preview_image_from_value(
                        target.get().strip()
                    ),
                ).grid(row=row, column=column, padx=(4, 0))

    def _collect_variable_entries(self) -> Tuple[Dict[str, str], Dict[str, str]]:
        string_vars: Dict[str, str] = {}
        json_vars: Dict[str, str] = {}
        for key, var in self.var_entries.items():
            value = var.get().strip()
            if not value:
                continue
            target = json_vars if self._is_json_field(key) else string_vars
            target[key] = value
        return string_vars, json_vars

    def _collect_config_overrides(self) -> Dict[str, str]:
        overrides: Dict[str, str] = {}
        for key, _meta in CONFIG_FIELDS:
            value = self.state.config.get(key) or self._env_defaults.get(key, "")
            if value:
                overrides[key] = value
        return overrides

    def _resolve_initial_value(
        self,
        name: str,
        definition: Mapping[str, object],
        preserved_values: Mapping[str, str],
    ) -> str:
        if name in preserved_values:
            return preserved_values[name]
        default = definition.get("default")
        is_json_field = bool(definition.get("is_json"))
        if default is not None:
            if is_json_field:
                return json.dumps(default, ensure_ascii=False)
            return str(default)
        if is_json_field:
            field_type = str(definition.get("type") or "")
            if field_type == "list":
                return "[]"
            return ""
        return ""

    def _is_json_field(self, name: str) -> bool:
        definition = self.var_definitions.get(name) or {}
        return bool(definition.get("is_json"))

    def _update_list_summary(self, name: str) -> None:
        summary_var = self._list_summary_vars.get(name)
        var = self.var_entries.get(name)
        if summary_var is None or var is None:
            return
        raw = var.get().strip()
        if not raw:
            summary_var.set("No items")
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            summary_var.set("Invalid data")
            return
        if isinstance(data, list):
            count = len(data)
            label = (
                self.var_definitions.get(name, {})
                .get("config", {})
                .get("item_label", "item")
            )
            if count == 0:
                summary_var.set(f"No {label}s")
            else:
                plural = "" if count == 1 else "s"
                summary_var.set(f"{count} {label}{plural}")
        else:
            summary_var.set("Invalid data")

    def _open_list_editor(self, name: str) -> None:
        definition = self.var_definitions.get(name)
        var = self.var_entries.get(name)
        if not definition or var is None:
            return
        config = definition.get("config") or {}
        fields = config.get("fields") or []
        if not fields:
            messagebox.showwarning(
                "Structured Input",
                "This field does not define any item metadata.",
                parent=self,
            )
            return
        label_text = (
            definition.get("label")
            or self._humanize_variable_name(name)
            or name.title()
        )
        item_label = config.get("item_label") or "Item"
        items = self._load_structured_list(var.get())

        dialog = tk.Toplevel(self)
        dialog.title(f"Edit {label_text}")
        dialog.transient(self)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)

        columns = [str(field.get("name") or idx) for idx, field in enumerate(fields)]
        tree = ttk.Treeview(dialog, columns=columns, show="headings", height=8)
        for column_id, field in zip(columns, fields):
            heading = field.get("label") or self._humanize_variable_name(column_id)
            tree.heading(column_id, text=str(heading))
            width = int(field.get("width", 160))
            tree.column(column_id, width=width, anchor="w")
        tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

        button_frame = ttk.Frame(dialog)
        button_frame.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        def refresh_tree() -> None:
            tree.delete(*tree.get_children())
            for idx, item in enumerate(items):
                values = [
                    self._format_structured_field_value(field, item.get(field_name))
                    for field, field_name in zip(fields, columns)
                ]
                tree.insert("", "end", iid=str(idx), values=values)

        def selected_index() -> Optional[int]:
            selection = tree.selection()
            if not selection:
                return None
            try:
                return int(selection[0])
            except ValueError:
                return None

        def add_item() -> None:
            result = self._prompt_list_item(
                dialog,
                item_label=f"New {item_label}",
                fields=fields,
            )
            if result is not None:
                items.append(result)
                refresh_tree()

        def edit_item() -> None:
            idx = selected_index()
            if idx is None:
                return
            result = self._prompt_list_item(
                dialog,
                item_label=f"Edit {item_label}",
                fields=fields,
                initial=items[idx],
            )
            if result is not None:
                items[idx] = result
                refresh_tree()

        def remove_item() -> None:
            idx = selected_index()
            if idx is None:
                return
            del items[idx]
            refresh_tree()

        def move_item(offset: int) -> None:
            idx = selected_index()
            if idx is None:
                return
            new_index = idx + offset
            if new_index < 0 or new_index >= len(items):
                return
            items[idx], items[new_index] = items[new_index], items[idx]
            refresh_tree()
            tree.selection_set(str(new_index))

        ttk.Button(button_frame, text="Add", command=add_item).grid(
            row=0, column=0, padx=(0, 4)
        )
        ttk.Button(button_frame, text="Edit", command=edit_item).grid(
            row=0, column=1, padx=4
        )
        ttk.Button(button_frame, text="Remove", command=remove_item).grid(
            row=0, column=2, padx=4
        )
        ttk.Button(button_frame, text="Move Up", command=lambda: move_item(-1)).grid(
            row=0, column=3, padx=4
        )
        ttk.Button(button_frame, text="Move Down", command=lambda: move_item(1)).grid(
            row=0, column=4, padx=4
        )

        tree.bind("<Double-1>", lambda *_args: edit_item())

        action_frame = ttk.Frame(dialog)
        action_frame.grid(row=2, column=0, columnspan=2, sticky="e", pady=(12, 0))

        def close_without_save() -> None:
            dialog.destroy()

        def save_and_close() -> None:
            var.set(json.dumps(items, ensure_ascii=False))
            self._update_list_summary(name)
            dialog.destroy()

        ttk.Button(action_frame, text="Cancel", command=close_without_save).grid(
            row=0, column=0, padx=(0, 6)
        )
        ttk.Button(action_frame, text="Done", command=save_and_close).grid(
            row=0, column=1
        )

        dialog.protocol("WM_DELETE_WINDOW", close_without_save)
        refresh_tree()
        self.wait_window(dialog)

    def _load_structured_list(self, raw: str) -> List[Dict[str, object]]:
        raw = raw.strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        cleaned: List[Dict[str, object]] = []
        for item in data:
            if isinstance(item, dict):
                cleaned.append(dict(item))
        return cleaned

    def _prompt_list_item(
        self,
        parent: tk.Toplevel,
        *,
        item_label: str,
        fields: Sequence[Mapping[str, object]],
        initial: Optional[Mapping[str, object]] = None,
    ) -> Optional[Dict[str, object]]:
        dialog = tk.Toplevel(parent)
        dialog.title(item_label)
        dialog.transient(parent)
        dialog.grab_set()
        dialog.columnconfigure(1, weight=1)

        entries: Dict[str, ttk.Entry] = {}
        text_widgets: Dict[str, tk.Text] = {}
        bool_vars: Dict[str, tk.BooleanVar] = {}

        for row, field in enumerate(fields):
            field_name = str(field.get("name") or row)
            label_text = str(
                field.get("label") or self._humanize_variable_name(field_name)
            )
            ttk.Label(dialog, text=f"{label_text}:").grid(
                row=row, column=0, sticky="w", padx=(0, 8), pady=2
            )
            widget_type = str(field.get("widget") or "")
            field_type = str(field.get("type") or "")
            default_value = None
            if initial and field_name in initial:
                default_value = initial[field_name]
            elif "default" in field:
                default_value = field.get("default")
            if field_type == "bool":
                var = tk.BooleanVar(value=bool(default_value))
                bool_vars[field_name] = var
                ttk.Checkbutton(dialog, variable=var).grid(
                    row=row, column=1, sticky="w"
                )
                continue
            if widget_type == "textarea":
                rows = int(field.get("rows", 4))
                text = tk.Text(dialog, height=rows, width=40)
                if default_value:
                    text.insert("1.0", str(default_value))
                text.grid(row=row, column=1, sticky="ew")
                text_widgets[field_name] = text
            else:
                entry = ttk.Entry(dialog)
                if default_value:
                    entry.insert(0, str(default_value))
                entry.grid(row=row, column=1, sticky="ew")
                entries[field_name] = entry

        button_frame = ttk.Frame(dialog)
        button_frame.grid(row=len(fields), column=0, columnspan=2, pady=(10, 0))

        result: List[Dict[str, object]] = []

        def close_without_save() -> None:
            dialog.destroy()

        def save_and_close() -> None:
            data: Dict[str, object] = {}
            for field in fields:
                field_name = str(field.get("name") or "")
                field_type = str(field.get("type") or "")
                widget_type = str(field.get("widget") or "")
                value: object
                if field_type == "bool":
                    value = bool_vars.get(field_name, tk.BooleanVar(value=False)).get()
                elif widget_type == "textarea":
                    widget = text_widgets.get(field_name)
                    value = widget.get("1.0", tk.END).strip() if widget else ""
                else:
                    entry = entries.get(field_name)
                    value = entry.get().strip() if entry else ""
                if field.get("required") and (value is None or value == ""):
                    messagebox.showwarning(
                        "Missing value",
                        f"{field.get('label', field_name)} is required.",
                        parent=dialog,
                    )
                    return
                data[field_name] = value
            result.append(data)
            dialog.destroy()

        ttk.Button(button_frame, text="Cancel", command=close_without_save).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(button_frame, text="Save", command=save_and_close).grid(
            row=0, column=1
        )

        dialog.protocol("WM_DELETE_WINDOW", close_without_save)
        parent.wait_window(dialog)
        return result[0] if result else None

    @staticmethod
    def _format_structured_field_value(
        field: Mapping[str, object], value: object
    ) -> str:
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if value is None:
            return ""
        text = str(value)
        return text.replace("\n", " ").strip()

    def _attachment_suggestions(self) -> List[str]:
        suggestions: List[str] = []
        seen: set[str] = set()
        for raw in self.attachment_list.get(0, tk.END):
            name = Path(raw).name
            token = f"attachment://{name}"
            if token not in seen:
                seen.add(token)
                suggestions.append(token)
        return suggestions

    def _resolve_attachment_path(self, filename: str) -> Optional[Path]:
        for raw in self.attachment_list.get(0, tk.END):
            path = Path(raw).expanduser()
            if path.name == filename:
                return path
        return None

    def _create_photo_image(self, data: bytes) -> Optional[tk.PhotoImage]:
        if Image is not None and ImageTk is not None:
            try:
                image = Image.open(io.BytesIO(data))
                return ImageTk.PhotoImage(image)
            except Exception:
                pass
        try:
            encoded = base64.b64encode(data).decode("ascii")
            return tk.PhotoImage(data=encoded)
        except Exception:
            return None

    @staticmethod
    def _detect_template_variables(template_path: str) -> List[str]:
        if not template_path:
            return []
        path = Path(template_path).expanduser()
        if not path.exists():
            return []
        try:
            source = path.read_text()
        except Exception:
            return []
        env = Environment(autoescape=False)
        try:
            parsed = env.parse(source)
        except Exception:
            return []
        raw = meta.find_undeclared_variables(parsed)
        candidates = [name for name in raw if name not in RESERVED_TEMPLATE_VARS]
        return sorted(candidates)

    @staticmethod
    def _load_env_defaults() -> Dict[str, str]:
        try:
            return load_environment()
        except Exception:
            return {}

    @staticmethod
    def _discover_template_choices() -> List[str]:
        base = TEMPLATE_DIR
        if not base.exists():
            return []
        files = sorted(p.resolve() for p in base.glob("*.json.j2"))
        return [str(p) for p in files]

    def _refresh_template_combo_values(self) -> None:
        values = self._build_template_display_values()
        self.template_combo["values"] = values

    def _build_template_display_values(self) -> List[str]:
        values: List[str] = []
        self._template_display_map = {}
        for raw_path in self.template_choices:
            display = self._template_display_for_path(raw_path)
            values.append(display)
            self._template_display_map[display] = raw_path
        return values

    def _template_display_for_path(self, path: str) -> str:
        if not path:
            return ""
        metadata = self._get_template_metadata(path)
        if isinstance(metadata, dict):
            display_name = metadata.get("display_name") or metadata.get("name")
            if isinstance(display_name, str) and display_name.strip():
                return display_name.strip()
        resolved = Path(path).expanduser().resolve()
        try:
            return str(resolved.relative_to(TEMPLATE_DIR.resolve()))
        except Exception:
            return str(resolved)

    def _template_path_from_display(self, display: str) -> Optional[str]:
        return self._template_display_map.get(display.strip())

    def _update_template_display_from_path(self) -> None:
        path = self.template_path_var.get().strip()
        display = self._template_display_for_path(path)
        self._suspend_template_display = True
        try:
            self.template_display_var.set(display)
        finally:
            self._suspend_template_display = False

    def _on_template_display_changed(self, *_args) -> None:
        if self._suspend_template_display:
            return
        display = self.template_display_var.get().strip()
        path = self._template_path_from_display(display) or display
        if self.template_path_var.get().strip() != path:
            self.template_path_var.set(path)

    def _build_variable_definitions(
        self, template_path: str, variable_names: Sequence[str]
    ) -> List[Dict[str, object]]:
        metadata = self._get_template_metadata(template_path)
        variable_metadata = {}
        if isinstance(metadata, dict):
            variable_metadata = metadata.get("variables", {}) or {}
        definitions: List[Dict[str, object]] = []
        for name in variable_names:
            info = {}
            if isinstance(variable_metadata, dict):
                info = variable_metadata.get(name, {}) or {}
            field_type = str(info.get("type") or info.get("input") or "")
            definition: Dict[str, object] = {
                "name": name,
                "label": info.get("label"),
                "type": field_type,
                "config": info,
                "is_json": bool(info.get("json")),
            }
            if "default" in info:
                definition["default"] = info.get("default")
            if field_type in {"list", "object"}:
                definition["is_json"] = True
            definitions.append(definition)
        return definitions

    def _get_template_metadata(self, template_path: str) -> Dict[str, object]:
        if not template_path:
            return {}
        path = Path(template_path)
        key = self._metadata_key_for_template(path)
        return self.template_metadata.get(key, {}) if key else {}

    @staticmethod
    def _metadata_key_for_template(path: Path) -> Optional[str]:
        try:
            return str(path.resolve().relative_to(TEMPLATE_DIR.resolve()))
        except Exception:
            return path.resolve().name

    @staticmethod
    def _load_template_metadata() -> Dict[str, object]:
        if not TEMPLATE_METADATA_FILE.exists():
            return {}
        try:
            return json.loads(TEMPLATE_METADATA_FILE.read_text())
        except Exception:
            return {}

    @staticmethod
    def _humanize_variable_name(name: str) -> str:
        tokens = name.replace("-", " ").replace("_", " ").split()
        return " ".join(token.capitalize() for token in tokens) if tokens else name

    @staticmethod
    def _is_color_field(name: str, field_type: str) -> bool:
        lowered = name.lower()
        return field_type == "color" or "color" in lowered

    @staticmethod
    def _is_image_field(name: str, field_type: str) -> bool:
        lowered = name.lower()
        image_keywords = ("image", "thumbnail", "banner")
        return field_type == "image" or any(
            keyword in lowered for keyword in image_keywords
        )

    def _open_config_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Configuration")
        dialog.transient(self)
        dialog.grab_set()
        entries: Dict[str, tk.StringVar] = {}

        for row, (key, meta) in enumerate(CONFIG_FIELDS):
            label_text = meta.get("label", key)
            ttk.Label(dialog, text=f"{label_text}:").grid(
                row=row, column=0, sticky="w", padx=(0, 8), pady=2
            )
            initial_value = self.state.config.get(key) or self._env_defaults.get(
                key, ""
            )
            var = tk.StringVar(value=initial_value)
            entries[key] = var
            entry = ttk.Entry(dialog, textvariable=var, width=40)
            entry.grid(row=row, column=1, sticky="ew", pady=2)
            dialog.columnconfigure(1, weight=1)

            column = 2
            if self._is_color_field(key, meta.get("type", "")):
                ttk.Button(
                    dialog,
                    text="Pick Color",
                    command=lambda target=var: self._pick_color_into(target),
                ).grid(row=row, column=column, padx=(4, 0))
                column += 1
            if meta.get("type") == "image":
                ttk.Button(
                    dialog,
                    text="Preview",
                    command=lambda target=var: self._preview_image_from_value(
                        target.get().strip()
                    ),
                ).grid(row=row, column=column, padx=(4, 0))

        button_row = len(CONFIG_FIELDS)
        button_frame = ttk.Frame(dialog)
        button_frame.grid(row=button_row, column=0, columnspan=3, pady=(12, 4))
        ttk.Button(button_frame, text="Cancel", command=dialog.destroy).grid(
            row=0, column=0, padx=4
        )

        def _save_config() -> None:
            for key, var in entries.items():
                value = var.get().strip()
                if value:
                    self.state.config[key] = value
                elif key in self.state.config:
                    del self.state.config[key]
            self.state.save()
            dialog.destroy()

        ttk.Button(button_frame, text="Save", command=_save_config).grid(
            row=0, column=1, padx=4
        )

    def _pick_color_into(self, target: tk.StringVar) -> None:
        current = (
            target.get().strip()
            or self.state.config.get("DEFAULT_COLOR")
            or self._env_defaults.get("DEFAULT_COLOR", "")
        )
        initial_hex = self._to_hex_color(current) or "#5865F2"
        result = colorchooser.askcolor(color=initial_hex, parent=self)
        if not result or not result[1]:
            return
        formatted = self._format_color_value(result[1])
        target.set(formatted)

    def _preview_image_from_value(self, value: str) -> None:
        value = value.strip()
        if not value:
            messagebox.showwarning("Image Preview", "Enter an image URL first.")
            return
        data: Optional[bytes] = None
        source_label = value
        if value.startswith("attachment://"):
            attachment_name = value.split("attachment://", 1)[1]
            attachment_path = self._resolve_attachment_path(attachment_name)
            if not attachment_path:
                messagebox.showwarning(
                    "Image Preview",
                    f"Attachment '{attachment_name}' not found in the list.",
                )
                return
            try:
                data = attachment_path.read_bytes()
                source_label = str(attachment_path)
            except Exception as exc:
                messagebox.showerror(
                    "Image Preview Failed", f"Could not read attachment: {exc}"
                )
                return
        elif value.startswith("http://") or value.startswith("https://"):
            try:
                response = requests.get(value, timeout=10)
                response.raise_for_status()
                data = response.content
            except Exception as exc:
                messagebox.showerror(
                    "Image Preview Failed", f"Could not fetch image:\n{exc}"
                )
                return
        else:
            path = Path(value).expanduser()
            if not path.exists():
                messagebox.showwarning(
                    "Image Preview", f"File '{value}' does not exist."
                )
                return
            try:
                data = path.read_bytes()
                source_label = str(path)
            except Exception as exc:
                messagebox.showerror(
                    "Image Preview Failed", f"Could not read file: {exc}"
                )
                return

        image = self._create_photo_image(data)
        if image is None:
            if Image is None or ImageTk is None:
                messagebox.showerror(
                    "Image Preview Failed",
                    "Unsupported image format. Install Pillow for broader image support.",
                )
            else:
                messagebox.showerror(
                    "Image Preview Failed", "Unsupported image format."
                )
            return
        preview = tk.Toplevel(self)
        preview.title("Image Preview")
        label = ttk.Label(preview, image=image)
        label.image = image
        label.grid(row=0, column=0, padx=8, pady=8)
        ttk.Label(preview, text=source_label, wraplength=400).grid(
            row=1, column=0, padx=8, pady=(0, 8)
        )

    @staticmethod
    def _to_hex_color(value: str) -> Optional[str]:
        if not value:
            return None
        value = value.strip()
        if value.startswith("#") and len(value) == 7:
            return value
        if value.lower().startswith("0x") and len(value) == 8:
            return f"#{value[2:]}"
        try:
            numeric = int(value, 0)
            return f"#{numeric:06X}"
        except ValueError:
            return None

    @staticmethod
    def _format_color_value(hex_value: str) -> str:
        cleaned = hex_value.strip().lstrip("#")
        if len(cleaned) != 6:
            return hex_value
        return f"0x{cleaned.upper()}"


def launch() -> None:
    app = NotifierGUI()
    app.mainloop()


if __name__ == "__main__":
    launch()
