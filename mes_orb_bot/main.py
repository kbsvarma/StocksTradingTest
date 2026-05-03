# main.py — Entry point.

import sys

import config
from bot import Bot


def main() -> None:
    if not config.PAPER_TRADING:
        print("\n" + "=" * 60)
        print("  ⚠️   LIVE TRADING MODE ACTIVE")
        print("  Real money will be at risk.")
        print("  Type 'LIVE' to confirm, anything else to abort:")
        print("=" * 60 + "\n")
        if input().strip() != "LIVE":
            print("Aborted.")
            sys.exit(0)

    bot = Bot()
    bot.run()


if __name__ == "__main__":
    main()
