"""
Scanner for redeemable positions.

SECURITY: Uses only httpx (already in project, trusted).
Queries public Polymarket Data API - no auth needed.
"""
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from .config import DATA_API_URL, ClaimConfig

log = logging.getLogger(__name__)


@dataclass
class RedeemablePosition:
    """A position that can be redeemed."""

    condition_id: str  # Market condition ID (32-byte hex)
    token_id: str  # Position token ID
    outcome: str  # "Up", "Down", "Yes", "No"
    outcome_index: int  # 0 or 1
    shares: float  # Amount of shares
    market_slug: str  # Market identifier
    title: str  # Market title


class PositionScanner:
    """
    Scans for redeemable positions using public Data API.

    SECURITY:
    - Uses only httpx (trusted, already in project)
    - Queries public API (no auth needed)
    - Does NOT send any credentials
    """

    def __init__(self, config: ClaimConfig):
        self.config = config
        self.client = httpx.Client(timeout=30.0)

    def close(self):
        """Close HTTP client."""
        self.client.close()

    def scan(self) -> list[RedeemablePosition]:
        """
        Scan for redeemable positions.

        Returns list of positions that can be redeemed.
        """
        if not self.config.wallet_address:
            log.error("wallet_address not configured")
            return []

        try:
            # Query public Data API
            url = f"{DATA_API_URL}/positions"
            params = {
                "user": self.config.wallet_address,
                "redeemable": "true",
                "sizeThreshold": "0.01",
                "limit": "100"
            }

            resp = self.client.get(url, params=params)
            resp.raise_for_status()

            positions = resp.json()
            result = []
            for pos in positions:
                item = self._parse_position(pos)
                if item:
                    result.append(item)

            return result

        except httpx.HTTPError as e:
            log.error(f"HTTP error scanning positions: {e}")
            return []
        except Exception as e:
            log.error(f"Error scanning positions: {e}")
            return []

    def _parse_position(self, pos: dict) -> Optional[RedeemablePosition]:
        """Parse position from API response."""
        try:
            condition_id = pos.get("conditionId", "")
            token_id = pos.get("asset", "")
            outcome = pos.get("outcome", "")
            outcome_index = pos.get("outcomeIndex", 0)
            shares = float(pos.get("size", 0))
            market_slug = pos.get("slug", "")
            title = pos.get("title", "")

            if not condition_id or not token_id or shares <= 0:
                return None

            return RedeemablePosition(
                condition_id=condition_id,
                token_id=token_id,
                outcome=outcome,
                outcome_index=outcome_index,
                shares=shares,
                market_slug=market_slug,
                title=title
            )

        except Exception as e:
            log.warning(f"Failed to parse position: {e}")
            return None
