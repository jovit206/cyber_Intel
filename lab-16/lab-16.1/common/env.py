from pathlib import Path

from dotenv import load_dotenv

CHAPTER_ROOT = Path(__file__).resolve().parents[2]


def load_chapter16_env() -> None:
    load_dotenv(CHAPTER_ROOT / ".env")
