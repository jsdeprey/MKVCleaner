import os

os.environ["MKVCLEANER_GUI"] = "1"

from gui import main


if __name__ == "__main__":
    raise SystemExit(main())
