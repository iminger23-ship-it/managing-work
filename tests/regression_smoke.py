from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.screen_observer import ScreenObserver
from pc_ai_engine import _open_url_action


def main():
    with tempfile.TemporaryDirectory() as data_dir:
        observer = ScreenObserver(data_dir, interval=60)
        windows = iter(
            [
                ("notepad.exe", "Notes"),
                ("chrome.exe", "Password Manager"),
            ]
        )
        observer._foreground = lambda: next(windows)

        first = observer.observe_once()
        second = observer.observe_once()

        assert first["blocked"] is False
        assert second["blocked"] is True
        activity = observer.recent_activity()
        assert len(activity) == 1, activity
        assert activity[0]["process"] == "notepad.exe", activity
        assert observer.recent_observations()[0]["ocr_text"] == ""
        observer.stop()

        observer.interval = 0.01
        observer.start()
        observer.stop()
        observer.start()
        assert observer.thread is not None and observer.thread.is_alive()
        observer.stop()

    blocked = _open_url_action("http://127.0.0.1:11434/")
    assert "blocked" in blocked.lower() or "public internet" in blocked.lower(), blocked

    print("REGRESSION_SMOKE_OK")


if __name__ == "__main__":
    main()
