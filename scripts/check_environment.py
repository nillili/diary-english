"""실행 환경 정보와 핵심 기능(MP3 쓰기, Qt MP3 디코딩, Ollama 연결)을 점검한다.

사용: python scripts/check_environment.py
"""

import json
import platform
import subprocess
import sys
import tempfile
import urllib.request
from importlib import metadata
from pathlib import Path

PACKAGES = ["PySide6", "torch", "torchaudio", "numpy", "soundfile", "melotts", "transformers", "librosa", "setuptools"]


def version(pkg: str) -> str:
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return "미설치"


def check_mp3_write(tmp: Path) -> str:
    import numpy as np
    import soundfile as sf

    p = tmp / "t.mp3"
    sf.write(str(p), (0.1 * np.sin(np.arange(22050) / 10)).astype("float32"), 22050, format="MP3")
    data, rate = sf.read(str(p))
    return f"OK ({len(data)} frames @ {rate}Hz), libsndfile {sf.__libsndfile_version__}"


def check_qt_mp3(tmp: Path) -> str:
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
    from PySide6.QtMultimedia import QMediaPlayer

    app = QCoreApplication.instance() or QCoreApplication([])
    player = QMediaPlayer()
    loop = QEventLoop()
    result = {"status": None, "duration": 0}

    def on_status(st):
        result["status"] = st
        if st in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.InvalidMedia):
            result["duration"] = player.duration()
            loop.quit()

    player.mediaStatusChanged.connect(on_status)
    player.setSource(QUrl.fromLocalFile(str(tmp / "t.mp3")))
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    del app
    return f"{result['status']}, duration={result['duration']}ms"


def check_ollama() -> str:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as r:
            models = [m["name"] for m in json.loads(r.read())["models"]]
        with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=3) as r:
            ver = json.loads(r.read())["version"]
        return f"v{ver}, 모델: {', '.join(models) or '없음'}"
    except Exception as exc:
        return f"연결 실패: {exc}"


def main() -> None:
    print("OS:", platform.platform())
    try:
        print("배포판:", subprocess.run(["lsb_release", "-ds"], capture_output=True, text=True).stdout.strip())
    except OSError:
        pass
    print("Python:", sys.version.split()[0], sys.executable)
    for p in PACKAGES:
        print(f"  {p}: {version(p)}")
    try:
        import torch

        print("  torch 장치:", "cuda" if torch.cuda.is_available() else "cpu")
    except Exception as exc:
        print("  torch 오류:", exc)
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for name, fn in (("MP3 쓰기/읽기", check_mp3_write), ("Qt MP3 디코딩", check_qt_mp3)):
            try:
                print(f"{name}:", fn(tmp))
            except Exception as exc:
                print(f"{name}: 실패 {exc}")
    print("Ollama:", check_ollama())


if __name__ == "__main__":
    main()
