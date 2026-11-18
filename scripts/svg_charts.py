"""Shared vector layout derived from the supplied status-cost reference SVG."""

import re
from dataclasses import dataclass
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKGROUND = "#F3F2EE"
INK = "#15191A"
MUTED = "#565C5A"
GRID = "#D6D8D1"
ACCENT = "#8A1C2E"
COLORS = ("#BCC0B8", ACCENT, INK)
FONTS = {
    "heading": "Archivo,'Helvetica Neue',Arial,sans-serif",
    "body": "'IBM Plex Sans','Helvetica Neue',Arial,sans-serif",
    "mono": "'IBM Plex Mono',Menlo,Consolas,monospace",
}


@dataclass(frozen=True)
class Row:
    """One measured condition, without any display-induced changes to its values."""

    label: str
    components: tuple[float, ...]
    change: float
    highlight: bool = False
    seconds: float | None = None


class Chart:
    """Render accessible horizontal stacked bars using the reference's embedded fonts."""

    def __init__(self, title: str, description: str, height: int, language: str) -> None:
        reference = (ROOT / "benchmarks/status-cost-composition.svg").read_text()
        match = re.search(r"<style>(.*?)</style>", reference, re.DOTALL)
        if match is None:
            raise ValueError("Reference SVG has no embedded font styles")
        style = match.group(1)
        self.language = language
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="{height}" '
            f'viewBox="0 0 1600 {height}" role="img" aria-labelledby="ttl dsc" '
            f'xml:lang="{language}">',
            f'<title id="ttl">{escape(title)}</title>',
            f'<desc id="dsc">{escape(description)}</desc>',
            f"<style>{style}</style>",
            f'<rect width="1600" height="{height}" fill="{BACKGROUND}"/>',
        ]

    def number(self, value: float, decimals: int) -> str:
        """Localize decimal separators without changing rounding precision."""
        text = f"{value:.{decimals}f}"
        return text.replace(".", ",") if self.language == "pt-br" else text

    def money(self, value: float, decimals: int = 4) -> str:
        """Use USD as declared in the chart, retaining precision for small calls."""
        return "$" + self.number(value, decimals)

    def text(
        self,
        x: float,
        y: float,
        value: str,
        size: int = 19,
        family: str = "body",
        weight: int = 400,
        color: str = INK,
        anchor: str = "start",
        tracking: str | None = None,
    ) -> None:
        """Append escaped text with the same typographic hierarchy as the reference."""
        style = f"font-family:{FONTS[family]};font-size:{size}px;font-weight:{weight}"
        if tracking:
            style += f";letter-spacing:{tracking}"
        self.parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" '
            f'fill="{color}" style="{style}">{escape(value)}</text>'
        )

    def rect(self, x: float, y: float, width: float, height: float, color: str) -> None:
        """Draw a rectangle at its true scaled width, including very small components."""
        self.parts.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{width:.2f}" '
            f'height="{height:.2f}" fill="{color}"/>'
        )

    def line(
        self, x1: float, y1: float, x2: float, y2: float, color: str = GRID, width: float = 1
    ) -> None:
        """Draw understated rules and shared-scale grid lines."""
        self.parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" '
            f'y2="{y2:.1f}" stroke="{color}" stroke-width="{width}"/>'
        )

    def header(
        self, eyebrow: str, title: str, subtitle: str, legends: tuple[str, ...], unit: str
    ) -> None:
        """Match the reference's title, legend and top rule positions."""
        self.text(80, 98, eyebrow, 18, "mono", 500, ACCENT, tracking="0.12em")
        self.text(80, 162, title, 52, "heading", 700, tracking="-0.02em")
        self.text(80, 208, subtitle, 24, color=MUTED)
        x = 80.0
        for label, color in zip(legends, COLORS, strict=False):
            self.rect(x, 244, 22, 22, color)
            self.text(x + 32, 262, label, 19, weight=500)
            x += len(label) * 10.3 + 76
        self.text(1520, 262, unit, 19, color=MUTED, anchor="end")
        self.line(80, 293, 1520, 293, INK, 2)

    def panel(
        self,
        provider: str,
        date: str,
        y: int,
        rows: list[Row],
        maximum: float,
        ticks: tuple[float, ...],
        total_label: str,
        change_label: str,
        baseline_label: str,
        short_labels: tuple[str, ...],
        time_label: str | None = None,
        show_ticks: bool = False,
        decimals: int = 4,
    ) -> None:
        """Draw one provider; cost, time and changes occupy separate columns."""
        bar_x, bar_end = 400, 1060 if time_label else 1280
        total_x = 1100 if time_label else 1316
        self.text(80, y, provider, 28, "heading", 600, tracking="-0.01em")
        self.text(bar_x, y, f"RUN {date}", 15, "mono", color=MUTED, tracking="0.1em")
        self.text(total_x, y, total_label, 15, "mono", color=MUTED, tracking="0.1em")
        if time_label:
            self.text(1350, y, time_label, 15, "mono", color=MUTED, anchor="end", tracking="0.1em")
        self.text(1520, y, change_label, 15, "mono", color=MUTED, anchor="end", tracking="0.1em")
        grid_top, grid_bottom = y + 18, y + 18 + len(rows) * 70 - 6
        for tick in ticks:
            x = bar_x + tick / maximum * (bar_end - bar_x)
            self.line(x, grid_top, x, grid_bottom, width=1.5 if tick == 0 else 1)
            if show_ticks:
                self.text(
                    x,
                    grid_bottom + 26,
                    self.money(tick, 2),
                    16,
                    "mono",
                    color=MUTED,
                    anchor="middle",
                )
        for index, row in enumerate(rows):
            top = y + 26 + index * 70
            self.text(80, top + 31, row.label, 21, weight=600 if row.highlight else 400)
            x = float(bar_x)
            outside = []
            for component, color, short in zip(
                row.components, COLORS[: len(row.components)], short_labels, strict=True
            ):
                width = component / maximum * (bar_end - bar_x)
                if component:
                    self.rect(x, top, width, 48, color)
                    label = self.money(component, 4 if time_label else 3)
                    if width >= 94:
                        self.text(
                            x + 14,
                            top + 30,
                            label,
                            16,
                            "mono",
                            500,
                            INK if color == COLORS[0] else "#FFFFFF",
                        )
                    else:
                        outside.append(f"{short} {label}")
                    if x > bar_x:
                        self.rect(x - 1, top, 2, 48, BACKGROUND)
                    x += width
            if outside:
                self.text(x + 14, top + 30, " · ".join(outside), 15, "mono", color=MUTED)
            self.text(
                total_x,
                top + 32,
                self.money(sum(row.components), decimals),
                22,
                "mono",
                500 if row.highlight else 400,
            )
            if row.seconds is not None:
                self.text(
                    1350, top + 31, self.number(row.seconds, 2) + " s", 19, "mono", anchor="end"
                )
            if row.change == 0:
                self.text(
                    1520,
                    top + 31,
                    baseline_label,
                    17,
                    "mono",
                    color=MUTED,
                    anchor="end",
                    tracking="0.04em",
                )
            else:
                sign = "\N{MINUS SIGN}" if row.change < 0 else "+"
                change = sign + self.number(abs(row.change) * 100, 1) + "%"
                if row.highlight:
                    self.rect(1416, top + 6, 104, 36, ACCENT)
                self.text(
                    1508,
                    top + 32,
                    change,
                    21,
                    "mono",
                    color="#FFFFFF" if row.highlight else INK,
                    anchor="end",
                )

    def footer(self, y: int, line1: str, line2: str) -> None:
        """Retain the reference's footnotes and author signature."""
        self.line(80, y - 35, 1520, y - 35, INK, 2)
        self.text(80, y, line1, 16, color=MUTED)
        self.text(80, y + 26, line2, 16, color=MUTED)
        self.text(1520, y, "BRUNO VICCO", 16, "mono", anchor="end", tracking="0.06em")
        self.text(
            1520,
            y + 26,
            "github.com/brunovicco/agent-context-bench",
            16,
            "mono",
            color=MUTED,
            anchor="end",
        )

    def save(self, output: Path) -> None:
        """Write a standalone SVG, retaining the supplied embedded font faces."""
        output.write_text("\n".join([*self.parts, "</svg>"]) + "\n", encoding="utf-8")
