# ============================================================================
# List MounThor credetials
# ============================================================================
"""
List credentials stored by MounThor in the Freedesktop Secret Service.

This tool prints credential metadata only. Secret values are never printed.
"""

import secretstorage

connection = secretstorage.dbus_init()
collection = secretstorage.get_default_collection(connection)

items = list(
    collection.search_items({
        "service": "MounThor",
    })
)

print(f"Found {len(items)} MounThor credential(s):")

for item in items:
    print(
        item.get_label(),
        item.get_attributes(),
        sep="\n  "
    )