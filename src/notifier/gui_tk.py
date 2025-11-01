from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

STATE_DIR = Path.home() / ".discord-webhook-notifier"
STATE_FILE = STATE_DIR / "state.json"


@dataclass
class GuiState:
    webhook_history: List[str] = field(default_factory=list)
    last_webhook: str = ""
    template_path: str = ""
    message: str = ""
    everyone: bool = False
    variables: Dict[str, str] = field(default_factory=dict)
    attachments: List[str] = field(default_factory=list)

    @classmethod
    def load(cls) -> "GuiState":
        if not STATE_FILE.exists():
            return cls()
        try:
            data = json.loads(STATE_FILE.read_text())
            return cls(
                webhook_history=data.get("webhook_history", []),
                last_webhook=data.get("last_webhook", ""),
                template_path=data.get("template_path", ""),
                message=data.get("message", ""),
                everyone=data.get("everyone", False),
                variables=data.get("variables", {}),
                attachments=data.get("attachments", []),
            )
        except Exception:
            return cls()

    def save(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "webhook_history": self.webhook_history[-10:],
            "last_webhook": self.last_webhook,
            "template_path": self.template_path,
            "message": self.message,
            "everyone": self.everyone,
            "variables": self.variables,
            "attachments": self.attachments,
        }
        STATE_FILE.write_text(json.dumps(payload, indent=2))


class NotifierGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Discord Webhook Notifier")
        self.state = GuiState.load()
        self._build_widgets()
        self._load_state()

    def _build_widgets(self) -> None:
        main = ttk.Frame(self, padding=12)
        main.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)

        ttk.Label(main, text="Webhook URL:").grid(row=0, column=0, sticky="w")
        self.webhook_var = tk.StringVar()
        self.webhook_combo = ttk.Combobox(main, textvariable=self.webhook_var)
        self.webhook_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))

        save_webhook_btn = ttk.Button(main, text="Save", command=self._save_webhook_to_history)
        save_webhook_btn.grid(row=0, column=2, sticky="ew")

        ttk.Label(main, text="Template:").grid(row=1, column=0, sticky="w")
        self.template_var = tk.StringVar()
        template_entry = ttk.Entry(main, textvariable=self.template_var)
        template_entry.grid(row=1, column=1, sticky="ew", padx=(0, 8))

        browse_btn = ttk.Button(main, text="Browse", command=self._choose_template)
        browse_btn.grid(row=1, column=2, sticky="ew")

        self.everyone_var = tk.BooleanVar()
        everyone_check = ttk.Checkbutton(main, text="@everyone", variable=self.everyone_var)
        everyone_check.grid(row=2, column=0, sticky="w")

        ttk.Label(main, text="Message:").grid(row=3, column=0, sticky="nw")
        self.message_text = tk.Text(main, height=5, width=60)
        self.message_text.grid(row=3, column=1, columnspan=2, sticky="nsew", pady=4)

        ttk.Label(main, text="Variables (key=value per line):").grid(row=4, column=0, sticky="nw")
        self.vars_text = tk.Text(main, height=4)
        self.vars_text.grid(row=4, column=1, columnspan=2, sticky="nsew", pady=4)

        ttk.Label(main, text="Attachments:").grid(row=5, column=0, sticky="nw")
        attachments_frame = ttk.Frame(main)
        attachments_frame.grid(row=5, column=1, columnspan=2, sticky="nsew")
        attachments_frame.columnconfigure(0, weight=1)

        self.attachment_list = tk.Listbox(attachments_frame, height=4, selectmode=tk.EXTENDED)
        self.attachment_list.grid(row=0, column=0, sticky="nsew")

        attachment_buttons = ttk.Frame(attachments_frame)
        attachment_buttons.grid(row=0, column=1, sticky="nsw", padx=(6, 0))
        ttk.Button(attachment_buttons, text="Add", command=self._add_attachment).grid(row=0, column=0, sticky="ew", pady=2)
        ttk.Button(attachment_buttons, text="Remove", command=self._remove_attachment).grid(
            row=1, column=0, sticky="ew", pady=2
        )

        ttk.Label(main, text="Preview / Log:").grid(row=6, column=0, sticky="nw")
        self.preview_text = tk.Text(main, height=12, state="disabled")
        self.preview_text.grid(row=6, column=1, columnspan=2, sticky="nsew", pady=(4, 0))

        button_bar = ttk.Frame(main)
        button_bar.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        button_bar.columnconfigure(1, weight=1)

        ttk.Button(button_bar, text="Preview", command=self._preview).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(button_bar, text="Send", command=self._send).grid(row=0, column=2, padx=(8, 0))

    def _load_state(self) -> None:
        self.webhook_combo["values"] = self.state.webhook_history
        self.webhook_var.set(self.state.last_webhook)
        self.template_var.set(self.state.template_path)
        self.message_text.insert("1.0", self.state.message)
        vars_blob = "\n".join(f"{k}={v}" for k, v in self.state.variables.items())
        self.vars_text.insert("1.0", vars_blob)
        for attachment in self.state.attachments:
            self.attachment_list.insert(tk.END, attachment)
        self.everyone_var.set(self.state.everyone)

    def _persist_state(self) -> None:
        variables = {}
        for line in self.vars_text.get("1.0", tk.END).splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            variables[key.strip()] = value.strip()

        attachments = list(self.attachment_list.get(0, tk.END))
        webhook = self.webhook_var.get().strip()
        if webhook and webhook not in self.state.webhook_history:
            self.state.webhook_history.append(webhook)

        self.state.last_webhook = webhook
        self.state.template_path = self.template_var.get().strip()
        self.state.message = self.message_text.get("1.0", tk.END).strip()
        self.state.everyone = self.everyone_var.get()
        self.state.variables = variables
        self.state.attachments = attachments
        self.state.save()

    def _choose_template(self) -> None:
        path = filedialog.askopenfilename(title="Select template", filetypes=[("JSON Templates", "*.json.j2"), ("All", "*.*")])
        if path:
            self.template_var.set(path)

    def _add_attachment(self) -> None:
        path = filedialog.askopenfilename(title="Select attachment")
        if path:
            self.attachment_list.insert(tk.END, path)

    def _remove_attachment(self) -> None:
        selection = list(reversed(self.attachment_list.curselection()))
        for idx in selection:
            self.attachment_list.delete(idx)

    def _save_webhook_to_history(self) -> None:
        value = self.webhook_var.get().strip()
        if not value:
            return
        if value not in self.state.webhook_history:
            self.state.webhook_history.append(value)
            self.webhook_combo["values"] = self.state.webhook_history
            messagebox.showinfo("Saved", "Webhook added to history.")
        else:
            messagebox.showinfo("Saved", "Webhook already in history.")
        self.state.save()

    def _build_cli_base(self) -> List[str]:
        module = "src.main"
        return [sys.executable, "-m", module]

    def _gather_cli_args(self) -> List[str]:
        args = []
        template = self.template_var.get().strip()
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
        for line in self.vars_text.get("1.0", tk.END).splitlines():
            line = line.strip()
            if not line:
                continue
            args.extend(["--var", line])
        for item in self.attachment_list.get(0, tk.END):
            args.extend(["--file", item])
        return args

    def _preview(self) -> None:
        self._execute_cli(dry_run=True)

    def _send(self) -> None:
        self._execute_cli(dry_run=False)

    def _execute_cli(self, *, dry_run: bool) -> None:
        self._persist_state()
        base_cmd = self._build_cli_base()
        args = self._gather_cli_args()
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
            messagebox.showwarning("Error", f"Command exited with {completed.returncode}. See logs above.")


def launch() -> None:
    app = NotifierGUI()
    app.mainloop()
