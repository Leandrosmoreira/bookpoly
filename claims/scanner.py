"""
Claim Scanner - Detects claimable positions in resolved markets.

Scans user positions and identifies which ones are in resolved markets
that can be claimed via the SELL@0.99 workaround.
"""
import asyncio
import hashlib
import httpx
import logging
import time
from typing import Optional
from claims.config import ClaimConfig
from claims.models import ClaimItem, ClaimType

log = logging.getLogger(__name__)


class ClaimScanner:
    """
    Scans for claimable positions.

    Flow:
    1. Get user positions from Polymarket API
    2. Check which markets are resolved
    3. Filter positions in resolved markets
    4. Return ClaimItems for each claimable position
    """

    def __init__(self, config: ClaimConfig):
        self.config = config
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()

    async def scan_claimables(self) -> list[ClaimItem]:
        """
        Scan for all claimable positions.

        Returns list of ClaimItem objects ready for claiming.
        """
        try:
            # 1. Get user positions
            positions = await self._get_user_positions()
            if not positions:
                log.debug("No open positions found")
                return []

            log.info(f"Found {len(positions)} positions to check")

            # 2. Get resolved markets
            resolved_markets = await self._get_resolved_markets()
            resolved_ids = {m["condition_id"] for m in resolved_markets}

            if not resolved_ids:
                log.debug("No resolved markets found")
                return []

            log.info(f"Found {len(resolved_ids)} resolved markets")

            # 3. Filter positions in resolved markets
            claimables = []
            for pos in positions:
                condition_id = pos.get("condition_id") or pos.get("market_id")

                if condition_id not in resolved_ids:
                    continue

                # Find the resolved market info
                market_info = next(
                    (m for m in resolved_markets if m["condition_id"] == condition_id),
                    None
                )

                if not market_info:
                    continue

                # Check if enough time has passed since resolution
                resolved_at = market_info.get("resolved_at", 0)
                if time.time() - resolved_at < self.config.wait_after_resolution_s:
                    log.debug(
                        f"Market {condition_id} resolved too recently, "
                        f"waiting {self.config.wait_after_resolution_s}s"
                    )
                    continue

                # Create ClaimItem
                claim_item = self._create_claim_item(pos, market_info)
                if claim_item:
                    claimables.append(claim_item)

            log.info(f"Found {len(claimables)} claimable positions")
            return claimables

        except Exception as e:
            log.error(f"Error scanning for claimables: {e}")
            return []

    async def _get_user_positions(self) -> list[dict]:
        """
        Get user's open positions from Polymarket.

        Uses the CLOB API /positions endpoint or equivalent.
        """
        if not self.config.funder:
            log.error("No funder address configured")
            return []

        try:
            # Try to get positions from CLOB API
            # Note: This endpoint may require authentication
            url = f"{self.config.clob_base_url}/positions"
            params = {"user": self.config.funder}

            headers = self._get_auth_headers("GET", "/positions")

            resp = await self.client.get(url, params=params, headers=headers)

            if resp.status_code == 200:
                return resp.json()

            # If CLOB fails, try Gamma API
            log.warning(f"CLOB positions failed ({resp.status_code}), trying Gamma")
            return await self._get_positions_from_gamma()

        except Exception as e:
            log.error(f"Error getting positions: {e}")
            return []

    async def _get_positions_from_gamma(self) -> list[dict]:
        """
        Get positions from Gamma API as fallback.
        """
        try:
            url = f"{self.config.gamma_base_url}/positions"
            params = {"user": self.config.funder}

            resp = await self.client.get(url, params=params)

            if resp.status_code == 200:
                return resp.json()

            log.error(f"Gamma positions failed: {resp.status_code}")
            return []

        except Exception as e:
            log.error(f"Error getting positions from Gamma: {e}")
            return []

    async def _get_resolved_markets(self) -> list[dict]:
        """
        Get list of resolved markets.

        Filters for markets that:
        1. Are resolved (have a winning outcome)
        2. Were resolved recently (within 24h)
        3. Match our market_slugs filter (if configured)
        """
        try:
            # Gamma API for resolved markets
            url = f"{self.config.gamma_base_url}/markets"
            params = {
                "closed": "true",
                "limit": 100,
            }

            resp = await self.client.get(url, params=params)

            if resp.status_code != 200:
                log.error(f"Failed to get resolved markets: {resp.status_code}")
                return []

            markets = resp.json()

            # Filter for recent resolutions
            cutoff = time.time() - 86400  # 24 hours
            resolved = []

            for m in markets:
                # Check if resolved
                if not m.get("closed") or not m.get("resolved"):
                    continue

                # Check resolution time
                resolved_at = m.get("end_date_iso")
                if resolved_at:
                    # Parse ISO date to timestamp
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(resolved_at.replace("Z", "+00:00"))
                        resolved_ts = dt.timestamp()
                        if resolved_ts < cutoff:
                            continue
                        m["resolved_at"] = resolved_ts
                    except:
                        m["resolved_at"] = time.time()

                # Filter by market slugs if configured
                if self.config.market_slugs:
                    slug = m.get("slug", "")
                    if not any(s in slug for s in self.config.market_slugs):
                        continue

                resolved.append(m)

            return resolved

        except Exception as e:
            log.error(f"Error getting resolved markets: {e}")
            return []

    def _create_claim_item(self, position: dict, market: dict) -> Optional[ClaimItem]:
        """
        Create a ClaimItem from position and market data.
        """
        try:
            token_id = position.get("token_id", "")
            condition_id = market.get("condition_id", "")
            market_slug = market.get("slug", "")

            if not token_id or not condition_id:
                return None

            # Determine if this position won
            # This depends on the market structure
            # For binary markets: check if our token is the winning outcome
            winning_token = market.get("winning_token_id")
            won = token_id == winning_token

            # Get position details
            shares = float(position.get("size", 0) or position.get("shares", 0))
            entry_price = float(position.get("avg_price", 0) or position.get("entry_price", 0))

            if shares <= 0:
                return None

            # Generate unique claim_id
            claim_id = self._generate_claim_id(token_id, condition_id, self.config.funder)

            # Determine side (UP/DOWN) from market structure
            side = self._determine_side(position, market)

            return ClaimItem(
                claim_id=claim_id,
                market_id=condition_id,
                market_slug=market_slug,
                token_id=token_id,
                shares=shares,
                entry_price=entry_price,
                won=won,
                payout_per_share=1.0 if won else 0.0,
                total_payout=shares if won else 0.0,
                resolved_at=int(market.get("resolved_at", time.time())),
                claim_type=ClaimType.REDEEM_WINNINGS if won else ClaimType.REDEEM_LOSING,
                side=side,
            )

        except Exception as e:
            log.error(f"Error creating ClaimItem: {e}")
            return None

    def _generate_claim_id(self, token_id: str, condition_id: str, user: str) -> str:
        """Generate unique claim ID."""
        data = f"{token_id}_{condition_id}_{user}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    def _determine_side(self, position: dict, market: dict) -> str:
        """Determine if position is UP or DOWN."""
        # This depends on market structure
        # For BTC 15m markets, check token name or metadata
        token_id = position.get("token_id", "")

        # Check market tokens
        tokens = market.get("tokens", [])
        for t in tokens:
            if t.get("token_id") == token_id:
                outcome = t.get("outcome", "").lower()
                if "yes" in outcome or "up" in outcome:
                    return "UP"
                if "no" in outcome or "down" in outcome:
                    return "DOWN"

        return "UNKNOWN"

    def _get_auth_headers(self, method: str, path: str) -> dict:
        """Generate authentication headers for CLOB API."""
        if not self.config.api_key or not self.config.api_secret:
            return {}

        import hmac
        import hashlib

        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}{method}{path}"

        signature = hmac.new(
            self.config.api_secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        return {
            "POLY_API_KEY": self.config.api_key,
            "POLY_TIMESTAMP": timestamp,
            "POLY_SIGNATURE": signature,
        }
