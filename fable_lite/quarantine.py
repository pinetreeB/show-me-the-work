from smtw.quarantine import *  # noqa: F403


if __name__ == "__main__":
    from ._entrypoint import run

    raise SystemExit(run("quarantine"))
