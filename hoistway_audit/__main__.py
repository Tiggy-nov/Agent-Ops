from .config import Config
from .server import run


def main() -> None:
    run(Config.from_env())


if __name__ == "__main__":
    main()
