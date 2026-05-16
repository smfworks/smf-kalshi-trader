"""
Kalshi API Authentication — RSA Key-Pair Signing
Consolidated from kalshi_auth.py. Single auth path for the entire system.

Usage:
    from kalshi.auth import KalshiAuth
    auth = KalshiAuth()
    markets, cursor = auth.get_markets()
"""

import os
import datetime
import base64
from urllib.parse import urlparse
from pathlib import Path

import requests
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import load_dotenv

# Load from kalshi.env in workspace root
ENV_PATH = Path(__file__).resolve().parent.parent / "kalshi.env"
load_dotenv(ENV_PATH)


class KalshiAuth:
    """Kalshi API authentication using RSA key-pair signing.

    Credentials come from kalshi.env (never hardcoded in source).
    """

    def __init__(self, api_key_id=None, private_key_path=None, base_url=None):
        self.api_key_id = api_key_id or os.getenv("KALSHI_API_KEY_ID")
        self.private_key_path = private_key_path or os.getenv(
            "KALSHI_PRIVATE_KEY_PATH", "kalshi_private.key"
        )
        # Resolve relative to workspace root
        if not os.path.isabs(self.private_key_path):
            self.private_key_path = str(
                Path(__file__).resolve().parent.parent / self.private_key_path
            )
        self.base_url = base_url or os.getenv(
            "KALSHI_BASE_URL", "https://external-api.kalshi.com/trade-api/v2"
        )

        self._private_key = None
        self._validate_config()

    def _validate_config(self):
        if not self.api_key_id:
            raise ValueError(
                "KALSHI_API_KEY_ID not set. Check kalshi.env"
            )
        if not os.path.exists(self.private_key_path):
            raise ValueError(
                f"Private key not found at {self.private_key_path}"
            )

    def _load_private_key(self):
        if self._private_key is None:
            with open(self.private_key_path, "rb") as f:
                self._private_key = serialization.load_pem_private_key(
                    f.read(), password=None, backend=default_backend()
                )
        return self._private_key

    def _create_signature(self, timestamp, method, path):
        path_without_query = path.split("?")[0]
        message = f"{timestamp}{method}{path_without_query}".encode("utf-8")

        private_key = self._load_private_key()
        signature = private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")

    def request(self, method, path, json_payload=None, timeout=15, params=None):
        """Make an authenticated request to Kalshi API."""
        timestamp = str(int(datetime.datetime.now().timestamp() * 1000))
        sign_path = urlparse(self.base_url + path).path
        signature = self._create_signature(timestamp, method, sign_path)

        headers = {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }

        url = self.base_url + path

        if method == "GET":
            return requests.get(
                url, headers=headers, params=params, timeout=timeout
            )
        elif method == "POST":
            return requests.post(
                url, headers=headers, json=json_payload, timeout=timeout
            )
        elif method == "DELETE":
            return requests.delete(url, headers=headers, timeout=timeout)
        elif method == "PUT":
            return requests.put(
                url, headers=headers, json=json_payload, timeout=timeout
            )
        else:
            raise ValueError(f"Unsupported method: {method}")

    # -- Convenience methods --

    def get(self, path, params=None, timeout=15):
        return self.request("GET", path, params=params, timeout=timeout)

    def post(self, path, json_payload=None, timeout=15):
        return self.request("POST", path, json_payload=json_payload, timeout=timeout)

    def delete(self, path, timeout=15):
        return self.request("DELETE", path, timeout=timeout)

    # -- Kalshi-specific endpoints --

    def get_balance(self):
        """Get account balance in dollars."""
        r = self.get("/portfolio/balance")
        if r.status_code == 200:
            return r.json().get("balance", 0) / 100.0
        return None

    def get_positions(self):
        """Get open positions."""
        r = self.get("/portfolio/positions")
        if r.status_code == 200:
            return r.json()
        return None

    def get_orders(self):
        """Get active orders."""
        r = self.get("/portfolio/orders")
        if r.status_code == 200:
            return r.json()
        return None

    def place_order(self, ticker, side, action, count, price_cents,
                    client_order_id=None, order_type="limit",
                    time_in_force="good_till_canceled"):
        """Place an order on Kalshi.

        Args:
            ticker: Market ticker
            side: 'yes' or 'no'
            action: 'buy' or 'sell'
            count: Number of contracts
            price_cents: Price in cents (1-99)
            client_order_id: Optional client order ID
            order_type: 'limit' or 'market'
            time_in_force: 'good_till_canceled', 'fill_or_kill', 'immediate_or_cancel'
        """
        import uuid

        if client_order_id is None:
            client_order_id = f"gabriel-{uuid.uuid4().hex[:8]}"

        payload = {
            "ticker": ticker,
            "client_order_id": client_order_id,
            "side": side,
            "action": action,
            "count": count,
            "type": order_type,
            "yes_price": price_cents,
            "time_in_force": time_in_force,
        }

        r = self.post("/portfolio/orders", json_payload=payload)

        return {
            "status_code": r.status_code,
            "response": r.json() if r.status_code < 300 else r.text[:500],
            "order_id": r.json().get("order", {}).get("order_id")
            if r.status_code < 300
            else None,
        }

    def cancel_order(self, order_id):
        """Cancel an order by ID."""
        r = self.delete(f"/portfolio/orders/{order_id}")
        return {
            "status_code": r.status_code,
            "response": r.json() if r.status_code < 300 else r.text[:500],
        }

    def get_markets(self, limit=100, cursor=""):
        """Fetch a page of markets. Returns (markets, next_cursor)."""
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor

        r = self.get("/markets", params=params)
        if r.status_code == 200:
            data = r.json()
            return data.get("markets", []), data.get("cursor", "")
        return [], ""

    def get_all_markets(self, max_pages=0):
        """Fetch all markets with pagination."""
        all_markets = []
        cursor = ""
        page = 0

        while True:
            markets, cursor = self.get_markets(limit=100, cursor=cursor)
            if not markets:
                break
            all_markets.extend(markets)
            page += 1
            if max_pages > 0 and page >= max_pages:
                break
            if not cursor:
                break

        return all_markets
