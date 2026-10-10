#!/usr/bin/env python3
"""Synchronize release metadata with APP_VERSION and APP_RELEASE_DATE."""

from __future__ import annotations

import re
import sys
from datetime import date, datetime
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_FILE = ROOT / "mounthor.py"
DEBIAN_CHANGELOG = ROOT / "debian" / "changelog"
RPM_SPEC = ROOT / "packaging" / "rpm" / "mounthor.spec"
APPSTREAM = ROOT / "data" / "io.github.mizgo.MounThor.metainfo.xml"


def replace_once(text: str, pattern: str, replacement, label: str, flags: int = 0) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise ValueError(f"Expected one {label} entry, found {count}.")
    return updated


def extract_constant(text: str, name: str) -> str:
    values = re.findall(
        rf"^{re.escape(name)}\s*=\s*['\"]([^'\"]+)['\"]\s*$",
        text,
        re.MULTILINE,
    )
    if len(values) != 1:
        raise ValueError(f"Expected exactly one {name} assignment in {APP_FILE}.")
    return values[0]


def main() -> int:
    app_text = APP_FILE.read_text(encoding="utf-8")
    version = extract_constant(app_text, "APP_VERSION")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version):
        raise ValueError(f"APP_VERSION must contain numeric release components, got {version!r}.")

    release_date_text = extract_constant(app_text, "APP_RELEASE_DATE")
    month_numbers = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    date_parts = re.fullmatch(r"(\d{1,2}) ([A-Za-z]+) (\d{4})", release_date_text)
    try:
        if date_parts is None:
            raise ValueError
        month = month_numbers[date_parts.group(2).lower()]
        release_date = date(int(date_parts.group(3)), month, int(date_parts.group(1)))
    except (KeyError, ValueError) as error:
        raise ValueError(
            f"APP_RELEASE_DATE must use the format '10 October 2026', got {release_date_text!r}."
        ) from error
    iso_date = release_date.isoformat()

    debian = DEBIAN_CHANGELOG.read_text(encoding="utf-8")
    debian = replace_once(
        debian,
        r"^mounthor \([^)]*\)",
        f"mounthor ({version}-1)",
        "top Debian changelog version",
        re.MULTILINE,
    )

    def update_debian_date(match: re.Match[str]) -> str:
        try:
            previous = parsedate_to_datetime(match.group(2))
            updated = datetime.combine(release_date, previous.timetz())
            return match.group(1) + format_datetime(updated)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("Could not update the Debian changelog release date.") from error

    debian = replace_once(
        debian,
        r"^( -- .+  )(.+)$",
        update_debian_date,
        "Debian changelog signature date",
        re.MULTILINE,
    )

    rpm = RPM_SPEC.read_text(encoding="utf-8")
    rpm = replace_once(rpm, r"^Version:\s*.*$", f"Version:        {version}", "RPM Version field", re.MULTILINE)
    rpm = replace_once(rpm, r"^Release:\s*.*$", "Release:        1%{?dist}", "RPM Release field", re.MULTILINE)

    weekdays = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    rpm_date = f"{weekdays[release_date.weekday()]} {months[release_date.month - 1]} {release_date.day} {release_date.year}"

    def update_rpm_changelog(match: re.Match[str]) -> str:
        return f"* {rpm_date}{match.group(1)}{version}-1"

    rpm = replace_once(
        rpm,
        r"^\* \w{3} \w{3} +\d{1,2} \d{4}( .+ - )\S+$",
        update_rpm_changelog,
        "top RPM changelog entry",
        re.MULTILINE,
    )

    appstream = APPSTREAM.read_text(encoding="utf-8")

    def update_appstream_release(match: re.Match[str]) -> str:
        tag = match.group(0)
        tag, version_count = re.subn(r"\bversion=(['\"])[^'\"]*\1", f'version="{version}"', tag, count=1)
        tag, date_count = re.subn(r"\bdate=(['\"])[^'\"]*\1", f'date="{iso_date}"', tag, count=1)
        if version_count != 1 or date_count != 1:
            raise ValueError("The AppStream release entry must have one version and one date attribute.")
        return tag

    appstream = replace_once(
        appstream,
        r"^\s*<release\b[^>]*/>\s*$",
        update_appstream_release,
        "AppStream release entry",
        re.MULTILINE,
    )

    # All parsing and replacements succeed before any metadata file is written.
    DEBIAN_CHANGELOG.write_text(debian, encoding="utf-8")
    RPM_SPEC.write_text(rpm, encoding="utf-8")
    APPSTREAM.write_text(appstream, encoding="utf-8")
    print(f"Synchronized release metadata to version {version}, dated {iso_date}.")
    print("Debian and RPM package revisions were reset to 1.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"Version synchronization failed: {error}", file=sys.stderr)
        raise SystemExit(1)