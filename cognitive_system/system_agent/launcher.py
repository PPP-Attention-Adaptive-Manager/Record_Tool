from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Optional

from .capability_probe import CapabilityStatus, SystemCapabilityProbe
from .config import (
    DUAL_TASK_INTERVAL_RANDOM,
    DUAL_TASK_INTERVAL_REGULAR,
    MODE_PRODUCTION,
    RuntimeConfig,
    VALID_DUAL_TASK_INTERVAL_MODES,
    VALID_MODES,
    build_default_runtime_config,
)
from .dependency_validation import validate_runtime_dependencies
from .main import CognitiveSystemAgent


@dataclass
class LaunchDecision:
    confirmed: bool
    config: RuntimeConfig | None = None


class StartupLauncher:
    """Small default-config confirmation window for production-style launch."""

    def __init__(
        self,
        config: RuntimeConfig,
        capabilities: Optional[list[CapabilityStatus]] = None,
    ) -> None:
        self.config = config
        self.capabilities = capabilities or []
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

        # ── System Status Card ──────────────────────────────────────────────
        if self.capabilities:
            status_frame = tk.Frame(shell, bg="#edf3fb")
            status_frame.grid(row=2, column=0, sticky="ew", pady=(0, 12))

            tk.Label(
                status_frame,
                text="System Status",
                bg="#edf3fb",
                fg="#203243",
                font=("Segoe UI", 12, "bold"),
            ).pack(anchor="w")

            status_inner = tk.Frame(
                status_frame,
                bg="#ffffff",
                padx=14,
                pady=10,
                highlightbackground="#d5deea",
                highlightthickness=1,
            )
            status_inner.pack(fill="x", pady=(4, 0))

            for cap in self.capabilities:
                row = tk.Frame(status_inner, bg="#ffffff")
                row.pack(fill="x", pady=2)

                icon = "\u2713" if cap.severity == "ok" else ("\u26a0" if cap.severity == "warning" else "\u2717")
                color = "#2a8a3a" if cap.severity == "ok" else ("#b8860b" if cap.severity == "warning" else "#af4343")

                tk.Label(
                    row,
                    text=icon,
                    bg="#ffffff",
                    fg=color,
                    font=("Segoe UI", 11, "bold"),
                    width=2,
                    anchor="w",
                ).pack(side="left")

                tk.Label(
                    row,
                    text=f"{cap.feature}:  {cap.detail}",
                    bg="#ffffff",
                    fg="#31475d",
                    font=("Segoe UI", 9),
                    wraplength=620,
                    justify="left",
                ).pack(side="left", fill="x", expand=True)

                if cap.guidance:
                    guidance_label = tk.Label(
                        status_inner,
                        text=f"   \u2192 {cap.guidance}",
                        bg="#ffffff",
                        fg="#8a6d3b",
                        font=("Segoe UI", 9, "italic"),
                        wraplength=600,
                        justify="left",
                    )
                    guidance_label.pack(anchor="w", pady=(0, 4))

            # Adjust grid row for the config card
            shell.grid_rowconfigure(3, weight=1)
            card_shell = tk.Frame(shell, bg="#edf3fb")
            card_shell.grid(row=3, column=0, sticky="nsew")
        else:
            shell.grid_rowconfigure(2, weight=1)
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

        def _mousewheel_scroll(event) -> str | None:
            x1 = canvas.winfo_rootx()
            y1 = canvas.winfo_rooty()
            x2 = x1 + canvas.winfo_width()
            y2 = y1 + canvas.winfo_height()
            if not (x1 <= event.x_root <= x2 and y1 <= event.y_root <= y2):
                return None

            content_bounds = canvas.bbox("all")
            if not content_bounds or content_bounds[3] <= canvas.winfo_height():
                return "break"

            if getattr(event, "num", None) == 4:
                units = -3
            elif getattr(event, "num", None) == 5:
                units = 3
            else:
                delta = getattr(event, "delta", 0)
                if not delta:
                    return "break"
                units = -int(delta / 120)
                if units == 0:
                    units = -1 if delta > 0 else 1

            canvas.yview_scroll(units, "units")
            return "break"

        root.bind_all("<MouseWheel>", _mousewheel_scroll, add="+")
        root.bind_all("<Button-4>", _mousewheel_scroll, add="+")
        root.bind_all("<Button-5>", _mousewheel_scroll, add="+")

        mode_var = tk.StringVar(value=self.config.mode)
        duration_var = tk.StringVar(value=str(self.config.session_duration_minutes))
        csv_var = tk.BooleanVar(value=self.config.csv_enabled)
        influx_var = tk.BooleanVar(value=self.config.influx_enabled)
        dual_task_var = tk.BooleanVar(value=self.config.dual_task_enabled)
        interval_mode_default = self.config.dual_task_interval_mode
        if interval_mode_default not in VALID_DUAL_TASK_INTERVAL_MODES:
            interval_mode_default = DUAL_TASK_INTERVAL_REGULAR
        dual_interval_mode_var = tk.StringVar(value=interval_mode_default)
        dual_interval_var = tk.StringVar(value=str(self.config.dual_task_interval_seconds))
        dual_random_min_var = tk.StringVar(value=str(self.config.dual_task_random_min_seconds))
        dual_random_max_var = tk.StringVar(value=str(self.config.dual_task_random_max_seconds))
        dual_timeout_var = tk.StringVar(value=str(self.config.dual_task_timeout_seconds))
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

        timing_section = _field_row(
            "Dual task timing",
            "Use a regular fixed interval or a random delay between two probes.",
        )
        timing_grid = tk.Frame(timing_section, bg="#ffffff")
        timing_grid.pack(fill="x")
        timing_grid.grid_columnconfigure(1, weight=1)

        tk.Label(
            timing_grid,
            text="Interval mode",
            bg="#ffffff",
            fg="#4f6478",
            font=("Segoe UI", 10),
        ).grid(row=0, column=0, sticky="w", padx=(0, 14), pady=4)
        dual_task_mode_box = ttk.Combobox(
            timing_grid,
            textvariable=dual_interval_mode_var,
            values=list(VALID_DUAL_TASK_INTERVAL_MODES),
            width=14,
            state="readonly",
        )
        dual_task_mode_box.grid(row=0, column=1, sticky="w", pady=4)

        def _timing_spinbox_row(
            row: int,
            label_text: str,
            variable: tk.StringVar,
            min_value: int,
        ) -> tk.Spinbox:
            tk.Label(
                timing_grid,
                text=label_text,
                bg="#ffffff",
                fg="#4f6478",
                font=("Segoe UI", 10),
            ).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=4)
            spinbox = tk.Spinbox(
                timing_grid,
                from_=min_value,
                to=86400,
                textvariable=variable,
                width=10,
                font=("Segoe UI", 10),
            )
            spinbox.grid(row=row, column=1, sticky="w", pady=4)
            return spinbox

        dual_interval_input = _timing_spinbox_row(1, "Regular interval (sec)", dual_interval_var, 5)
        dual_random_min_input = _timing_spinbox_row(2, "Random minimum (sec)", dual_random_min_var, 5)
        dual_random_max_input = _timing_spinbox_row(3, "Random maximum (sec)", dual_random_max_var, 5)
        dual_timeout_input = _timing_spinbox_row(4, "Probe timeout (sec)", dual_timeout_var, 1)

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

        def _sync_dual_task_controls(*_args) -> None:
            production_mode = mode_var.get().strip().lower() == MODE_PRODUCTION
            dual_enabled = bool(dual_task_var.get()) and not production_mode
            interval_mode = dual_interval_mode_var.get().strip().lower()
            if interval_mode not in VALID_DUAL_TASK_INTERVAL_MODES:
                interval_mode = DUAL_TASK_INTERVAL_REGULAR
                dual_interval_mode_var.set(interval_mode)

            dual_task_mode_box.config(state="readonly" if dual_enabled else "disabled")
            dual_interval_input.config(
                state="normal"
                if dual_enabled and interval_mode == DUAL_TASK_INTERVAL_REGULAR
                else "disabled"
            )
            random_state = (
                "normal"
                if dual_enabled and interval_mode == DUAL_TASK_INTERVAL_RANDOM
                else "disabled"
            )
            dual_random_min_input.config(state=random_state)
            dual_random_max_input.config(state=random_state)
            dual_timeout_input.config(state="normal" if dual_enabled else "disabled")

        def _apply_mode_rules(*_args) -> None:
            production_mode = mode_var.get().strip().lower() == MODE_PRODUCTION
            if production_mode:
                dual_task_var.set(False)
                questionnaire_var.set(False)
            state = "disabled" if production_mode else "normal"
            checkbox_widgets["Dual task"].config(state=state)
            checkbox_widgets["Questionnaire"].config(state=state)
            _sync_dual_task_controls()

        mode_var.trace_add("write", _apply_mode_rules)
        dual_task_var.trace_add("write", _sync_dual_task_controls)
        dual_interval_mode_var.trace_add("write", _sync_dual_task_controls)
        _apply_mode_rules()

        note_row = 4 if self.capabilities else 3
        note = tk.Frame(shell, bg="#edf3fb", pady=14)
        note.grid(row=note_row, column=0, sticky="ew")

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
        buttons.grid(row=note_row + 1, column=0, sticky="ew")

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

            production_mode = mode == MODE_PRODUCTION
            dual_enabled = bool(dual_task_var.get()) and not production_mode
            dual_interval_mode = dual_interval_mode_var.get().strip().lower()
            dual_interval = max(5, self.config.dual_task_interval_seconds)
            dual_random_min = max(5, self.config.dual_task_random_min_seconds)
            dual_random_max = max(dual_random_min, self.config.dual_task_random_max_seconds)
            dual_timeout = max(1, self.config.dual_task_timeout_seconds)

            if dual_enabled:
                if dual_interval_mode not in VALID_DUAL_TASK_INTERVAL_MODES:
                    status_var.set("Dual task interval mode is invalid.")
                    return
                try:
                    dual_timeout = int(dual_timeout_var.get().strip())
                except ValueError:
                    status_var.set("Dual task timeout must be a whole number.")
                    return
                if dual_timeout < 1:
                    status_var.set("Dual task timeout must be at least 1 second.")
                    return

                if dual_interval_mode == DUAL_TASK_INTERVAL_RANDOM:
                    try:
                        dual_random_min = int(dual_random_min_var.get().strip())
                        dual_random_max = int(dual_random_max_var.get().strip())
                    except ValueError:
                        status_var.set("Random interval bounds must be whole numbers.")
                        return
                    if dual_random_min < 5:
                        status_var.set("Random minimum interval must be at least 5 seconds.")
                        return
                    if dual_random_max < dual_random_min:
                        status_var.set("Random maximum interval must be greater than or equal to the minimum.")
                        return
                else:
                    try:
                        dual_interval = int(dual_interval_var.get().strip())
                    except ValueError:
                        status_var.set("Regular interval must be a whole number.")
                        return
                    if dual_interval < 5:
                        status_var.set("Regular interval must be at least 5 seconds.")
                        return

            self._decision.config = replace(
                self.config,
                mode=mode,
                session_duration_minutes=duration,
                csv_enabled=bool(csv_var.get()),
                influx_enabled=bool(influx_var.get()),
                dual_task_enabled=dual_enabled,
                questionnaire_enabled=False if production_mode else bool(questionnaire_var.get()),
                dual_task_interval_seconds=dual_interval,
                dual_task_interval_mode=dual_interval_mode,
                dual_task_random_min_seconds=dual_random_min,
                dual_task_random_max_seconds=dual_random_max,
                dual_task_timeout_seconds=dual_timeout,
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
        probe = SystemCapabilityProbe(config)
        capabilities = probe.probe()
    except Exception:
        capabilities = []

    try:
        selected_config = StartupLauncher(config, capabilities).show()
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
