"""Finder and Native Messaging share one bundle, code identity and Python runtime."""
import sys

if __name__ == '__main__':
    if '--native-host' in sys.argv[1:]:
        from downloader.browser.native_host import main
    else:
        from main import main
        from downloader.utils.app_log import setup_logging
        setup_logging()
    main()
