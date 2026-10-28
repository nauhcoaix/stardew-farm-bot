"""``python -m stardew_agent`` 等同于 ``stardew-agent``。"""

from .cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
