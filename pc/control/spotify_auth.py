"""執行一次性的 Spotify PKCE 授權。"""

from __future__ import annotations

import argparse
import sys

from .spotify import SpotifyClient, SpotifyError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--client-id",
        help="Spotify Developer App 的公開 Client ID；成功後保存到 repo 外",
    )
    parser.add_argument(
        "--redirect-uri",
        default=None,
        help="須與 Dashboard 完全相同；預設 http://127.0.0.1:8888/callback",
    )
    args = parser.parse_args()
    try:
        client = SpotifyClient(
            client_id=args.client_id,
            redirect_uri=args.redirect_uri,
        )
        client.authenticate()
        client.save_config()
    except SpotifyError as exc:
        print(f"Spotify 授權失敗：{exc}", file=sys.stderr)
        return 1
    print("Spotify 授權成功。")
    print(f"公開設定：{client.config_path}")
    print(f"Token：{client.token_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
