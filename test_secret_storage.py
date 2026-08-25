from mounthor import (
    _secure_storage_available,
    _secure_store_password,
    _secure_load_password,
    _secure_delete_password,
)


HOST = "mounthor-test.local"
SHARE = "test-share"
USERNAME = "test-user"
PASSWORD = "test-password-123"


print(
    "Available:",
    _secure_storage_available(),
)


_secure_store_password(
    HOST,
    SHARE,
    USERNAME,
    PASSWORD,
)

print(
    "Stored."
)


loaded = _secure_load_password(
    HOST,
    SHARE,
    USERNAME,
)

print(
    "Loaded:",
    loaded == PASSWORD,
)


_secure_delete_password(
    HOST,
    SHARE,
    USERNAME,
)

print(
    "Deleted."
)


loaded_after_delete = _secure_load_password(
    HOST,
    SHARE,
    USERNAME,
)

print(
    "Still present:",
    loaded_after_delete is not None,
)