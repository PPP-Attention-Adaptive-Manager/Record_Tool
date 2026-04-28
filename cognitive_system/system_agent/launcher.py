from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

from .config import MODE_PRODUCTION, RuntimeConfig, VALID_MODES, build_default_runtime_config
from .dependency_validation import validate_runtime_dependencies
from .main import CognitiveSystemAgent


@dataclass
class LaunchDecision:
    confirmed: bool
    config: RuntimeConfig | None = None


class StartupLauncher:
    """Small default-config confirmation window for production-style launch."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self._decision = LaunchDecision(confirmed=False)

    def show(self) -> RuntimeConfig | None:
        try:
            import tkinter as tk
            from tkinter import ttk
        except ImportError:
            raise RuntimeError("tkinter is required for the desktop launcher.")

        root = tk.Tk()
        root.title("Cognitive Session Launcher")
        root.configure(bg="#edf3fb")
        root.geometry("760x640")
        root.minsize(680, 560)
        root.resizable(True, True)

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        root.geometry(f"760x640+{max(0, sw // 2 - 380)}+{max(0, sh // 2 - 320)}")

        shell = tk.Frame(root, bg="#edf3fb", padx=22, pady=20)
        shell.pack(fill="both", expand=True)
        shell.grid_columnconfigure(0, weight=1)
        shell.grid_rowconfigure(2, weight=1)

        tk.Label(
            shell,
            text="Cognitive Data Collection",
            bg="#edf3fb",
            fg="#203243",
            font=("Segoe UI", 22, "bold"),
        ).grid(row=0, column=0, sticky="w")

        tk.Label(
            shell,
            text="Default configuration is ready. Click Start Session to begin.",
            bg="#edf3fb",
            fg="#5f7285",
            font=("Segoe UI", 10),
        ).grid(row=1, column=0, sticky="w", pady=(6, 16))

        card_shell = tk.Frame(shell, bg="#edf3fb")
        card_shell.grid(row=2, column=0, sticky="nsew")
        card_shell.grid_columnconfigure(0, weight=1)
        card_shell.grid_rowconfigure(0, weight=1)

        canvas = tk.Canvas(card_shell, bg="#edf3fb", highlightthickness=0)
        scrollbar = tk.Scrollbar(card_shell, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(10, 0))

        card = tk.Frame(canvas, bg="#ffffff", padx=18, pady=18, highlightbackground="#d5deea", highlightthickness=1)
        card_window = canvas.create_window((0, 0), window=card, anchor="nw")

        def _sync_scrollregion(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _resize_card(event) -> None:
            canvas.itemconfigure(card_window, width=event.width)

        card.bind("<Configure>", _sync_scrollregion)
        canvas.bind("<Configure>", _resize_card)

        mode_var = tk.StringVar(value=self.config.mode)
        duration_var = tk.StringVar(value=str(self.config.session_duration_minutes))
        csv_var = tk.BooleanVar(value=self.config.csv_enabled)
        influx_var = tk.BooleanVar(value=self.config.influx_enabled)
        dual_task_var = tk.BooleanVar(value=self.config.dual_task_enabled)
        questionnaire_var = tk.BooleanVar(value=self.config.questionnaire_enabled)
        keyboard_var = tk.BooleanVar(value=self.config.keyboard_tracking_enabled)
        mouse_var = tk.BooleanVar(value=self.config.mouse_tracking_enabled)
        notification_var = tk.BooleanVar(value=self.config.notification_tracking_enabled)
        system_metrics_var = tk.BooleanVar(value=self.config.system_metrics_enabled)
        overlay_var = tk.BooleanVar(value=self.config.ui_overlay_enabled)
        status_var = tk.StringVar(value="")

        def _field_row(label_text: str, hint: str = "") -> tk.Frame:
            section = tk.Frame(card, bg="#ffffff", pady=7)
            section.pack(fill="x")
            tk.Label(
                section,
                text=label_text,
                anchor="w",
                bg="#ffffff",
                fg="#31475d",
                font=("Segoe UI", 10, "bold"),
            ).pack(anchor="w")
            if hint:
                tk.Label(
                    section,
                    text=hint,
                    anchor="w",
                    justify="left",
                    bg="#ffffff",
                    fg="#6a7d90",
                    font=("Segoe UI", 9),
                    wraplength=600,
                ).pack(anchor="w", pady=(2, 6))
            return section

        mode_section = _field_row("Mode", "Production mode automatically disables dual task and questionnaire.")
        mode_box = ttk.Combobox(
            mode_section,
            textvariable=mode_var,
            values=list(VALID_MODES),
            width=20,
            state="readonly",
        )
        mode_box.pack(anchor="w")

        duration_section = _field_row("Session duration (minutes)")
        duration_input = tk.Spinbox(
            duration_section,
            from_=1,
            to=480,
            textvariable=duration_var,
            width=10,
            font=("Segoe UI", 10),
        )
        duration_input.pack(anchor="w")

        options_section = _field_row("Session options")
        options_grid = tk.Frame(options_section, bg="#ffffff")
        options_grid.pack(fill="x")

        checkbox_specs = [
            ("CSV export", csv_var),
            ("InfluxDB export", influx_var),
            ("Dual task", dual_task_var),
            ("Questionnaire", questionnaire_var),
            ("Keyboard tracking", keyboard_var),
            ("Mouse tracking", mouse_var),
            ("Notifications", notification_var),
            ("System metrics", system_metrics_var),
            ("Timer overlay", overlay_var),
        ]

        checkbox_widgets: dict[str, tk.Checkbutton] = {}
        for index, (label_text, variable) in enumerate(checkbox_specs):
            checkbox = tk.Checkbutton(
                options_grid,
                text=label_text,
                variable=variable,
                onvalue=True,
                offvalue=False,
                bg="#ffffff",
                fg="#31475d",
                activebackground="#ffffff",
                font=("Segoe UI", 10),
                anchor="w",
            )
            checkbox.grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 24), pady=4)
            checkbox_widgets[label_text] = checkbox

        data_dir_section = _field_row("Data directory")
        tk.Label(
            data_dir_section,
            text=str(self.config.data_dir),
            anchor="w",
            justify="left",
            bg="#ffffff",
            fg="#4f6478",
            font=("Segoe UI", 10),
            wraplength=600,
        ).pack(anchor="w")

        def _apply_mode_rules(*_args) -> None:
            production_mode = mode_var.get().strip().lower() == MODE_PRODUCTION
            if production_mode:
                dual_task_var.set(False)
                questionnaire_var.set(False)
            state = "disabled" if production_mode else "normal"
            checkbox_widgets["Dual task"].config(state=state)
            checkbox_widgets["Questionnaire"].config(state=state)

        mode_var.trace_add("write", _apply_mode_rules)
        _apply_mode_rules()

        note = tk.Frame(shell, bg="#edf3fb", pady=14)
        note.grid(row=3, column=0, sticky="ew")

        tk.Label(
            note,
            text="After start, this window closes and the session timer overlay appears.",
            bg="#edf3fb",
            fg="#3d5369",
            font=("Segoe UI", 10),
        ).pack(anchor="w")
        tk.Label(
            note,
            text="When the session ends, the questionnaire opens in the browser if the extension is connected; otherwise it opens in the desktop app.",
            bg="#edf3fb",
            fg="#607188",
            font=("Segoe UI", 9),
            justify="left",
            wraplength=700,
        ).pack(anchor="w", pady=(6, 0))

        buttons = tk.Frame(shell, bg="#edf3fb", pady=6)
        buttons.grid(row=4, column=0, sticky="ew")

        def _cancel() -> None:
            self._decision.confirmed = False
            self._decision.config = None
            root.destroy()

        def _confirm() -> None:
            status_var.set("")
            mode = mode_var.get().strip().lower()
            if mode not in VALID_MODES:
                status_var.set("Mode is invalid.")
                return
            try:
                duration = int(duration_var.get().strip())
            except ValueError:
                status_var.set("Session duration must be a whole number.")
                return
            if duration <= 0:
                status_var.set("Session duration must be greater than zero.")
                return
            if not csv_var.get() and not influx_var.get():
                status_var.set("Enable at least one export sink: CSV or InfluxDB.")
                return

            self._decision.config = replace(
                self.config,
                mode=mode,
                session_duration_minutes=duration,
                csv_enabled=bool(csv_var.get()),
                influx_enabled=bool(influx_var.get()),
                dual_task_enabled=False if mode == MODE_PRODUCTION else bool(dual_task_var.get()),
                questionnaire_enabled=False if mode == MODE_PRODUCTION else bool(questionnaire_var.get()),
                keyboard_tracking_enabled=bool(keyboard_var.get()),
                mouse_tracking_enabled=bool(mouse_var.get()),
                notification_tracking_enabled=bool(notification_var.get()),
                system_metrics_enabled=bool(system_metrics_var.get()),
                ui_overlay_enabled=bool(overlay_var.get()),
            )
            self._decision.confirmed = True
            root.destroy()

        tk.Label(
            buttons,
            textvariable=status_var,
            bg="#edf3fb",
            fg="#af4343",
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=(0, 10))

        tk.Button(
            buttons,
            text="Cancel",
            command=_cancel,
            bg="#d8e0ea",
            fg="#2c3d4f",
            relief="flat",
            padx=18,
            pady=9,
            font=("Segoe UI", 10),
            cursor="hand2",
        ).pack(side="right", padx=(8, 0))

        tk.Button(
            buttons,
            text="Start Session",
            command=_confirm,
            bg="#2f7dd1",
            fg="white",
            activebackground="#2366ae",
            activeforeground="white",
            relief="flat",
            padx=18,
            pady=9,
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
        ).pack(side="right")

        root.bind("<Return>", lambda _event: _confirm())
        root.bind("<Escape>", lambda _event: _cancel())
        root.protocol("WM_DELETE_WINDOW", _cancel)
        root.mainloop()
        return self._decision.config if self._decision.confirmed else None


def _show_error_dialog(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        print(message)
        return

    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Cognitive Session Launcher", message)
    root.destroy()


def run_launcher() -> int:
    try:
        config = build_default_runtime_config()
        validate_runtime_dependencies(config)
    except Exception as exc:
        _show_error_dialog(f"Startup failed:\n\n{exc}")
        return 1

    try:
        selected_config = StartupLauncher(config).show()
        if selected_config is None:
            return 0
    except Exception as exc:
        _show_error_dialog(f"Could not open the launcher window:\n\n{exc}")
        return 1

    try:
        validate_runtime_dependencies(selected_config)
    except Exception as exc:
        _show_error_dialog(f"Selected configuration is invalid:\n\n{exc}")
        return 1

    agent = CognitiveSystemAgent(selected_config)
    try:
        asyncio.run(agent.run(wait_for_user_start=False))
    except KeyboardInterrupt:
        pass
    return 0


def main() -> None:
    raise SystemExit(run_launcher())
