"""启动 Poker Battle 1.8 网页版。"""
import argparse
def main():
    p = argparse.ArgumentParser(description="Poker Battle 1.8")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true")
    a = p.parse_args()
    from wargame.web import serve
    serve(a.host, a.port, not a.no_browser)
if __name__ == "__main__":
    main()
