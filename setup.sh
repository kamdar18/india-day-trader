#!/usr/bin/env bash
# One-time setup on the DigitalOcean droplet (Ubuntu). Run: bash setup.sh
set -e
cd "$(dirname "$0")"
command -v python3 >/dev/null || { sudo apt-get update && sudo apt-get install -y python3; }
python3 -c 'import sys; assert sys.version_info >= (3, 8), "need Python 3.8+"'
[ -f config.json ] || cp config.example.json config.json
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo ">> Now edit .env and put your Dhan client id and access token:  nano .env"
fi
chmod 600 .env
python3 -m unittest discover -s tests
echo
echo ">> Setup done. Next:"
echo "   1) nano .env                       (Dhan client id + access token)"
echo "   2) python3 trader.py doctor        (checks Dhan and shows this server's IP)"
echo "   3) Whitelist that IP in web.dhan.co > Profile > DhanHQ APIs > Static IP"
echo "   4) python3 trader.py instruments --refresh"
echo "   5) python3 trader.py scan          (after 9:30 AM on a market day)"
