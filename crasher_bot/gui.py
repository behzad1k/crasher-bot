"""GUI entry point."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()

    from crasher_bot.config import get_default_config_path
    from crasher_bot.ui.app import Application

    config_path = sys.argv[1] if len(sys.argv) > 1 else get_default_config_path()
    app = Application(config_path)
    app.run()
