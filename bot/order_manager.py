"""
Intelligent POST ONLY order management.

Implements smart order placement with:
- Entry price calculation (best_bid + delta)
- Liquidity verification at target level
- Fill probability estimation
- Automatic retry cycle with timeout
"""

import asyncio
import time
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable, Any

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.config import BotConfig


log = logging.getLogger("order_manager")


class OrderState(Enum):
    """Order lifecycle states."""
    PENDING = "pending"
    IN_BOOK = "in_book"
    FILLED = "filled"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    FAILED = "failed"


@dataclass
class OrderBookSnapshot:
    """Snapshot of order book state."""
    best_bid: float
    best_ask: float
    bid_depth: float  # Total depth at best bid
    ask_depth: float  # Total depth at best ask
    total_bid_depth: float  # Total bid depth (all levels)
    total_ask_depth: float  # Total ask depth (all levels)
    spread: float
    mid: float
    timestamp: float


@dataclass
class OrderAttempt:
    """Record of a single order attempt."""
    attempt_number: int
    entry_price: float
    size: float
    placed_at: float
    cancelled_at: Optional[float] = None
    filled_at: Optional[float] = None
    state: OrderState = OrderState.PENDING
    reason: str = ""


@dataclass
class OrderResult:
    """Final result of order execution."""
    success: bool
    filled: bool
    entry_price: float
    size: float
    total_attempts: int
    attempts: list[OrderAttempt]
    total_time_s: float
    reason: str


@dataclass
class OrderManagerConfig:
    """Configuration for order management."""
    # Price delta to add to best_bid (in price units, e.g., 0.001 = 0.1 cent)
    entry_delta: float = 0.001

    # Minimum delta (never go lower than this)
    min_delta: float = 0.001

    # Maximum delta (never go higher than this)
    max_delta: float = 0.02

    # Timeout before cancelling order (seconds)
    order_timeout_s: float = 5.0

    # Maximum number of retry attempts
    max_attempts: int = 10

    # Minimum liquidity at our price level (in shares)
    min_liquidity_at_level: float = 50.0

    # Minimum remaining time to attempt entry (seconds)
    min_remaining_time_s: float = 30.0

    # Check interval when order is in book (seconds)
    check_interval_s: float = 0.5

    # Aggressive mode: use best_ask on final attempts
    aggressive_final_attempts: int = 2

    # Fill probability thresholds
    min_fill_probability: float = 0.30  # Don't enter if prob < 30%


class FillProbabilityEstimator:
    """
    Estimate probability of order being filled before window end.

    Uses heuristics based on:
    - Time remaining
    - Order book dynamics
    - Historical fill rates
    """

    @staticmethod
    def estimate(
        our_price: float,
        book: OrderBookSnapshot,
        remaining_s: float,
        side: str = "BUY",
    ) -> float:
        """
        Estimate fill probability for a POST ONLY order.

        Args:
            our_price: Our limit price
            book: Current order book snapshot
            remaining_s: Seconds until window ends
            side: "BUY" or "SELL"

        Returns:
            Probability between 0 and 1
        """
        if remaining_s <= 0:
            return 0.0

        if side == "BUY":
            # For BUY: we're at best_bid level, waiting for seller to hit us
            # Higher probability if:
            # - Our price is close to best_ask (smaller spread to cross)
            # - More time remaining
            # - Lower ask depth (easier for price to move)

            spread_to_cross = book.best_ask - our_price
            spread_pct = spread_to_cross / book.mid if book.mid > 0 else 1.0

            # Base probability from time remaining
            # More time = higher chance of fill
            time_factor = min(remaining_s / 60.0, 1.0)  # Normalize to 60s

            # Spread factor: smaller spread = easier to fill
            # If spread is 0, we'd be a taker (which POST ONLY prevents)
            # If spread is large, harder to fill
            spread_factor = max(0, 1.0 - spread_pct * 5)  # 20% spread = 0 factor

            # Liquidity factor: less depth = more volatile = more likely to fill
            depth_factor = 1.0 / (1.0 + book.ask_depth / 1000.0)

            # Combine factors
            base_prob = time_factor * 0.4 + spread_factor * 0.4 + depth_factor * 0.2

            return min(max(base_prob, 0.0), 1.0)

        else:
            # For SELL: mirror logic
            spread_to_cross = our_price - book.best_bid
            spread_pct = spread_to_cross / book.mid if book.mid > 0 else 1.0

            time_factor = min(remaining_s / 60.0, 1.0)
            spread_factor = max(0, 1.0 - spread_pct * 5)
            depth_factor = 1.0 / (1.0 + book.bid_depth / 1000.0)

            base_prob = time_factor * 0.4 + spread_factor * 0.4 + depth_factor * 0.2

            return min(max(base_prob, 0.0), 1.0)


class OrderManager:
    """
    Intelligent POST ONLY order manager.

    Workflow:
    1. Calculate entry price as best_bid + delta
    2. Verify liquidity at target level
    3. Estimate fill probability
    4. Place POST ONLY order
    5. Wait up to timeout_s
    6. If not filled, cancel and retry at new best_bid + delta
    7. Repeat until filled, max_attempts, or time runs out
    """

    def __init__(
        self,
        config: OrderManagerConfig,
        bot_config: Optional[Any] = None,
        place_order_fn: Optional[Callable] = None,
        cancel_order_fn: Optional[Callable] = None,
        get_order_status_fn: Optional[Callable] = None,
    ):
        """
        Initialize order manager.

        Args:
            config: Order manager configuration
            bot_config: Bot configuration (optional for paper trading)
            place_order_fn: Async function to place order
            cancel_order_fn: Async function to cancel order
            get_order_status_fn: Async function to get order status
        """
        self.config = config
        self.bot_config = bot_config
        self.place_order_fn = place_order_fn
        self.cancel_order_fn = cancel_order_fn
        self.get_order_status_fn = get_order_status_fn
        self.estimator = FillProbabilityEstimator()

    def calculate_entry_price(
        self,
        book: OrderBookSnapshot,
        side: str,
        attempt: int = 1,
    ) -> float:
        """
        Calculate optimal entry price.

        For BUY: best_bid + delta (to be at top of bid queue)
        For SELL: best_ask - delta (to be at top of ask queue)

        Args:
            book: Current order book
            side: "BUY" or "SELL"
            attempt: Current attempt number (higher = more aggressive)

        Returns:
            Entry price
        """
        # Scale delta based on attempt number
        # First attempts: conservative
        # Later attempts: more aggressive
        attempts_remaining = self.config.max_attempts - attempt + 1

        if attempts_remaining <= self.config.aggressive_final_attempts:
            # Final attempts: be more aggressive
            delta = self.config.max_delta
        else:
            # Normal attempts: use base delta
            delta = self.config.entry_delta

        if side == "BUY":
            # Place just above best_bid to be at top of queue
            price = book.best_bid + delta
            # But never above best_ask (would be taker)
            price = min(price, book.best_ask - 0.001)
        else:
            # Place just below best_ask
            price = book.best_ask - delta
            # But never below best_bid (would be taker)
            price = max(price, book.best_bid + 0.001)

        # Round to 3 decimal places (Polymarket precision)
        return round(price, 3)

    def verify_liquidity(
        self,
        entry_price: float,
        book: OrderBookSnapshot,
        side: str,
    ) -> tuple[bool, str]:
        """
        Verify there's sufficient liquidity at our entry level.

        Args:
            entry_price: Our target entry price
            book: Current order book
            side: "BUY" or "SELL"

        Returns:
            (has_liquidity, reason)
        """
        if side == "BUY":
            # For BUY: check ask depth (that's what we need to get filled)
            if book.ask_depth < self.config.min_liquidity_at_level:
                return False, f"Insufficient ask depth: {book.ask_depth:.0f} < {self.config.min_liquidity_at_level}"

            # Check that our price makes sense
            if entry_price >= book.best_ask:
                return False, f"Entry price {entry_price} >= best_ask {book.best_ask}"

        else:
            # For SELL: check bid depth
            if book.bid_depth < self.config.min_liquidity_at_level:
                return False, f"Insufficient bid depth: {book.bid_depth:.0f} < {self.config.min_liquidity_at_level}"

            if entry_price <= book.best_bid:
                return False, f"Entry price {entry_price} <= best_bid {book.best_bid}"

        return True, "OK"

    async def execute_with_retry(
        self,
        token_id: str,
        side: str,
        size: float,
        get_book_fn: Callable[[], OrderBookSnapshot],
        remaining_s: float,
    ) -> OrderResult:
        """
        Execute order with intelligent retry cycle.

        Args:
            token_id: Token to trade
            side: "BUY" or "SELL"
            size: Number of shares
            get_book_fn: Function to get current order book
            remaining_s: Seconds until window ends

        Returns:
            OrderResult with execution details
        """
        start_time = time.time()
        attempts: list[OrderAttempt] = []

        for attempt_num in range(1, self.config.max_attempts + 1):
            # Check if we have enough time
            elapsed = time.time() - start_time
            time_left = remaining_s - elapsed

            if time_left < self.config.min_remaining_time_s:
                return OrderResult(
                    success=False,
                    filled=False,
                    entry_price=0,
                    size=size,
                    total_attempts=attempt_num - 1,
                    attempts=attempts,
                    total_time_s=elapsed,
                    reason=f"Not enough time remaining: {time_left:.0f}s < {self.config.min_remaining_time_s}s",
                )

            # Get current book
            book = get_book_fn()

            # Calculate entry price
            entry_price = self.calculate_entry_price(book, side, attempt_num)

            # Verify liquidity
            has_liquidity, liquidity_reason = self.verify_liquidity(entry_price, book, side)
            if not has_liquidity:
                log.warning(f"[Attempt {attempt_num}] Liquidity check failed: {liquidity_reason}")
                await asyncio.sleep(0.5)
                continue

            # Estimate fill probability
            fill_prob = self.estimator.estimate(entry_price, book, time_left, side)

            if fill_prob < self.config.min_fill_probability:
                log.warning(
                    f"[Attempt {attempt_num}] Fill probability too low: "
                    f"{fill_prob:.0%} < {self.config.min_fill_probability:.0%}"
                )
                # Don't retry immediately, wait a bit
                await asyncio.sleep(1.0)
                continue

            # Create attempt record
            attempt = OrderAttempt(
                attempt_number=attempt_num,
                entry_price=entry_price,
                size=size,
                placed_at=time.time(),
                state=OrderState.PENDING,
            )
            attempts.append(attempt)

            log.info(
                f"[Attempt {attempt_num}/{self.config.max_attempts}] "
                f"Placing POST ONLY {side} @ {entry_price:.3f} "
                f"(best_bid={book.best_bid:.3f}, best_ask={book.best_ask:.3f}, "
                f"fill_prob={fill_prob:.0%})"
            )

            # Place order
            is_simulation = (
                self.bot_config is None or
                getattr(self.bot_config, 'dry_run', True) or
                getattr(self.bot_config, 'paper_trading', True)
            )

            if is_simulation:
                # Simulate order placement
                order_id = f"sim_{int(time.time() * 1000)}"
                attempt.state = OrderState.IN_BOOK
            else:
                # Real order placement
                if self.place_order_fn:
                    try:
                        order = await self.place_order_fn(
                            token_id=token_id,
                            side=side,
                            size=size,
                            price=entry_price,
                        )
                        if order is None:
                            attempt.state = OrderState.FAILED
                            attempt.reason = "Place order returned None"
                            continue
                        order_id = order.order_id
                        attempt.state = OrderState.IN_BOOK
                    except Exception as e:
                        attempt.state = OrderState.FAILED
                        attempt.reason = str(e)
                        log.error(f"Failed to place order: {e}")
                        continue
                else:
                    attempt.state = OrderState.FAILED
                    attempt.reason = "No place_order_fn provided"
                    continue

            # Wait for fill or timeout
            order_start = time.time()
            filled = False

            while time.time() - order_start < self.config.order_timeout_s:
                # Check time remaining in window
                time_left = remaining_s - (time.time() - start_time)
                if time_left < 5:  # Last 5 seconds, give up
                    break

                # Check order status
                if is_simulation:
                    # Simulate: check if book moved in our favor
                    new_book = get_book_fn()
                    if side == "BUY":
                        # Filled if ask came down to our price
                        if new_book.best_ask <= entry_price:
                            filled = True
                            break
                    else:
                        # Filled if bid came up to our price
                        if new_book.best_bid >= entry_price:
                            filled = True
                            break
                else:
                    # Real order status check
                    if self.get_order_status_fn:
                        try:
                            status = await self.get_order_status_fn(order_id)
                            if status and status.status.value == "filled":
                                filled = True
                                break
                        except Exception as e:
                            log.warning(f"Error checking order status: {e}")

                await asyncio.sleep(self.config.check_interval_s)

            if filled:
                attempt.filled_at = time.time()
                attempt.state = OrderState.FILLED

                total_time = time.time() - start_time
                log.info(
                    f"Order FILLED on attempt {attempt_num} "
                    f"@ {entry_price:.3f} after {total_time:.1f}s"
                )

                return OrderResult(
                    success=True,
                    filled=True,
                    entry_price=entry_price,
                    size=size,
                    total_attempts=attempt_num,
                    attempts=attempts,
                    total_time_s=total_time,
                    reason="filled",
                )

            # Not filled - cancel and retry
            attempt.cancelled_at = time.time()
            attempt.state = OrderState.TIMEOUT
            attempt.reason = f"Timeout after {self.config.order_timeout_s}s"

            log.info(f"[Attempt {attempt_num}] Order timeout, cancelling...")

            if not is_simulation:
                if self.cancel_order_fn:
                    try:
                        await self.cancel_order_fn(order_id)
                    except Exception as e:
                        log.warning(f"Error cancelling order: {e}")

            # Small delay before retry
            await asyncio.sleep(0.2)

        # All attempts exhausted
        total_time = time.time() - start_time
        return OrderResult(
            success=False,
            filled=False,
            entry_price=0,
            size=size,
            total_attempts=len(attempts),
            attempts=attempts,
            total_time_s=total_time,
            reason=f"Max attempts ({self.config.max_attempts}) exhausted",
        )


def create_book_snapshot_from_polymarket_data(data: dict) -> OrderBookSnapshot:
    """
    Create OrderBookSnapshot from Polymarket recorder data format.

    Args:
        data: Row from Polymarket book recorder

    Returns:
        OrderBookSnapshot
    """
    yes_data = data.get("yes", {}) or {}

    best_bid = yes_data.get("best_bid", 0)
    best_ask = yes_data.get("best_ask", 0)
    bid_depth = yes_data.get("bid_depth", 0)
    ask_depth = yes_data.get("ask_depth", 0)
    spread = yes_data.get("spread", 0)
    mid = yes_data.get("mid", 0)

    return OrderBookSnapshot(
        best_bid=best_bid,
        best_ask=best_ask,
        bid_depth=bid_depth,
        ask_depth=ask_depth,
        total_bid_depth=bid_depth,
        total_ask_depth=ask_depth,
        spread=spread,
        mid=mid,
        timestamp=data.get("ts_ms", 0) / 1000.0,
    )
