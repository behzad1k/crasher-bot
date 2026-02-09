# Crasher Bot v2

Multi-strategy crash game bot with dark-themed GUI.

## Project Structure

```
crasher-bot/
├── crasher_bot/
│   ├── __init__.py              # Package metadata
│   ├── config.py                # Config loading, validation, dataclasses
│   ├── cli.py                   # CLI entry point (headless)
│   ├── gui.py                   # GUI entry point
│   ├── core/
│   │   ├── __init__.py          # Database class
│   │   ├── driver.py            # Selenium browser automation
│   │   ├── engine.py            # Main bot loop & orchestration
│   │   ├── hotstreak.py         # Hotstreak detection & signal analysis
│   │   └── session.py           # Session recovery & backfill
│   ├── strategies/
│   │   └── __init__.py          # StrategyState & SecondaryState models
│   └── ui/
│       ├── __init__.py          # Theme constants
│       ├── app.py               # Tkinter Application class
│       └── widgets.py           # Reusable widgets (MultiplierCanvas, StrategyCard)
├── scripts/
│   ├── build.sh                 # macOS/Linux build script
│   ├── build.bat                # Windows build script
│   └── crasher_bot.spec         # PyInstaller spec
├── bot_config.example.json      # Example configuration
├── pyproject.toml
├── requirements.txt
└── README.md
```

## Setup

```bash
# 1. Clone and enter the directory
cd crasher-bot

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
.venv\Scripts\activate      # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and edit config
cp bot_config.example.json bot_config.json
# Edit bot_config.json with your credentials
```

## Running

```bash
# GUI mode
python -m crasher_bot.gui

# CLI mode (headless)
python -m crasher_bot.cli
```

## Building Executables

### Windows (.exe)

```bat
scripts\build.bat
:: Output: dist\CrasherBot.exe
```

### macOS (.dmg)

```bash
chmod +x scripts/build.sh
./scripts/build.sh

# Requires: brew install create-dmg
# Output: dist/CrasherBot.dmg
```

### Linux

```bash
chmod +x scripts/build.sh
./scripts/build.sh
# Output: dist/CrasherBot
```

## Architecture

| Module | Responsibility |
|---|---|
| `config.py` | Load/save/validate JSON config with typed dataclasses |
| `core/driver.py` | All Selenium interactions (login, bet, read multipliers) |
| `core/engine.py` | Main loop, strategy orchestration, command queue |
| `core/hotstreak.py` | Statistical pattern detection |
| `core/session.py` | Session recovery & backfill from page data |
| `core/__init__.py` | SQLite database (sessions, multipliers, bets) |
| `strategies/` | Pure state objects with bet calculation |
| `ui/` | Tkinter GUI (theme, widgets, application) |
