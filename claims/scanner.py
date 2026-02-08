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

        Uses the Data API with redeemable=true filter for efficiency.
        Returns list of ClaimItem objects ready for claiming.
        """
        try:
            # Get redeemable positions directly from API
            # This is more efficient than fetching all and filtering
            positions = await self._get_redeemable_positions()

            if not positions:
                log.debug("No redeemable positions found")
                return []

            log.info(f"Found {len(positions)} redeemable positions from API")

            # Convert to ClaimItems
            claimables = []
            for pos in positions:
                claim_item = self._create_claim_item_from_data_api(pos)
                if claim_item:
                    claimables.append(claim_item)
                    log.info(
                        f"  -> {claim_item.market_slug}: {claim_item.shares:.2f} shares "
                        f"({claim_item.side}) - ${claim_item.total_payout:.2f}"
                    )

            log.info(f"Total {len(claimables)} claimable positions ready")
            return claimables

        except Exception as e:
            log.error(f"Error scanning for claimables: {e}")
            return []

    def _create_claim_item_from_data_api(self, pos: dict) -> Optional[ClaimItem]:
        """
        Create ClaimItem from Data API response format.

        Data API response format:
        {
            "conditionId": "0x...",
            "title": "Will BTC price...",
            "slug": "btc-15m-...",
            "outcome": "Up",
            "outcomeIndex": 0,
            "size": 25.0,
            "avgPrice": 0.95,
            "currentValue": 25.0,
            "cashPnl": 1.25,
            "redeemable": true,
            "asset": "...",
            "proxyWallet": "0x...",
            "negativeRisk": false
        }
        """
        try:
            # Extract fields from Data API format
            condition_id = pos.get("conditionId", "")
            market_slug = pos.get("slug", "")
            title = pos.get("title", "")
            outcome = pos.get("outcome", "")
            asset = pos.get("asset", "")  # token_id
            outcome_index = pos.get("outcomeIndex", 0)
            neg_risk = pos.get("negativeRisk", False)

            shares = float(pos.get("size", 0))
            avg_price = float(pos.get("avgPrice", 0))
            current_value = float(pos.get("currentValue", 0))
            cash_pnl = float(pos.get("cashPnl", 0))

            if shares <= 0:
                return None

            # Generate unique claim_id
            claim_id = self._generate_claim_id(asset, condition_id, self.config.funder)

            # Determine side from outcome
            outcome_lower = outcome.lower()
            if "yes" in outcome_lower or "up" in outcome_lower:
                side = "UP"
            elif "no" in outcome_lower or "down" in outcome_lower:
                side = "DOWN"
            else:
                side = outcome.upper()

            # Redeemable positions are always winners (they have value)
            won = True
            payout_per_share = 1.0
            total_payout = shares * payout_per_share

            return ClaimItem(
                claim_id=claim_id,
                market_id=condition_id,
                market_slug=market_slug,
                token_id=asset,
                shares=shares,
                entry_price=avg_price,
                won=won,
                payout_per_share=payout_per_share,
                total_payout=total_payout,
                resolved_at=int(time.time()),  # API doesn't provide this
                claim_type=ClaimType.REDEEM_WINNINGS,
                side=side,
                outcome_index=outcome_index,
                neg_risk=neg_risk,
            )

        except Exception as e:
            log.error(f"Error creating ClaimItem from Data API: {e}")
            return None

    async def _get_user_positions(self) -> list[dict]:
        """
        Get user's open positions from Polymarket Data API.

        Uses the official Data API endpoint:
        https://data-api.polymarket.com/positions

        Docs: https://docs.polymarket.com/developers/misc-endpoints/data-api-get-positions
        """
        if not self.config.funder:
            log.error("No funder address configured")
            return []

        try:
            # Use the official Data API endpoint
            url = "https://data-api.polymarket.com/positions"
            params = {
                "user": self.config.funder,
                "sizeThreshold": "0.01",  # Include small positions
                "limit": "500",
            }

            resp = await self.client.get(url, params=params)

            if resp.status_code == 200:
                positions = resp.json()
                log.info(f"Data API returned {len(positions)} positions")
                return positions

            log.error(f"Data API positions failed: {resp.status_code} - {resp.text}")
            return []

        except Exception as e:
            log.error(f"Error getting positions: {e}")
            return []

    async def _get_redeemable_positions(self) -> list[dict]:
        """
        Get only redeemable (claimable) positions directly from API.

        This is more efficient as it filters server-side.
        """
        if not self.config.funder:
            log.error("No funder address configured")
            return []

        try:
            url = "https://data-api.polymarket.com/positions"
            params = {
                "user": self.config.funder,
                "redeemable": "true",  # Only get claimable positions
                "sizeThreshold": "0.01",
                "limit": "500",
            }

            resp = await self.client.get(url, params=params)

            if resp.status_code == 200:
                positions = resp.json()
                log.info(f"Found {len(positions)} redeemable positions")
                return positions

            log.error(f"Redeemable positions failed: {resp.status_code}")
            return []

        except Exception as e:
            log.error(f"Error getting redeemable positions: {e}")
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
