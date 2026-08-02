"""The desktop shell: a real window, a tray icon, and one instance at a time.

Nothing in here is required for the app to work. The server, the UI and the CLI
behave identically if every module in this package fails to import — which they
will on a headless box, over SSH, or on a desktop with no system tray. Each entry
point is written to return a falsey value rather than raise, and the caller in
:mod:`ebook_audiobook.web.server` treats that as "run the plain way".
"""
