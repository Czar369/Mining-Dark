"""
Run the dashboard directly:

    python -m mining_dark.gui --simulate
    python -m mining_dark.gui --theme amber --autostart

The `mining-dark gui` subcommand is the preferred entry point; this module
exists so the GUI can be launched without the Typer CLI in the way.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mining_dark.config.settings import ConfigError, load_settings
from mining_dark.gui import GUIUnavailableError, run_gui
from mining_dark.utils.logger import setup_logger


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mining_dark.gui",
        description="Mining-Dark - painel grafico (Dear PyGui).",
    )
    parser.add_argument("--simulate", action="store_true",
                        help="usa dados simulados (dispensa o banco UTXO)")
    parser.add_argument("--config", "-c", type=Path, default=None,
                        help="arquivo de configuracao (padrao: config.yaml)")
    parser.add_argument("--theme", default=None, choices=("matrix", "amber", "ice"),
                        help="paleta de cores (padrao: config.yaml)")
    parser.add_argument("--lang", default=None, choices=("pt", "en"),
                        help="idioma da interface (padrao: config.yaml)")
    parser.add_argument("--font-scale", type=float, default=None,
                        help="multiplicador do tamanho das fontes (telas HiDPI)")
    parser.add_argument("--autostart", action="store_true",
                        help="inicia o scan assim que a janela abrir")
    parser.add_argument("--screenshot", type=Path, default=None,
                        help="grava um PNG do painel e continua rodando")
    parser.add_argument("--screenshot-frames", type=int, default=120,
                        help="frame em que o PNG e gravado (padrao: 120)")
    parser.add_argument("--max-frames", type=int, default=0,
                        help="encerra apos N frames (0 = sem limite)")

    args = parser.parse_args(argv)

    # The Typer CLI configures logging in its root callback; this entry point
    # bypasses that, so it has to do it itself - otherwise loguru has no file
    # sink at all and the session's STREAM LOG goes nowhere but the screen.
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    setup_logger(
        level=settings.logging.level,
        logs_dir=settings.logging.resolved_logs_dir(),
        rotation=settings.logging.rotation,
        retention=settings.logging.retention,
    )

    try:
        run_gui(
            simulate=args.simulate,
            settings=settings,
            config_path=args.config,
            palette=args.theme,
            language=args.lang,
            font_scale=args.font_scale,
            autostart=args.autostart,
            screenshot=args.screenshot,
            screenshot_frames=args.screenshot_frames,
            max_frames=args.max_frames,
        )
    except GUIUnavailableError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
