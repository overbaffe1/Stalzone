def test_ui_module_imports_without_display():
    import ui.app  # noqa: F401  (customtkinter импортируется только в main())

    assert callable(ui.app.main)
    assert ui.app._fmt_money(1400000) == "1 400 000 ₽"
