"""Read-only local viewer for a packed aif.json (pip install "ziplex[gui]")."""


def main() -> None:
    """`ziplex-gui` entry point: explains the missing extra instead of a traceback."""
    try:
        from .gui_server import main as server_main
    except ImportError as exc:  # flask / pywebview / watchdog not installed
        import sys

        sys.exit(f'ziplex-gui needs the optional GUI dependencies: pip install "ziplex[gui]" ({exc})')
    server_main()
