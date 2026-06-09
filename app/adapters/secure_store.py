"""Secure secret storage with a size-robust fallback.

Windows Credential Manager (the default keyring backend) rejects blobs larger than ~1.3 KB,
and OAuth token JSON (JWT access + refresh) exceeds that. So:

  - small values  -> OS keychain (keyring)
  - large values  -> a file in the user's local app-data dir, encrypted at rest:
        * Windows: DPAPI (CryptProtectData) — tied to the OS user account, no extra deps
        * other:   plaintext file with 0600 perms (keyring usually works there anyway)

Used for the Robinhood MCP OAuth tokens. (TODO Chunk 8: consider DPAPI on all platforms via
an explicit crypto dep for parity.)
"""

from __future__ import annotations

import os
import sys

try:
    import keyring

    _HAVE_KEYRING = True
except Exception:  # pragma: no cover
    _HAVE_KEYRING = False

SERVICE = "hardspace-rh-mcp"


def _store_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(base, "hardspace-finance", "secrets")
    os.makedirs(d, exist_ok=True)
    return d


def _file(key: str) -> str:
    return os.path.join(_store_dir(), key.replace(":", "_") + ".bin")


# ---- DPAPI (Windows) ---------------------------------------------------------
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32

    def _blob_bytes(blob: "_BLOB") -> bytes:
        size = blob.cbData
        return ctypes.cast(blob.pbData, ctypes.POINTER(ctypes.c_char * size)).contents.raw

    def _encrypt(data: bytes) -> bytes:
        buf = ctypes.create_string_buffer(data, len(data))
        bin_in = _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        bin_out = _BLOB()
        if not _crypt32.CryptProtectData(
            ctypes.byref(bin_in), None, None, None, None, 0, ctypes.byref(bin_out)
        ):
            raise OSError("CryptProtectData failed")
        try:
            return _blob_bytes(bin_out)
        finally:
            _kernel32.LocalFree(bin_out.pbData)

    def _decrypt(data: bytes) -> bytes:
        buf = ctypes.create_string_buffer(data, len(data))
        bin_in = _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        bin_out = _BLOB()
        if not _crypt32.CryptUnprotectData(
            ctypes.byref(bin_in), None, None, None, None, 0, ctypes.byref(bin_out)
        ):
            raise OSError("CryptUnprotectData failed")
        try:
            return _blob_bytes(bin_out)
        finally:
            _kernel32.LocalFree(bin_out.pbData)

else:  # pragma: no cover

    def _encrypt(data: bytes) -> bytes:
        return data

    def _decrypt(data: bytes) -> bytes:
        return data


def _write_file(key: str, value: str) -> None:
    blob = _encrypt(value.encode("utf-8"))
    path = _file(key)
    with open(path, "wb") as f:
        f.write(blob)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_file(key: str) -> str | None:
    path = _file(key)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return _decrypt(f.read()).decode("utf-8")


def _remove_file(key: str) -> None:
    try:
        os.remove(_file(key))
    except OSError:
        pass


# ---- public API --------------------------------------------------------------
def set_secret(key: str, value: str) -> None:
    if _HAVE_KEYRING:
        try:
            keyring.set_password(SERVICE, key, value)
            _remove_file(key)  # keep a single source of truth
            return
        except Exception:
            pass  # too large / no backend -> fall through to file
    _write_file(key, value)
    if _HAVE_KEYRING:
        try:
            keyring.delete_password(SERVICE, key)
        except Exception:
            pass


def get_secret(key: str) -> str | None:
    if _HAVE_KEYRING:
        try:
            v = keyring.get_password(SERVICE, key)
            if v is not None:
                return v
        except Exception:
            pass
    return _read_file(key)


def delete_secret(key: str) -> None:
    if _HAVE_KEYRING:
        try:
            keyring.delete_password(SERVICE, key)
        except Exception:
            pass
    _remove_file(key)
