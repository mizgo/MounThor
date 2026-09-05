#!/usr/bin/env python3

"""Secure credential storage using the Secret Service API."""

import logging
import os

import secretstorage

from .constants import CREDENTIAL_SERVICE

LOGGER = logging.getLogger("mounthor")


# ============================================================================
# Secret Service helpers
# ============================================================================

def _secret_service_connection():
    """Initialize a D-Bus connection to the Secret Service."""
    return secretstorage.dbus_init()


def _secret_service_collection(connection):
    """Get the default collection, unlocking it if necessary."""
    collection = secretstorage.get_default_collection(connection)
    if collection.is_locked():
        collection.unlock()
    return collection


def _secret_service_attributes(host: str, share: str, username: str) -> dict:
    """Build search attributes for a credential entry."""
    return {
        "service": CREDENTIAL_SERVICE,
        "host": host,
        "share": share,
        "username": username,
    }


# ============================================================================
# Public API
# ============================================================================

def secure_storage_available() -> bool:
    """Check whether the Secret Service is available on this system."""
    try:
        connection = _secret_service_connection()
        _secret_service_collection(connection)
        return True
    except Exception:
        return False


def secure_store_password(host: str, share: str, username: str, password: str) -> None:
    """Store a password in the system keyring."""
    connection = _secret_service_connection()
    collection = _secret_service_collection(connection)

    attributes = _secret_service_attributes(host, share, username)
    label = f"MounThor SMB password for //{host}/{share}"

    collection.create_item(
        label,
        attributes,
        password.encode("utf-8"),
        replace=True,
    )


def secure_load_password(host: str, share: str, username: str) -> str | None:
    """Retrieve a stored password from the system keyring."""
    connection = _secret_service_connection()
    collection = _secret_service_collection(connection)

    attributes = _secret_service_attributes(host, share, username)
    items = collection.search_items(attributes)

    for item in items:
        if item.is_locked():
            item.unlock()
        return item.get_secret().decode("utf-8")

    return None


def secure_delete_password(host: str, share: str, username: str) -> None:
    """Remove a stored password from the system keyring."""
    connection = _secret_service_connection()
    collection = _secret_service_collection(connection)

    attributes = _secret_service_attributes(host, share, username)
    items = collection.search_items(attributes)

    for item in items:
        item.delete()
