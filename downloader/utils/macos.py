"""macOS services used by the desktop app. Never write user state into the bundle."""
import ctypes
from functools import lru_cache
import os
from pathlib import Path


def support_dir():
    return Path.home() / 'Library/Application Support/daw下载器'


@lru_cache(maxsize=1)
def _rename_api():
    function = ctypes.CDLL(None, use_errno=True).renamex_np
    function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    return function


def rename_exclusive(src, dst):
    # Darwin <sys/stdio.h>: RENAME_EXCL. Unlike rename(), never replaces a destination.
    if _rename_api()(os.fsencode(src), os.fsencode(dst), 0x00000004):
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), dst)


def volume_online(directory):
    path = Path(os.path.realpath(directory))
    # /Volumes/Foo can remain as an ordinary directory after eject. Do not write there.
    if len(path.parts) >= 3 and path.parts[1] == 'Volumes':
        return os.path.ismount(Path(*path.parts[:3]))
    return True


@lru_cache(maxsize=1)
def _credential_cipher():
    """One user-bound key; a competing first launch must never replace an existing key."""
    from cryptography.fernet import Fernet
    sec = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    void, uint, text = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p
    find = sec.SecKeychainFindGenericPassword
    find.argtypes = [void, uint, text, uint, text, ctypes.POINTER(uint), ctypes.POINTER(void), void]
    add = sec.SecKeychainAddGenericPassword
    add.argtypes = [void, uint, text, uint, text, uint, text, void]
    free = sec.SecKeychainItemFreeContent
    free.argtypes = [void, void]
    service, account = b'com.laowang.downloader', b'browser-context-v1'
    query = (None, len(service), service, len(account), account)
    size, value = uint(), void()
    for _ in range(2):
        status = find(*query, ctypes.byref(size), ctypes.byref(value), None)
        if status == 0:
            try:
                return Fernet(ctypes.string_at(value, size.value))
            finally:
                free(None, value)
        if status != -25300:  # errSecItemNotFound
            break
        key = Fernet.generate_key()
        status = add(*query, len(key), key, None)
        if status == 0:
            return Fernet(key)
        if status != -25299:  # errSecDuplicateItem: re-read the winning key
            break
    raise OSError(f'无法使用登录钥匙串保护浏览器信息 (OSStatus {status})')


def crypt_context(data, decrypt):
    cipher = _credential_cipher()
    if decrypt:
        from cryptography.fernet import InvalidToken
        try:
            return cipher.decrypt(data)
        except InvalidToken as exc:
            raise ValueError('浏览器任务凭据无法解密') from exc
    return cipher.encrypt(data)
