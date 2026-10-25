"""`nightshift-dashboard`: run the dashboard on this machine."""
import argparse

import uvicorn


def main():
    parser = argparse.ArgumentParser(prog="nightshift-dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="keep 127.0.0.1 and expose it through a tunnel if the crew needs to reach it")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run("server.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
