from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class QuestionnaireField:
    key: str
    label: str
    hint: str


QUESTIONNAIRE_FIELDS: tuple[QuestionnaireField, ...] = (
    QuestionnaireField("mental_demand", "Mental demand", "How mentally demanding was the session?"),
    QuestionnaireField("physical_demand", "Physical demand", "How physically demanding was the session?"),
    QuestionnaireField("temporal_demand", "Temporal demand", "How rushed or time-pressured did you feel?"),
    QuestionnaireField("performance", "Performance", "How satisfied are you with your performance?"),
    QuestionnaireField("effort", "Effort", "How hard did you have to work overall?"),
    QuestionnaireField("frustration", "Frustration", "How insecure, discouraged, or annoyed did you feel?"),
    QuestionnaireField("stress_self_report", "Stress", "How stressed did you feel during the session?"),
    QuestionnaireField("valence", "Valence", "How positive or pleasant did you feel?"),
    QuestionnaireField("arousal", "Arousal", "How activated or alert did you feel?"),
)


class DesktopQuestionnaireApp:
    """Topmost desktop questionnaire used when the browser extension is unavailable."""

    def collect(
        self,
        session_id: str,
        timeout_seconds: int = 900,
    ) -> Optional[dict[str, object]]:
        try:
            import tkinter as tk
        except ImportError:
            return None

        result: dict[str, object] = {}
        submitted = {"value": False}

        root = tk.Tk()
        root.title("Post-Session Questionnaire")
        root.attributes("-topmost", True)
        root.configure(bg="#eef4fb")
        root.geometry("700x760")
        root.minsize(680, 700)

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        root.geometry(f"700x760+{max(0, sw // 2 - 350)}+{max(0, sh // 2 - 380)}")

        header = tk.Frame(root, bg="#eef4fb", padx=22, pady=18)
        header.pack(fill="x")

        tk.Label(
            header,
            text="Session Questionnaire",
            bg="#eef4fb",
            fg="#203243",
            font=("Segoe UI", 20, "bold"),
        ).pack(anchor="w")

        tk.Label(
            header,
            text=f"Session: {session_id}",
            bg="#eef4fb",
            fg="#5f7285",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(4, 0))

        tk.Label(
            header,
            text="Please rate the session before closing this window.",
            bg="#eef4fb",
            fg="#364b60",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(8, 0))

        outer = tk.Frame(root, bg="#eef4fb", padx=18, pady=0)
        outer.pack(fill="both", expand=True, pady=(0, 14))

        canvas = tk.Canvas(outer, bg="#eef4fb", highlightthickness=0)
        scrollbar = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        content = tk.Frame(canvas, bg="#ffffff", padx=18, pady=18)

        content.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas_window = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(canvas_window, width=event.width),
        )

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

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        root.bind_all("<MouseWheel>", _mousewheel_scroll, add="+")
        root.bind_all("<Button-4>", _mousewheel_scroll, add="+")
        root.bind_all("<Button-5>", _mousewheel_scroll, add="+")

        variables: dict[str, tk.IntVar] = {}
        outputs: dict[str, tk.Label] = {}
        slider_bg = "#ffffff"

        for field in QUESTIONNAIRE_FIELDS:
            card = tk.Frame(content, bg=slider_bg, pady=8)
            card.pack(fill="x", pady=4)

            tk.Label(
                card,
                text=field.label,
                bg=slider_bg,
                fg="#223346",
                font=("Segoe UI", 11, "bold"),
                anchor="w",
            ).pack(fill="x")

            tk.Label(
                card,
                text=field.hint,
                bg=slider_bg,
                fg="#607188",
                font=("Segoe UI", 9),
                anchor="w",
                justify="left",
                wraplength=600,
            ).pack(fill="x", pady=(2, 8))

            row = tk.Frame(card, bg=slider_bg)
            row.pack(fill="x")

            tk.Label(row, text="0", bg=slider_bg, fg="#607188", width=3).pack(side="left")

            var = tk.IntVar(value=50)
            variables[field.key] = var
            scale = tk.Scale(
                row,
                from_=0,
                to=100,
                orient="horizontal",
                resolution=1,
                showvalue=False,
                variable=var,
                bg=slider_bg,
                highlightthickness=0,
                troughcolor="#d9e6f4",
                activebackground="#5d90d7",
                fg="#223346",
            )
            scale.pack(side="left", fill="x", expand=True, padx=8)

            tk.Label(row, text="100", bg=slider_bg, fg="#607188", width=4).pack(side="left")
            output = tk.Label(
                row,
                text="50",
                width=4,
                bg="#edf4ff",
                fg="#1f4f85",
                font=("Segoe UI", 10, "bold"),
            )
            output.pack(side="left", padx=(10, 0))
            outputs[field.key] = output

            scale.configure(
                command=lambda v, lbl=output: lbl.config(text=str(int(float(v))))
            )

        footer = tk.Frame(root, bg="#eef4fb", padx=20, pady=16)
        footer.pack(fill="x")

        status_var = tk.StringVar(value="")

        tk.Label(
            footer,
            textvariable=status_var,
            bg="#eef4fb",
            fg="#af4343",
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        def _close_without_submit() -> None:
            status_var.set("Questionnaire closed without submission.")
            root.destroy()

        def _submit() -> None:
            submitted["value"] = True
            result.update(
                {
                    "session_id": session_id,
                    **{field.key: int(variables[field.key].get()) for field in QUESTIONNAIRE_FIELDS},
                }
            )
            root.destroy()

        tk.Button(
            footer,
            text="Submit",
            command=_submit,
            bg="#2f7dd1",
            fg="white",
            activebackground="#2366ae",
            activeforeground="white",
            relief="flat",
            padx=20,
            pady=8,
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
        ).pack(side="right")

        root.protocol("WM_DELETE_WINDOW", _close_without_submit)
        if timeout_seconds > 0:
            root.after(int(timeout_seconds * 1000), _close_without_submit)
        root.mainloop()

        return result if submitted["value"] else None
