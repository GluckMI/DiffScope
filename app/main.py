"""DiffScope 应用入口：命令行参数走 CLI，其余启动图形界面。"""

from __future__ import annotations

import os
import sys

CLI_ONLY = {
    "--diff", "--script", "--merge", "--snapshot", "--json", "--quiet",
    "--report", "--allow-write", "--favor", "--exclude", "--ignore-case",
    "--ignore-whitespace", "--timestamp-only", "--version", "-h", "--help",
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(arg.split("=", 1)[0] in CLI_ONLY for arg in argv):
        from cli import main as cli_main

        return cli_main(argv)

    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    from .ui.main_window import launch

    return launch(argv)


if __name__ == "__main__":
    sys.exit(main())
