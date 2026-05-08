from pathlib import Path


class InfographicConfig:
    BASE_DIR = Path(__file__).parent.parent.absolute()
    CONFIG_DIR = Path(BASE_DIR, "config")
    LOGS_DIR = Path(BASE_DIR, "logs")
    ASSETS_DIR = Path(BASE_DIR, "assets")
