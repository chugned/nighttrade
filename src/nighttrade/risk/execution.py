"""Realistic execution modeling — fees, slippage, latency, partial fills.

A simulated fill must never flatter the strategy. Every fill here is *adverse*:

* **latency / base slippage** — between deciding and executing, the price
  drifts against you (``base_slippage_bps``);
* **market impact** — the more of the visible liquidity an order consumes, the
  worse the average price (``impact_slippage_bps``, scaled by participation);
* **partial fills** — an order cannot eat more than a configured fraction of
  the available liquidity; the remainder simply does not fill;
* **fees** — charged on the filled notional, both sides.

The invariant the tests pin down: a BUY always fills at or above the reference
price, a SELL at or below it. Slippage can only hurt.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..config.schema import RiskConfig
from ..models import Fill, Side

_EPS = 1e-12


def simulate_fill(
    order_id: str,
    symbol: str,
    side: Side,
    quantity: float,
    reference_price: float,
    available_liquidity: float,
    config: RiskConfig,
    timestamp: datetime | None = None,
) -> Fill:
    """Simulate a single order fill under realistic, adverse assumptions.

    Args:
        quantity: desired order size in base units.
        reference_price: the price the decision was made against.
        available_liquidity: base-unit liquidity available on the relevant
            side of the book.

    Returns:
        A :class:`Fill`. ``is_partial`` is set when liquidity capped the size.

    Raises:
        ValueError: if the order cannot fill at all (no liquidity).
    """
    if quantity <= 0:
        raise ValueError("order quantity must be > 0")
    if reference_price <= 0:
        raise ValueError("reference_price must be > 0")

    timestamp = timestamp or datetime.now(timezone.utc)

    # An order may consume at most this fraction of the available liquidity.
    max_fillable = max(available_liquidity, 0.0) * config.partial_fill_liquidity_frac
    if max_fillable <= _EPS:
        raise ValueError("no liquidity available to fill order")

    filled_qty = min(quantity, max_fillable)
    is_partial = filled_qty < quantity - _EPS

    # Participation ratio in [0, 1]: how hard this order leans on the book.
    participation = min(1.0, filled_qty / (max_fillable + _EPS))
    impact_bps = config.impact_slippage_bps * participation
    adverse_bps = config.base_slippage_bps + impact_bps
    adverse = reference_price * adverse_bps / 10_000.0

    # Slippage is always adverse: pay up to buy, sell down to sell.
    if side is Side.BUY:
        fill_price = reference_price + adverse
    else:
        fill_price = reference_price - adverse
    fill_price = max(fill_price, _EPS)

    slippage = abs(fill_price - reference_price)
    fee = fill_price * filled_qty * config.fee_bps / 10_000.0

    return Fill(
        order_id=order_id,
        symbol=symbol,
        side=side,
        quantity=round(filled_qty, 10),
        price=round(fill_price, 8),
        requested_price=round(reference_price, 8),
        fee=round(fee, 8),
        slippage=round(slippage, 8),
        timestamp=timestamp,
        is_partial=is_partial,
    )
