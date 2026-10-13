import argparse

from minidb.cli import main

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="MiniDBMS interactive shell")
    ap.add_argument("--data-dir", default="data", help="directory for databases (default: data)")
    main(ap.parse_args().data_dir)
