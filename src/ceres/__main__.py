"""Allow `python -m ceres` invocation."""

from ceres.main import main

if __name__ == "__main__":
    raise SystemExit(main())
