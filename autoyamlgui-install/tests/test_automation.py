from autoyamlgui import automation


def test_wait_for_window_focuses_matching_title(monkeypatch):
    seen = {}

    monkeypatch.setattr(automation, "_get_window_titles", lambda: ["My App - Etomo"])

    def fake_focus_window(title):
        seen["title"] = title

    monkeypatch.setattr(automation, "_focus_window", fake_focus_window)

    assert automation.wait_for_window("* - Etomo", timeout=0.1) is True
    assert seen["title"] == "My App - Etomo"


def test_wait_for_window_close_all(monkeypatch):
    called = []

    monkeypatch.setattr(automation, "_get_window_titles", lambda: ["Doc - Etomo", "Other - Etomo"])

    def fake_close_matching_windows(pattern):
        called.append(pattern)
        return True

    monkeypatch.setattr(automation, "_close_matching_windows", fake_close_matching_windows)

    assert automation.wait_for_window("* - Etomo", timeout=0.1, action="close_all") is True
    assert called == ["* - Etomo"]

def test_wait_for_window_close_all_no_matches(monkeypatch):
    monkeypatch.setattr(automation, "_get_window_titles", lambda: [])

    assert automation.wait_for_window("* - Etomo", timeout=0.1, action="close_all") is True