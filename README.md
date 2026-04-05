# 7d2d Mod Analyzer

A comprehensive Python tool for analyzing, conflict detection, and load-order optimization for **7 Days to Die** mods.

---

## 🔍 Source Code Location

**ALL source code is located in the `/src` folder.** This repository contains:
- ✅ Complete, un-obfuscated Python source code (99 .py files)
- ✅ Complete C# WinForms sources (30 .cs files)  
- ✅ Full implementation with NO external dependencies beyond standard library
- ✅ Build artifacts in `/build` folder (NOT source code)
- ✅ Tests in `/tests` folder (fully traceable)

**The code is transparent and available for full security review.**

---

## Overview

This tool helps modpack creators and players manage large mod collections by:
- **Scanning** mod metadata and detecting conflicts
- **Resolving** load order issues automatically
- **Optimizing** mod dependencies for stability
- **Reporting** detailed conflict analysis
- **Providing transparency** with complete operation logging

Safe for antivirus scanning. Opens only the mod folder you select—no internet access, registry access, or system file modifications.

---

## 📁 Project Structure

```
├── main.py                          # Root entry point (imports from /src)
├── src/                             # ⭐ ALL SOURCE CODE HERE
│   ├── gui/                         # GUI application (Tkinter)
│   │   └── app.py                   # Main GUI window entry point
│   ├── logic/                       # Business logic (22 modules)
│   │   ├── conflict_detector.py
│   │   ├── load_order_engine.py
│   │   ├── classifier.py
│   │   └── ... (19 more modules)
│   ├── engines/                     # Analysis engines (10 modules)
│   │   ├── conflict_engine.py
│   │   ├── classification_engine.py
│   │   └── ... (8 more modules)
│   ├── deployment/                  # Deployment operations (6 modules)
│   ├── scanner/                     # Mod scanning (5 modules)
│   ├── models/                      # Data models (3 modules)
│   ├── mock_deploy/                 # Simulation/testing (4 modules)
│   ├── exporter/                    # Export functionality (2 modules)
│   ├── xml_analyzer/                # XML parsing utilities
│   └── path_safety.py               # Path safety utilities
├── src/winforms/                    # C# WinForms project (alternative UI)
│   └── 7dtd-mod-loadorder-manager/  # Full .NET source (30 .cs files)
├── tests/                           # Test suite (35 test files)
├── scripts/                         # Utility scripts
├── data/                            # Configuration & rules
│   ├── rules.json                   # Conflict resolution rules
│   └── mod_metadata.json            # Detected mod metadata
├── build/                           # Build artifacts (compiled output)
├── pyproject.toml                   # Python project configuration
├── app.spec                         # PyInstaller build specification
├── LICENSE                          # MIT License
└── README.md                         # This file
```

---

## ⚡ Entry Points

| Entry Point | Purpose | Language |
|---|---|---|
| **main.py** | Primary entry point (command-line or programmatic) | Python |
| **src/gui/app.py** | GUI application (Tkinter) | Python |
| **src/winforms/.../Program.cs** | Alternative WinForms GUI (requires .NET 7) | C# |

Run the GUI:
```bash
python main.py
```

Or directly:
```bash
python src/gui/app.py
```

---

## Requirements

### For Running Pre-Built Executable
- **Windows 10/11** (64-bit)
- No Python installation required
- No dependencies to install

### For Building from Source or Development
- **Python 3.10 or higher** (3.10, 3.11, 3.12 tested)
- **pip** (included with Python)
- **PyInstaller** (for building standalone `.exe`)

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
# - Windows: dist/7d2d-mod-analyzer.exe
```

### Using Pre-Built Executable

1. Download `7d2d-mod-analyzer.exe` from the latest release
2. Run `7d2d-mod-analyzer.exe` directly—no installation needed
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

## ✨ Features

- **Conflict Detection**: Identifies and categorizes mod conflicts
- **Load Order Optimization**: Reorders mods for compatibility
- **Transparent Logging**: All operations logged in the GUI for verification
- **Standalone Executable**: Pre-built `.exe` available (or build from source)
- **Safe Operation**: Read-only by default; write operations only after user confirmation
- **Windows Optimized**: Built for Windows 10/11

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

### Project Configuration

The project uses:
- **pyproject.toml**: Python project metadata and tool configuration
- **app.spec**: PyInstaller spec for building standalone executables
- **config.json**: Application runtime configuration
- **data/rules.json**: Conflict detection and resolution rules

---

## Security & Transparency

This project prioritizes **security** and **transparency**:

### 🔍 What This Tool Does
- Reads mod folder structure and metadata files
- Analyzes XML and JSON configuration files
- Detects conflicts based on configurable rules
- Optionally renames mod folders (write operation)

### ❌ What This Tool Does NOT Do
- Access the internet or make network calls
- Modify system registry or files outside the selected mod folder
- Run executable files or scripts
- Encrypt or obfuscate any data
- Collect telemetry or user information

### 📋 Transparency Features
- **Complete logging**: All operations logged in the GUI
- **No hidden operations**: All file I/O is visible
- **Open source**: Code available for security review
- **Antivirus safe**: No packing, encryption, or obfuscation

### 🔐 Trust & Verification
- Built with standard Python libraries
- PyInstaller spec provided for rebuilding
- Suitable for antivirus scanning and security audits
- No unsigned or suspicious operations

For detailed security information, see [SECURITY.md](SECURITY.md).

---

## Configuration

### app.spec
Controls how the standalone executable is built:
- Entry point: `gui/app.py`
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
# dist/7d2d-mod-analyzer.exe          <- Standalone executable
```

### Distribution
1. Test the executable thoroughly on Windows 10/11
2. Submit to antivirus vendors if desired (optional)
3. Package with:
   - `7d2d-mod-analyzer.exe`
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
