# EveryDay — Daily Planner for Windows

A desktop task manager for Windows: custom tasks, recurring / one-off tasks,
completion tracking, ICS calendar, preset templates, a always-on-top floating
window, system tray, and launch-at-login.

> Current version **v1.0.0** (V1 scope).

---

## 1. Quick start (5 minutes)

### Requirements

| Item | Requirement |
|---|---|
| OS | Windows 10 1809+ (Windows 11 works) |
| Python | 3.9+ (3.11 / 3.12 recommended) |
| IDE | PyCharm 2023+ (Community edition is fine) |

### Install

```powershell
cd EveryDay
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

The only runtime dependency is `PySide6` (Qt6). All business logic
(`models` / `db` / `services`) uses the Python standard library only, so the
unit-test suite runs even without Qt installed.

### Run

```powershell
python main.py
```

### Run in PyCharm

1. `File → Open` the `EveryDay` folder
2. `Settings → Project → Python Interpreter`: create a venv and install `requirements.txt`
3. Right-click `main.py → Run 'main'`

### Command-line options

| Option | Effect |
|---|---|
| `--minimized` | Start silently in the tray (+ floating window). Used by launch-at-login |
| `--data-dir <path>` | Override the data directory (useful for debugging) |
| `--log-level DEBUG` | Override the log level |
| `--version` | Print the version |

### Tests

```powershell
python -m unittest discover -s tests -v
```

### Verification tools

Three dependency-free check scripts ship with the repo (stdlib + PySide6 only):

| Command | Purpose |
|---|---|
| `python -m unittest discover -s tests` | 230 unit + UI smoke + end-to-end startup tests |
| `python tools/coverage_report.py` | Coverage via stdlib `trace` (no `pytest-cov` needed) |
| `python tools/smoke_startup.py` | Boots the real app and verifies on-disk artefacts |

Measured: **230 tests pass**, core-service coverage **86.2%**,
end-to-end smoke 12/12 and 14/14 checks green. See
[docs/acceptance-report.md](docs/acceptance-report.md).

---

## 2. Features

| ID | Feature | Notes |
|---|---|---|
| F1 | Launch at login | Writes to the current-user `Run` registry key; no admin rights |
| F2 | Floating window | Frameless, translucent, always-on-top, hidden from the taskbar; draggable with remembered position; falls back to the primary screen |
| F3 | Custom tasks | Create / edit / delete; title required, max 100 characters |
| F4 | Completion state | Checkbox persists immediately; one-off tasks disappear when done, recurring ones reset the next day |
| F5 | Recurring / one-off | Once / Daily / Weekly / Monthly; midnight rollover handled by a timer |
| F6 | Live calendar | Reads a local `.ics` file or a network subscription URL |
| F7 | Preset templates | 10 built-in templates, one-click task creation, full CRUD |

Also included: system tray, startup database backup (keeps 5), daily rolling
logs (keeps 7 days), three-layer global exception handling, SQLite WAL mode.

---

## 3. Project layout

```
EveryDay/
├── main.py                     Entry point (run this in PyCharm)
├── requirements.txt            Runtime dependency
├── pyproject.toml              Packaging + pytest / coverage / ruff config
├── config/appsettings.example.json   Config template
├── migrations/001_initial.sql  Schema + default templates
├── docs/                       Architecture, acceptance report, known issues
├── src/everyday/
│   ├── app.py                  Wiring (logging → db → migrate → backup → UI)
│   ├── models/                 TaskItem / PresetTemplate / AppSettings
│   ├── db/                     SQLite connection, idempotent migrations, repositories
│   ├── services/               Tasks, calendar, ICS parser, autostart, presets, backup
│   └── ui/                     PySide6 windows, dialogs, tray, code-drawn icons
└── tests/                      Unit tests on in-memory SQLite (no DB mocks)
```

Layering: `ui → services → db → models`. Only `ui` depends on Qt.

---

## 4. Where data lives

Data is separated from the program directory so upgrades never lose it:

```
%LOCALAPPDATA%\EveryDay\
├── everyday.db               SQLite database (WAL)
├── appsettings.json          Runtime configuration
├── backups\                  Startup backups, newest 5 kept
└── logs\                     Daily rolling logs, 7 days kept
```

---

## 5. FAQ

**Tray icon seems missing.** Windows tucks new icons into the overflow area —
drag it onto the taskbar to pin it.

**Closing the main window does not exit.** By design: closing minimises to the
tray. Use the tray menu's *Exit* item to quit.

**Calendar subscription failed to load.** The previous successful data is kept
and a message is shown; nothing is cleared. Make sure the URL returns raw ICS.

**Launch at login has no effect.** Check Task Manager → Startup; Windows may
have disabled the entry. It runs `pythonw.exe main.py --minimized`, so moving
the project folder requires re-ticking the box in Settings.

---

## 6. License

[MIT](LICENSE)

---

中文文档：[README.md](README.md)
