"""
Application version and metadata.

This module defines the application metadata used across the project.
Version is defined here and imported by both Python code and PyInstaller.
"""

__title__ = "7d2d Mod Manager"
__description__ = "Mod manager and load-order optimizer for 7 Days to Die"
__version__ = "1.1.0"
__version_tuple__ = (1, 1, 0, 0)  # (major, minor, patch, build) for Windows
__author__ = "Anonymous"
__company__ = "7d2d-mod-tools"
__copyright__ = "Copyright (c) 2024 7d2d-mod-tools. All rights reserved."
__license__ = "MIT"

# Combined version strings for display
__version_string__ = f"{__title__} v{__version__}"
__user_agent__ = f"{__title__}/{__version__} (no-network; transparent-analysis)"

# File description for Windows properties
__file_description__ = "7 Days to Die Mod Manager and Load-Order Optimizer"
__product_name__ = "7d2d-mod-manager"
__internal_name__ = "7d2d-mod-manager"
__original_filename__ = "7d2d-mod-manager.exe"
