# 7d2d Mod Manager

> Formerly known as 7d2d Mod Analyzer

A comprehensive Python tool for managing, conflict detection, and load-order optimization for **7 Days to Die** mods.

---

## 📋 Quick Review Guide

**For Security & Whitelist Reviewers:**
- **Start here:** [main.py](main.py) (root entry point that loads all core logic from /src)
- **Core logic:** [`/src`](src/) contains ALL 99 .py + 30 .cs source files
- **GUI entry:** [`src/gui/app.py`](src/gui/app.py) (Tkinter-based interface)
- **Tests:** [`/tests`](tests/) contains 35 full test suite files
- **Build:** [`app.spec`](app.spec) (PyInstaller specification - no obfuscation)

**Key facts for approval:**
- ✅ **NO external dependencies** — Standard library only
- ✅ **NO obfuscation** — All code is plain, readable Python/C#
- ✅ **NO network access** — Fully offline operation
- ✅ **Fully traceable** — Every operation visible in logs

---

## Description

This tool helps modpack creators and players manage large mod collections by:
- **Scanning** mod metadata and detecting conflicts
- **Resolving** load order issues automatically
- **Optimizing** mod dependencies for stability
- **Reporting** detailed conflict analysis
- **Providing transparency** with complete operation logging

Safe for antivirus scanning. Opens only the mod folder you select—no internet access, registry access, or system file modifications.

---

## 🔍 Source Code Location

**ALL source code is located in the `/src` folder:**
- ✅ Complete, un-obfuscated Python source code (99 .py files)
- ✅ Complete C# WinForms sources (30 .cs files)  
- ✅ **ZERO external runtime dependencies** (standard library only)
- ✅ Build artifacts in `/build` folder (NOT source code)
- ✅ Tests in `/tests` folder (fully traceable)

**The code is transparent and available for full security review.**

---

## 📁 Project Structure

```
7d2d-mod-manager/
│
├── main.py                          # ⭐ PRIMARY ENTRY POINT
├── __version__.py                   # Version metadata (imported by all modules)
├── app.spec                         # PyInstaller build specification
├── pyproject.toml                   # Python package metadata
│
├── src/                             # 🔴 ALL SOURCE CODE (99 .py + 30 .cs files)
│   ├── gui/
│   │   └── app.py                   # GUI entry point (Tkinter UI)
│   ├── logic/                       # Business logic (22 modules)
│   ├── engines/                     # Analysis engines (10 modules)
│   ├── deployment/                  # Deployment operations (6 modules)
│   ├── scanner/                     # Mod scanning (5 modules)
│   ├── models/                      # Data models (3 modules)
│   ├── mock_deploy/                 # Simulation/testing (4 modules)
│   ├── exporter/                    # Export functionality (2 modules)
│   ├── xml_analyzer/                # XML utilities
│   ├── path_safety.py               # Security utilities
│   └── winforms/                    # C# WinForms project (30 .cs files)
│
├── tests/                           # Test suite (35 test files)
├── scripts/                         # Utility scripts (PowerShell, Python)
├── data/                            # Configuration & rules
│   ├── rules.json                   # Conflict resolution rules
│   └── mod_metadata.json            # Detected mod metadata
│
├── build/                           # Build artifacts (compiled output - NOT source)
├── LICENSE                          # MIT License
├── SECURITY.md                      # Security & privacy information
├── README.md                         # This file
└── [config files]                   # config.json, settings.json, etc.
```

---

## Dependencies

- **Python runtime:** Python 3.10+ only
- **External packages:** **NONE** — Uses only standard library
- **Build tool:** PyInstaller (for `.exe` compilation; not required for source execution)

---

## ⚡ Entry Points

| Entry Point | Purpose | Language | Required? |
|---|---|---|---|
| **main.py** | Primary application entry | Python | ✅ Yes |
| **src/gui/app.py** | GUI interface (Tkinter) | Python | ✅ Default |
| **src/winforms/Program.cs** | Alternative .NET UI | C# | ⚠️ Optional (.NET 7+) |

### Running the Application

```bash
# Start the GUI (primary entry)
python main.py

# Or run GUI directly
python src/gui/app.py

# For WinForms UI (.NET 7 required)
cd src/winforms/7dtd-mod-loadorder-manager && dotnet run
```

---

## Requirements

### For Running From Source
- **Python 3.10, 3.11, or 3.12** (tested on all three versions)
- **No external packages required** — Standard library only
- **Tkinter** (bundled with Python)

### For Building Standalone Executable
- Everything above, plus:
- **PyInstaller** (install only when building: `pip install pyinstaller`)

### For Windows Pre-Built Executable (dist/7d2d-mod-manager.exe)
- **Windows 10 or 11** (64-bit)
- Python **NOT** required
- No dependencies to install

---

## ✨ Features

- **Conflict Detection**: Identifies and categorizes mod conflicts
- **Load Order Optimization**: Reorders mods for compatibility
- **Transparent Logging**: All operations logged in the GUI for verification
- **Standalone Executable**: Pre-built `.exe` available (or build from source)
- **Safe Operation**: Read-only by default; write operations only after user confirmation
- **Windows Optimized**: Built for Windows 10/11

---

## 🔐 Security & Transparency

### No External Dependencies
- ✅ **ZERO external packages** — Uses only Python standard library
- ✅ **Fully self-contained** — All code is included in the repository
- ✅ **Verifiable build** — PyInstaller spec provided for transparent executable building

### No Network Activity
- ✅ **Offline operation** — No internet access required
- ✅ **No telemetry** — No user data collected
- ✅ **No external runtime code** — Everything happens locally

### No Obfuscation or Hiding
- ✅ **Plain source code** — All Python and C# is human-readable
- ✅ **No encryption** — No obfuscated binaries or hidden code
- ✅ **Full transparency** — All operations visible in built executable

### Safety Guarantees
- ✅ **Read-only by default** — Application only analyzes, no destructive changes
- ✅ **User-confirmed writes** — File modifications only happen after explicit user action
- ✅ **No system access** — Limited to mod folder only; no Registry or system files touched
- ✅ **No process execution** — Doesn't run external programs or scripts

### Audit & Verification
- ✅ **Complete logging** — All operations logged to console and GUI
- ✅ **Open source (MIT)** — Full source available for review at any time
- ✅ **Reproducible builds** — Same PyInstaller spec produces identical outputs
- ✅ **Antivirus safe** — No suspicious patterns, can be scanned independently

For more details, see [SECURITY.md](SECURITY.md).

---

## 🚀 Installation & Build Instructions

### Quick Start (Run from Source)

```bash
# 1. Clone the repository
git clone https://github.com/lQwadl/7d2d-mod-analyzer.git
cd 7d2d-mod-analyzer

# 2. Create virtual environment
python -m venv .venv

# 3. Activate virtual environment
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 4. Run the application
python main.py
```

### Build Standalone Executable

```bash
# 1. Activate virtual environment (see above)

# 2. Install build dependencies
pip install pyinstaller

# 3. Build executable
pyinstaller app.spec --clean --noconfirm

# 4. Output location:
# - Windows: dist/7d2d-mod-manager.exe
```

### Using Pre-Built Executable

1. Download `7d2d-mod-manager.exe` from the latest release
2. Run `7d2d-mod-manager.exe` directly—no installation needed
3. Select your 7 Days to Die Mods folder
4. Click **Scan** to analyze conflicts
5. Review recommendations; click **Rename** to apply changes
6. Relaunch 7 Days to Die from Steam

### Using the WinForms UI (Requires .NET 7)

```bash
# Navigate to the C# project
cd src/winforms/7dtd-mod-loadorder-manager

# Build and run
dotnet build
dotnet run
```

---

## Development

### Running Tests

```bash
# Install test dependencies
pip install pytest

# Run all tests
pytest

# Run with coverage
pytest --cov=. --cov-report=html
```

### Code Formatting & Linting

```bash
# Install dev tools
pip install black ruff

# Format code
black .

# Check code style
ruff check .
```

---

## Configuration

### app.spec
Controls how the standalone executable is built:
- Entry point: `src/gui/app.py`
- Data files to include: `data/` directory
- Excluded modules: test dependencies, dev tools

Modify this file to customize the build process. Rebuild with:
```bash
pyinstaller app.spec --clean --noconfirm
```

### config.json
Runtime application settings (e.g., logging level, UI options).

### data/rules.json
Defines conflict detection and resolution rules. Add custom rules here for specialized mod handling.

---

## Build & Release

### Prerequisites
```bash
pip install pyinstaller
```

### Building for Release
```bash
# Build clean standalone executable
pyinstaller app.spec --clean --noconfirm

# Output located in:
# dist/7d2d-mod-manager.exe          <- Standalone executable
```

### Distribution
1. Test the executable thoroughly on Windows 10/11
2. Submit to antivirus vendors if desired (optional)
3. Package with:
   - `7d2d-mod-manager.exe`
   - `README.md` (this file)
   - License information
   - Optional: `data/` folder for custom configs

---

## Troubleshooting

### Common Issues

**Q: "No module named 'gui'"**
- Ensure you're running from the project root directory
- Verify the virtual environment is activated

**Q: PyInstaller build fails**
- Ensure PyInstaller is installed: `pip install pyinstaller`
- Run from a path without special characters
- Try: `pyinstaller app.spec --clean --noconfirm`

**Q: Application won't start**
- Check `config.json` for syntax errors
- Verify `data/rules.json` exists and is valid JSON
- Run with administrator privileges if folder access is denied

---

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure code passes linting: `ruff check .`
5. Format code: `black .`
6. Submit a pull request with a clear description

---

## License

This project is licensed under the **MIT License**. See [LICENSE](LICENSE) file for details.

---

## Community & Support

- **Report Issues**: Use GitHub Issues for bug reports
- **Suggestions**: Feature requests welcome via Issues
- **Security**: Report security concerns privately to maintainers

---

## Changelog

### v1.1
- Improved conflict detection algorithms
- Enhanced logging and transparency
- Better handling of edge cases in mod metadata

### v1.0
- Initial release
- Core conflict detection and resolution
- Standalone executable build support

---

## Acknowledgments

Built with Python and [PyInstaller](https://www.pyinstaller.org/). Designed for the 7 Days to Die community.
