"""CLI entry point – runs the bot without GUI."""

import logging
import sys

from crasher_bot.config import BotConfig
from crasher_bot.core.engine import BotEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("crasher_bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "./bot_config.json"
    try:
        cfg = BotConfig.from_file(config_path)
        errors = cfg.validate()
        if errors:
            for e in errors:
                print(f"  ERROR: {e}")
            sys.exit(1)
        bot = BotEngine(cfg)
        bot.run()
    except FileNotFoundError:
        print(f"Config file not found: {config_path}")
        sys.exit(1)


if __name__ == "__main__":
    main()
