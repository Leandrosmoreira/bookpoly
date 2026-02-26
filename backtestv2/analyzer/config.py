"""Analyzer configuration."""

from dataclasses import dataclass


@dataclass
class AnalyzerConfig:
    results_dir: str = "backtestv2/data/results"
    output_dir: str = "backtestv2/data/reports"
    trades_csv: str | None = None
    dpi: int = 150
    style: str = "dark_background"
    figsize: tuple = (12, 6)
