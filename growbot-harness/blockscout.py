"""
blockscout.py · stdlib-only Blockscout REST v2 client (read path)
=================================================================

Story's testnet explorer (Storyscan) runs Blockscout, so the verify page reads
the on-chain anchor over the standard Blockscout REST v2 API -- no node, no
private key, no auth. Uses only urllib + json so the entire verification path
stays dependency-free (consistent with the cert's zero-dependency integrity core).

Story Aeneid testnet:  chainId 1315
  RPC      https://aeneid.storyrpc.io        (write path / web3, not used here)
  Explorer https://aeneid.storyscan.xyz
  API base https://aeneid.storyscan.xyz/api/v2/   <- confirmed via safe-eth-py
Story mainnet API base: https://mainnet.storyscan.xyz/api/v2/

NOTE: there is also a .io host (aeneid.storyscan.io). Smoke-test the API base
with a known tx before relying on it; the UI host and API host can differ.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

AENEID_API = "https://aeneid.storyscan.xyz/api/v2/"
MAINNET_API = "https://mainnet.storyscan.xyz/api/v2/"


class Blockscout:
    def __init__(self, base_url: str = AENEID_API, timeout: float = 15.0):
        self.base = base_url if base_url.endswith("/") else base_url + "/"
        self.timeout = timeout
        self.ssl_context = self._ssl_context()

    @staticmethod
    def _ssl_context():
        try:
            import certifi

            return ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            return ssl.create_default_context()

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = urllib.parse.urljoin(self.base, path.lstrip("/"))
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                   "User-Agent": "growbot-verify/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ssl_context) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return {"_error": f"http {e.code}", "_url": url}
        except urllib.error.URLError as e:
            return {"_error": str(e.reason), "_url": url}

    # --- well-supported endpoints (used by the verify path) ---------------- #
    def transaction(self, tx_hash: str) -> dict:
        """Tx info incl. status ('ok'/'error'), from/to, method."""
        tx_hash = tx_hash if tx_hash.startswith("0x") else f"0x{tx_hash}"
        return self._get(f"transactions/{tx_hash}")

    def transaction_logs(self, tx_hash: str) -> dict:
        """Decoded event logs for a tx. Primary anchor source -- robust and standard."""
        tx_hash = tx_hash if tx_hash.startswith("0x") else f"0x{tx_hash}"
        return self._get(f"transactions/{tx_hash}/logs")

    def address(self, address: str) -> dict:
        """Address/contract info (for displaying the IP Asset in the verify UI)."""
        return self._get(f"addresses/{address}")
