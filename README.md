# 나의 일기 영어회화

한국어 일기를 미국식 영어로 번역하고, 문장별 MP3 로 만들어 보관·반복 학습하는 데스크톱 앱.
번역(Ollama)과 음성(MeloTTS)은 모두 이 PC 에서 실행한다.

## 실행

```bash
# 1) 번역 서버 (한 번 켜 두면 됨)
~/.local/ollama/bin/ollama serve &

# 2) 앱
conda activate diary_en
cd diary-english   # 이 저장소 폴더
python -m diary_english
```

- 개발 모드(가짜 번역·음성): `python -m diary_english --dev-fake`
- 첫 변환은 모델 로딩 때문에 1~2분 걸릴 수 있다(CPU).
- 저장한 자료는 번역 서버 없이도 재생된다.

## 파일 위치

| 무엇 | 어디 |
| --- | --- |
| 학습 자료(기본) | `~/문서/영어회화/학습자료/<날짜_시각_ID>/` |
| 설정·초안·임시 결과·로그 | `~/.local/share/diary-english/diary-english/` |
| 번역 모델 변경 | 위 폴더 `settings.json` 의 `translation_model` (기본 `qwen2.5:7b`) |

학습 폴더: `manifest.json`, `original.txt`, `translation.txt`, `full.mp3`, `sentences/001.mp3 …`

## 새 PC 에 설치

```bash
conda create -n diary_en python=3.10 -y
conda activate diary_en
pip install -r requirements.lock.txt
python -m unidic download
python -c "import nltk; [nltk.download(x) for x in ('averaged_perceptron_tagger_eng','averaged_perceptron_tagger','cmudict')]"
pip install --no-deps -e .

# 한글 입력(fcitx): 시스템 Qt6 플러그인을 PySide6 에 복사
cp /usr/lib/x86_64-linux-gnu/qt6/plugins/platforminputcontexts/libfcitxplatforminputcontextplugin-qt6.so \
   "$(python -c 'import PySide6,os;print(os.path.dirname(PySide6.__file__))')/Qt/plugins/platforminputcontexts/"

# Ollama (sudo 없이)
mkdir -p ~/.local/ollama && cd ~/.local/ollama
curl -fsSL https://github.com/ollama/ollama/releases/download/v0.34.4/ollama-linux-amd64.tar.zst | tar --zstd -x
~/.local/ollama/bin/ollama serve &
~/.local/ollama/bin/ollama pull qwen2.5:7b
```

MeloTTS 영어 모델은 첫 실행 때 자동으로 내려받는다.

## 테스트

```bash
python -m pytest                 # 자동 테스트 (모델·네트워크 불필요)
python -m pytest -m integration  # 실제 Ollama + MeloTTS
python scripts/check_environment.py
```

## 복구

- 저장하지 않은 변환 결과·보관한 초안이 남아 있으면 다음 실행 때 복구를 묻는다.
- 손상된 학습 폴더는 보관함에 "(손상)" 으로 표시되고, 다른 자료는 정상 사용된다.

## 알려진 제한

- 남성/여성 음성 선택 미지원 (MeloTTS 영어 화자에 성별 구분 없음).
- 검증 환경: Linux Mint 22.3, CPU 전용. 다른 OS 는 미검증.
