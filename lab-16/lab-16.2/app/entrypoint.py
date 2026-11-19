from app.demo import main as run_demo
from app.wait_for_databases import main as wait_for_databases


def main() -> None:
    wait_for_databases()
    run_demo()


if __name__ == "__main__":
    main()
