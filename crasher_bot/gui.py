"""GUI entry point."""

import sys

from crasher_bot.ui.app import Application


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "./bot_config.json"
    app = Application(config_path)
    app.run()


if __name__ == "__main__":
    main()
