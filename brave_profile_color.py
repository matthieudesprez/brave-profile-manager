#!/usr/bin/env python3
"""
Brave Profile Color Manager

Set custom theme colors in Brave Browser profiles, bypassing the restricted
color picker introduced in recent Chromium versions.

See: https://github.com/brave/brave-browser/issues/39629
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

__version__ = "1.0.0"


# =============================================================================
# Browser Detection
# =============================================================================

def is_brave_running() -> bool:
    """Check if Brave Browser is currently running."""
    if sys.platform == "darwin":
        result = subprocess.run(
            ["pgrep", "-x", "Brave Browser"],
            capture_output=True
        )
        return result.returncode == 0
    elif sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq brave.exe"],
            capture_output=True,
            text=True
        )
        return "brave.exe" in result.stdout.lower()
    else:  # Linux
        result = subprocess.run(
            ["pgrep", "-x", "brave"],
            capture_output=True
        )
        return result.returncode == 0


def get_brave_user_data_dir() -> Path:
    """Get the Brave Browser user data directory for the current platform."""
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/BraveSoftware/Brave-Browser"
    elif sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "BraveSoftware/Brave-Browser/User Data"
    else:  # Linux
        return Path.home() / ".config/BraveSoftware/Brave-Browser"


# =============================================================================
# Backup Management
# =============================================================================

def get_backup_dir(user_data_dir: Path) -> Path:
    """Get the backup directory path."""
    return user_data_dir / ".color_backups"


def backup_preferences(prefs_path: Path, user_data_dir: Path) -> Path | None:
    """
    Create a backup of the Preferences file before modification.

    Returns:
        Path to backup file, or None if backup failed
    """
    backup_dir = get_backup_dir(user_data_dir)
    backup_dir.mkdir(exist_ok=True)

    # Create backup filename with timestamp and profile info
    profile_folder = prefs_path.parent.name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{profile_folder}_{timestamp}.json"
    backup_path = backup_dir / backup_name

    try:
        shutil.copy2(prefs_path, backup_path)
        return backup_path
    except IOError:
        return None


def list_backups(user_data_dir: Path) -> list[tuple[str, Path]]:
    """List all available backups."""
    backup_dir = get_backup_dir(user_data_dir)
    if not backup_dir.exists():
        return []

    backups = []
    for f in sorted(backup_dir.glob("*.json"), reverse=True):
        backups.append((f.stem, f))
    return backups


# =============================================================================
# Color Conversion
# =============================================================================

def hex_to_signed_int(hex_color: str) -> int:
    """
    Convert a hex color string to a signed 32-bit integer (ARGB format).

    The color is stored as a signed 32-bit integer where:
    - Alpha is always 0xFF (fully opaque)
    - The remaining 24 bits are RGB

    Args:
        hex_color: Color in format "#RRGGBB" or "RRGGBB"

    Returns:
        Signed 32-bit integer representation
    """
    hex_color = hex_color.lstrip('#')

    if len(hex_color) == 6:
        # Add alpha channel (FF = fully opaque)
        hex_color = "FF" + hex_color
    elif len(hex_color) != 8:
        raise ValueError(f"Invalid hex color format: {hex_color}")

    # Convert to unsigned 32-bit integer
    unsigned = int(hex_color, 16)

    # Convert to signed 32-bit integer
    if unsigned >= (1 << 31):
        return unsigned - (1 << 32)
    return unsigned


def signed_int_to_hex(signed_int: int) -> str:
    """
    Convert a signed 32-bit integer back to hex color.

    Args:
        signed_int: The signed integer from Preferences

    Returns:
        Hex color string in format "#RRGGBB"
    """
    if signed_int < 0:
        unsigned = signed_int + (1 << 32)
    else:
        unsigned = signed_int

    # Return just RGB part (skip alpha)
    return f"#{unsigned & 0xFFFFFF:06X}"


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip('#')
    return (
        int(hex_color[0:2], 16),
        int(hex_color[2:4], 16),
        int(hex_color[4:6], 16)
    )


def color_swatch(hex_color: str, width: int = 2) -> str:
    """
    Generate a terminal color swatch using ANSI 24-bit color codes.

    Args:
        hex_color: Color in format "#RRGGBB"
        width: Number of space characters for the swatch

    Returns:
        String with ANSI escape codes to display a colored block
    """
    r, g, b = hex_to_rgb(hex_color)
    # Use 24-bit true color: \033[48;2;R;G;Bm for background
    return f"\033[48;2;{r};{g};{b}m{' ' * width}\033[0m"


def format_color_display(hex_color: str | None) -> str:
    """Format a color for display with swatch."""
    if hex_color is None:
        return "(not set)    "
    if hex_color.startswith("ERROR"):
        return hex_color
    return f"{color_swatch(hex_color)} {hex_color}"


# =============================================================================
# Profile Management
# =============================================================================

def load_profile_names_from_local_state(user_data_dir: Path) -> dict[str, str]:
    """
    Load profile display names from the Local State file.

    The Local State file contains the authoritative profile names,
    which may differ from the names stored in individual Preferences files.

    Returns:
        Dict mapping folder name (e.g., "Profile 1") to display name
    """
    local_state_path = user_data_dir / "Local State"
    try:
        with open(local_state_path, 'r', encoding='utf-8') as f:
            state = json.load(f)
        info_cache = state.get("profile", {}).get("info_cache", {})
        return {folder: info.get("name", "") for folder, info in info_cache.items()}
    except (json.JSONDecodeError, IOError):
        return {}


def get_profile_dirs(user_data_dir: Path) -> list[tuple[str, Path]]:
    """
    Get all profile directories.

    Returns:
        List of (profile_name, profile_path) tuples
    """
    profiles = []

    for entry in user_data_dir.iterdir():
        if not entry.is_dir():
            continue

        prefs_file = entry / "Preferences"
        if prefs_file.exists():
            # Skip system-level profiles
            if entry.name in ["System Profile", "Guest Profile"]:
                continue
            profiles.append((entry.name, entry))

    # Sort: Default first, then Profile N in numeric order
    def sort_key(item):
        name = item[0]
        if name == "Default":
            return (0, 0)
        elif name.startswith("Profile "):
            try:
                num = int(name.split()[1])
                return (1, num)
            except (IndexError, ValueError):
                return (2, name)
        return (2, name)

    return sorted(profiles, key=sort_key)


def get_profile_display_name(prefs_path: Path) -> str:
    """Get the user-visible profile name from a Preferences file."""
    try:
        with open(prefs_path, 'r', encoding='utf-8') as f:
            prefs = json.load(f)
        return prefs.get("profile", {}).get("name", "")
    except (json.JSONDecodeError, IOError):
        return ""


def get_current_color(prefs_path: Path) -> str | None:
    """Get the current theme color from a Preferences file."""
    try:
        with open(prefs_path, 'r', encoding='utf-8') as f:
            prefs = json.load(f)

        color = prefs.get("autogenerated", {}).get("theme", {}).get("color")
        if color is not None:
            return signed_int_to_hex(color)
        return None
    except (json.JSONDecodeError, IOError) as e:
        return f"ERROR: {e}"


def get_theme_id(prefs_path: Path) -> str:
    """Get the current theme ID from a Preferences file."""
    try:
        with open(prefs_path, 'r', encoding='utf-8') as f:
            prefs = json.load(f)
        return prefs.get("extensions", {}).get("theme", {}).get("id", "")
    except (json.JSONDecodeError, IOError):
        return ""


def set_theme_color(
    prefs_path: Path,
    color_int: int,
    user_data_dir: Path,
    dry_run: bool = False,
    backup: bool = True
) -> bool:
    """
    Set the theme color in a Preferences file.

    Args:
        prefs_path: Path to the Preferences file
        color_int: The signed integer color value
        user_data_dir: Path to user data directory (for backups)
        dry_run: If True, don't actually write changes
        backup: If True, create a backup before modifying

    Returns:
        True if successful, False otherwise
    """
    try:
        with open(prefs_path, 'r', encoding='utf-8') as f:
            prefs = json.load(f)

        # Ensure the nested structure exists
        if "autogenerated" not in prefs:
            prefs["autogenerated"] = {}
        if "theme" not in prefs["autogenerated"]:
            prefs["autogenerated"]["theme"] = {}

        # Set the color value
        prefs["autogenerated"]["theme"]["color"] = color_int

        # IMPORTANT: Set the theme ID to use the autogenerated theme
        # This is required to bypass the restricted color picker
        if "extensions" not in prefs:
            prefs["extensions"] = {}
        if "theme" not in prefs["extensions"]:
            prefs["extensions"]["theme"] = {}
        prefs["extensions"]["theme"]["id"] = "autogenerated_theme_id"

        if not dry_run:
            # Create backup before modifying
            if backup:
                backup_preferences(prefs_path, user_data_dir)

            with open(prefs_path, 'w', encoding='utf-8') as f:
                json.dump(prefs, f, separators=(',', ':'))

        return True
    except (json.JSONDecodeError, IOError) as e:
        print(f"  Error: {e}", file=sys.stderr)
        return False


def find_profiles_by_name(
    user_data_dir: Path,
    names: list[str]
) -> list[tuple[str, Path]]:
    """
    Find profiles by their display name (case-insensitive, partial match).

    Args:
        user_data_dir: Path to Brave user data directory
        names: List of profile names to search for

    Returns:
        List of (folder_name, path) tuples for matching profiles
    """
    all_profiles = get_profile_dirs(user_data_dir)
    profile_names = load_profile_names_from_local_state(user_data_dir)

    # Build reverse lookup: display_name -> (folder_name, path)
    name_to_profile: dict[str, tuple[str, Path]] = {}
    for folder_name, path in all_profiles:
        display_name = profile_names.get(folder_name) or get_profile_display_name(path / "Preferences")
        if display_name:
            name_to_profile[display_name.lower()] = (folder_name, path)

    matched = []
    for search_name in names:
        search_lower = search_name.lower()
        # Try exact match first
        if search_lower in name_to_profile:
            matched.append(name_to_profile[search_lower])
        else:
            # Try partial match
            for display_name, profile_info in name_to_profile.items():
                if search_lower in display_name:
                    matched.append(profile_info)
                    break

    return matched


def list_profiles(user_data_dir: Path):
    """List all profiles and their current theme colors."""
    profiles = get_profile_dirs(user_data_dir)
    profile_names = load_profile_names_from_local_state(user_data_dir)

    if not profiles:
        print("No profiles found.")
        return

    print(f"Found {len(profiles)} profile(s):\n")
    print(f"{'Folder':<15} {'Profile Name':<28} {'Color':<20} {'Status'}")
    print("-" * 80)

    for folder_name, path in profiles:
        prefs_path = path / "Preferences"
        # Prefer name from Local State, fall back to Preferences
        display_name = profile_names.get(folder_name) or get_profile_display_name(prefs_path)
        color = get_current_color(prefs_path)
        theme_id = get_theme_id(prefs_path)
        color_display = format_color_display(color)

        # Determine status based on theme_id
        if theme_id == "autogenerated_theme_id":
            status = "\033[32m✓ custom\033[0m"  # green
        elif theme_id == "user_color_theme_id":
            status = "\033[33m⚠ restricted\033[0m"  # yellow
        elif theme_id == "":
            status = "\033[90m(none)\033[0m"  # gray
        else:
            status = "\033[90mextension\033[0m"  # gray (using a theme extension)

        print(f"{folder_name:<15} {display_name:<28} {color_display:<20} {status}")


# =============================================================================
# CLI Interface
# =============================================================================

def print_error(msg: str):
    """Print an error message to stderr."""
    print(f"\033[31mError:\033[0m {msg}", file=sys.stderr)


def print_warning(msg: str):
    """Print a warning message to stderr."""
    print(f"\033[33mWarning:\033[0m {msg}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Set custom theme colors in Brave Browser profiles, "
                    "bypassing the restricted color picker.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s "#FF5500"                      Apply orange to all profiles
  %(prog)s "#3366FF" -p Default           Apply blue to Default profile only
  %(prog)s "#3366FF" -p "Profile 1" "Profile 4"
  %(prog)s "#AA0000" -n "LIZY - Dev"      Apply by profile name
  %(prog)s --list                         Show all profiles and colors
  %(prog)s "#00FF00" --dry-run            Preview changes without applying
  %(prog)s --list --data-dir /path/to/Brave-Browser-Beta

Color format:
  Use standard 6-digit hex colors: #RRGGBB (e.g., #FF5500, #3366FF)

More info: https://github.com/brave/brave-browser/issues/39629
        """
    )

    parser.add_argument(
        "color",
        nargs="?",
        help="Hex color to apply (e.g., #FF5500)"
    )
    parser.add_argument(
        "-p", "--profiles",
        nargs="+",
        metavar="FOLDER",
        help="Profile folders to update (e.g., 'Default', 'Profile 1')"
    )
    parser.add_argument(
        "-n", "--name",
        nargs="+",
        metavar="NAME",
        help="Profile names to update (case-insensitive, partial match)"
    )
    parser.add_argument(
        "-l", "--list",
        action="store_true",
        help="List all profiles and their current colors"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without making changes"
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Don't create backups before modifying"
    )
    parser.add_argument(
        "-f", "--force",
        action="store_true",
        help="Apply changes even if Brave is running (not recommended)"
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--data-dir",
        metavar="PATH",
        help="Custom Brave user data directory (default: auto-detect)"
    )

    args = parser.parse_args()

    user_data_dir = Path(args.data_dir) if args.data_dir else get_brave_user_data_dir()

    if not user_data_dir.exists():
        print_error(f"Brave user data directory not found: {user_data_dir}")
        sys.exit(1)

    # List mode
    if args.list:
        list_profiles(user_data_dir)
        return

    # Color is required for non-list operations
    if not args.color:
        parser.print_help()
        sys.exit(1)

    # Check if Brave is running
    if is_brave_running():
        if args.force:
            print_warning("Brave is running. Changes may not take effect until restart.")
        else:
            print_error("Brave Browser is running. Please close it first.")
            print("       Use --force to apply anyway (changes won't take effect until restart).")
            sys.exit(1)

    # Validate and convert color
    try:
        color_int = hex_to_signed_int(args.color)
        color_hex = args.color.upper() if args.color.startswith('#') else f"#{args.color.upper()}"
    except ValueError as e:
        print_error(str(e))
        sys.exit(1)

    print(f"Color: {color_swatch(color_hex)} {color_hex} (value: {color_int})")
    if args.dry_run:
        print("DRY RUN - no changes will be made\n")
    else:
        print()

    # Get profiles to update
    all_profiles = get_profile_dirs(user_data_dir)
    profiles: list[tuple[str, Path]] = []

    if args.name:
        # Find profiles by display name
        profiles = find_profiles_by_name(user_data_dir, args.name)
        if not profiles:
            print_error(f"No profiles found matching: {', '.join(args.name)}")
            print("       Use --list to see available profiles.")
            sys.exit(1)
    elif args.profiles:
        # Filter by folder name
        profile_set = set(args.profiles)
        profiles = [(n, p) for n, p in all_profiles if n in profile_set]

        # Check for missing profiles
        found_names = {n for n, _ in profiles}
        missing = profile_set - found_names
        if missing:
            print_warning(f"Profile folder(s) not found: {', '.join(missing)}")
    else:
        profiles = all_profiles

    if not profiles:
        print_error("No profiles to update.")
        sys.exit(1)

    # Load profile names from Local State
    profile_names = load_profile_names_from_local_state(user_data_dir)

    # Update profiles
    success_count = 0
    do_backup = not args.no_backup and not args.dry_run

    for folder_name, path in profiles:
        prefs_path = path / "Preferences"
        # Prefer name from Local State, fall back to Preferences
        display_name = profile_names.get(folder_name) or get_profile_display_name(prefs_path)
        current = get_current_color(prefs_path)

        status = "would update" if args.dry_run else "updating"
        name_display = f"{folder_name} ({display_name})" if display_name else folder_name
        current_display = format_color_display(current)
        new_display = format_color_display(color_hex)
        print(f"{status} {name_display}: {current_display} -> {new_display}")

        if set_theme_color(
            prefs_path,
            color_int,
            user_data_dir,
            dry_run=args.dry_run,
            backup=do_backup
        ):
            success_count += 1

    print(f"\n{'Would update' if args.dry_run else 'Updated'} {success_count}/{len(profiles)} profile(s)")

    if not args.dry_run and success_count > 0:
        if do_backup:
            print(f"\nBackups saved to: {get_backup_dir(user_data_dir)}")
        print("Restart Brave Browser to see the changes.")


if __name__ == "__main__":
    main()
