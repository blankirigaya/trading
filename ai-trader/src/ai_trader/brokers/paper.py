from decimal import ROUND_HALF_UP, Decimal

from ai_trader.brokers.base import Broker
from ai_trader.brokers.charges import ChargeBreakdown, ChargeRates, compute_charges
from ai_trader.domain.common import Clock
from ai_trader.domain.orders import Fill, Order, OrderRequest, OrderSide, OrderStatus, OrderType
from ai_trader.domain.portfolio import PortfolioState, Position
from ai_trader.persistence.trade_ledger import ClosedTrade, TradeLedger

PAISE = Decimal("0.01")


class PaperBroker(Broker):
    """Deterministic, immediate-fill simulator; it is not a realistic backtester.

    Cash blocking follows the intraday (MIS-style) model: a BUY blocks only
    ``value / margin_multiple`` of cash instead of the full notional, so one
    shard's book is not exhausted by a single full-price position. P&L is
    always computed on the full notional. With ``margin_multiple=1`` (default)
    behaviour is unchanged delivery-style blocking.

    ``charge_rates`` applies the Zerodha equity-intraday charge schedule to each
    fill. Entry charges are carried on the open position and netted out of the
    realized P&L when it is sold, so a round trip reports its true cost.
    """

    def __init__(
        self,
        initial_cash: Decimal,
        clock: Clock,
        prices: dict[str, Decimal] | None = None,
        slippage: Decimal = Decimal("0"),
        fee_per_order: Decimal = Decimal("0"),
        margin_multiple: Decimal = Decimal("1"),
        shard: int = 0,
        ledger: TradeLedger | None = None,
        charge_rates: ChargeRates | None = None,
    ) -> None:
        if margin_multiple <= 0:
            raise ValueError("margin_multiple must be positive")
        self.initial_cash = initial_cash
        self.clock = clock
        self.prices = prices or {}
        self.slippage = slippage
        self.fee_per_order = fee_per_order
        self.margin_multiple = margin_multiple
        self.shard = shard
        self.ledger = ledger
        self.charge_rates = charge_rates
        self.cash = initial_cash
        self.positions: dict[str, Position] = {}
        self.orders: dict[str, Order] = {}
        self.fills: list[Fill] = []
        # Entry charges paid on the quantity still open, per symbol. Netted out
        # of realized P&L on the closing sell so both legs are accounted for.
        self.entry_charges: dict[str, Decimal] = {}
        self._order_sequence = 0
        # Realized P&L survives the position it came from. A fully closed
        # position leaves ``self.positions``, so keeping the running total here
        # (and reloading it from the close history) is what lets the portfolio
        # keep reporting P&L after a round trip.
        self.realized_pnl = Decimal("0")
        # Best/worst marks per open symbol since entry, for MFE/MAE. In memory
        # only: a restart re-seeds from the first live mark, which can only
        # understate the excursion, never invent one.
        self._peaks: dict[str, Decimal] = {}
        self._troughs: dict[str, Decimal] = {}

    async def restore(self) -> None:
        """Reload the persisted open book (cash + positions) after a restart.

        No ledger attached means nothing to restore; the book starts fresh.
        """
        if self.ledger is None:
            return
        cash, positions = await self.ledger.load_book(self.shard)
        if cash is not None:
            self.cash = cash
        self.positions = {position.symbol: position for position in positions}
        self.entry_charges = await self.ledger.load_entry_charges(self.shard)
        self.realized_pnl = await self.ledger.realized_pnl(self.shard)
        self._order_sequence = await self.ledger.fill_count(self.shard)

    def set_price(self, symbol: str, price: Decimal) -> None:
        """Set the reference price used for fills and mark-to-market.

        Also records the best and worst price seen while a position is open, so
        a closed trade can report how much unrealized profit it gave back.
        """
        self.prices[symbol] = price
        position = self.positions.get(symbol)
        if position is None or position.quantity <= 0 or price <= 0:
            return
        peak = self._peaks.get(symbol)
        trough = self._troughs.get(symbol)
        self._peaks[symbol] = price if peak is None else max(peak, price)
        self._troughs[symbol] = price if trough is None else min(trough, price)

    def mark_to_market(self, prices: dict[str, Decimal]) -> None:
        """Update paper prices from real market data and return the new valuation."""
        self.prices.update(prices)

    def unrealized_pnl(self) -> Decimal:
        return sum(
            (
                (
                    self.prices.get(position.symbol, position.average_entry_price)
                    - position.average_entry_price
                )
                * position.quantity
                for position in self.positions.values()
                if position.quantity
            ),
            Decimal("0"),
        )

    def position_age_seconds(self, symbol: str) -> int | None:
        """Seconds since the most recent fill for a symbol, used for holding context."""
        filled = [fill.filled_at for fill in self.fills if fill.symbol == symbol]
        if not filled:
            return None
        return int((self.clock.now() - max(filled)).total_seconds())

    async def get_account(self) -> PortfolioState:
        return self._state()

    async def get_positions(self) -> PortfolioState:
        return self._state()

    async def get_orders(self) -> list[Order]:
        return list(self.orders.values())

    async def place_order(self, request: OrderRequest) -> Order:
        existing = next(
            (
                order
                for order in self.orders.values()
                if order.request.client_order_id == request.client_order_id
            ),
            None,
        )
        if existing is not None:
            return existing
        price = self._fill_price(request)
        value = price * request.quantity
        margin = value / self.margin_multiple
        fees, _ = self._fees_for(request.side, value)
        if request.side == OrderSide.BUY and margin + fees > self.cash:
            raise ValueError("insufficient paper cash")
        self._order_sequence += 1
        # Include the shard: order ids (and the fill ids derived from them) are
        # written to globally-unique ledger columns, and each shard restores its
        # own sequence independently. Without this, the first trade after a
        # restart reused FILL-PAPER-00000N and the insert aborted after the
        # in-memory fill had already been applied.
        broker_order_id = f"PAPER-{self.shard}-{self._order_sequence:06d}"
        order = Order(
            broker_order_id=broker_order_id,
            request=request,
            status=OrderStatus.FILLED,
            created_at=self.clock.now(),
        )
        # Snapshot the book before mutating: if persisting the fill fails, the
        # in-memory change rolls back too, so a fill can never exist only in
        # memory (a ghost position that vanishes on the next restart).
        snapshot = (
            self.cash,
            dict(self.positions),
            dict(self.orders),
            list(self.fills),
            dict(self.entry_charges),
            self._order_sequence,
        )
        try:
            self.orders[broker_order_id] = order
            fill = self._apply_fill(order, price)
            if self.ledger is not None:
                await self._persist_fill(fill, request)
        except Exception:
            (
                self.cash,
                self.positions,
                self.orders,
                self.fills,
                self.entry_charges,
                self._order_sequence,
            ) = snapshot
            raise
        return order

    async def modify_order(self, broker_order_id: str) -> Order:
        raise ValueError(f"cannot modify immediately filled paper order {broker_order_id}")

    async def cancel_order(self, broker_order_id: str) -> Order:
        raise ValueError(f"cannot cancel immediately filled paper order {broker_order_id}")

    async def get_order_status(self, broker_order_id: str) -> Order:
        return self.orders[broker_order_id]

    def reset(self) -> None:
        self.cash = self.initial_cash
        self.positions.clear()
        self.orders.clear()
        self.fills.clear()
        self.entry_charges.clear()
        self._order_sequence = 0

    async def clear_ledger_book(self) -> None:
        """Forget the persisted open book (paper reset). Fill/close history stays."""
        if self.ledger is not None:
            await self.ledger.clear_book(self.shard)

    def _fill_price(self, request: OrderRequest) -> Decimal:
        market_price = self.prices[request.symbol]
        if request.order_type == OrderType.LIMIT:
            if request.limit_price is None:
                raise ValueError("limit order requires limit price")
            if request.side == OrderSide.BUY and market_price > request.limit_price:
                raise ValueError("paper limit order is not marketable")
            if request.side == OrderSide.SELL and market_price < request.limit_price:
                raise ValueError("paper limit order is not marketable")
        return (
            market_price + self.slippage
            if request.side == OrderSide.BUY
            else market_price - self.slippage
        )

    def _fees_for(
        self, side: OrderSide, turnover: Decimal
    ) -> tuple[Decimal, ChargeBreakdown]:
        """Total cash cost of a fill and its statutory breakdown."""
        breakdown = (
            compute_charges(side, turnover, self.charge_rates)
            if self.charge_rates is not None
            else ChargeBreakdown()
        )
        return breakdown.total + self.fee_per_order, breakdown

    def _release_entry_charges(
        self, symbol: str, quantity: int, held_quantity: int
    ) -> Decimal:
        """Release the entry charges belonging to ``quantity`` shares."""
        total = self.entry_charges.get(symbol, Decimal("0"))
        if total <= 0 or held_quantity <= 0:
            return Decimal("0.00")
        allocated = (total * quantity / held_quantity).quantize(
            PAISE, rounding=ROUND_HALF_UP
        )
        remaining = total - allocated
        if quantity >= held_quantity or remaining <= 0:
            self.entry_charges.pop(symbol, None)
        else:
            self.entry_charges[symbol] = remaining
        return allocated

    def _apply_fill(self, order: Order, price: Decimal) -> Fill:
        request = order.request
        value = price * request.quantity
        fees, breakdown = self._fees_for(request.side, value)
        old = self.positions.get(
            request.symbol,
            Position(symbol=request.symbol, quantity=0, average_entry_price=Decimal("0")),
        )
        realized = old.realized_pnl
        margin = value / self.margin_multiple
        entry_fees = Decimal("0.00")
        if request.side == OrderSide.BUY:
            new_quantity = old.quantity + request.quantity
            average = ((old.average_entry_price * old.quantity) + value) / new_quantity
            self.cash -= margin + fees
            # Both legs must show up in the round trip: carry the entry cost
            # until the shares that paid it are sold.
            self.entry_charges[request.symbol] = (
                self.entry_charges.get(request.symbol, Decimal("0")) + fees
            )
            if old.quantity <= 0:
                self._peaks[request.symbol] = price
                self._troughs[request.symbol] = price
            else:
                self._peaks[request.symbol] = max(
                    self._peaks.get(request.symbol, price), price
                )
                self._troughs[request.symbol] = min(
                    self._troughs.get(request.symbol, price), price
                )
        else:
            if request.quantity > old.quantity:
                raise ValueError("paper broker does not support short selling")
            new_quantity = old.quantity - request.quantity
            average = old.average_entry_price
            # Release the margin actually blocked at entry (average price), not the
            # exit value: a price move changes P&L, never the blocked margin.
            released = old.average_entry_price * request.quantity / self.margin_multiple
            pnl = (price - old.average_entry_price) * request.quantity
            entry_fees = self._release_entry_charges(
                request.symbol, request.quantity, old.quantity
            )
            # Realized P&L is net of both legs; cash only pays the exit leg here
            # because the entry charges already left the account on the buy.
            realized += pnl - fees - entry_fees
            self.realized_pnl += pnl - fees - entry_fees
            self.cash += released + pnl - fees
        self.positions[request.symbol] = Position(
            symbol=request.symbol,
            quantity=new_quantity,
            average_entry_price=average,
            realized_pnl=realized,
        )
        fill = Fill(
            fill_id=f"FILL-{order.broker_order_id}",
            broker_order_id=order.broker_order_id,
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            price=price,
            fees=fees,
            entry_fees=entry_fees,
            charge_breakdown=breakdown.as_dict(),
            filled_at=self.clock.now(),
        )
        self.fills.append(fill)
        return fill

    async def _persist_fill(self, fill: Fill, request: OrderRequest) -> None:
        """Persist one fill, the open book, and any closed trade with its P&L."""
        assert self.ledger is not None
        ledger = self.ledger
        await ledger.record_fill(self.shard, fill, request.decision_id, self.cash)
        if fill.side == OrderSide.BUY:
            # A buy from flat opens the position: stamp entry provenance once the
            # row exists (save_book below creates it).
            await ledger.save_book(
                self.shard,
                self.cash,
                list(self.positions.values()),
                self.clock.now(),
                self.entry_charges,
            )
            await ledger.note_position_opened(
                self.shard, fill.symbol, request.decision_id, fill.filled_at
            )
        else:
            # A sell closes quantity at the pre-exit average: book the round trip.
            position = self.positions.get(fill.symbol)
            entry_price = (
                position.average_entry_price if position is not None else fill.price
            )
            entry_decision_id, opened_at = await ledger.position_open_context(
                self.shard, fill.symbol
            )
            # Net of both legs: the entry charges already left cash on the buy,
            # so they are part of this round trip's realized result.
            pnl = (fill.price - entry_price) * fill.quantity - fill.fees - fill.entry_fees
            # Best/worst unrealized P&L over the holding period, gross of
            # charges: the gap from max_unrealized to realized_pnl is what was
            # given back by exiting late.
            peak = self._peaks.get(fill.symbol, fill.price)
            trough = self._troughs.get(fill.symbol, fill.price)
            max_unrealized = (peak - entry_price) * fill.quantity
            min_unrealized = (trough - entry_price) * fill.quantity
            await ledger.save_book(
                self.shard,
                self.cash,
                list(self.positions.values()),
                self.clock.now(),
                self.entry_charges,
            )
            await ledger.record_close(
                ClosedTrade(
                    closed_trade_id=f"CLOSE-{fill.fill_id}",
                    shard=self.shard,
                    symbol=fill.symbol,
                    quantity=fill.quantity,
                    entry_price=entry_price,
                    exit_price=fill.price,
                    realized_pnl=pnl,
                    opened_at=opened_at,
                    closed_at=fill.filled_at,
                    entry_decision_id=entry_decision_id,
                    exit_decision_id=request.decision_id,
                    charges=fill.fees + fill.entry_fees,
                    max_unrealized=max_unrealized,
                    min_unrealized=min_unrealized,
                )
            )
            position_after = self.positions.get(fill.symbol)
            if position_after is None or position_after.quantity <= 0:
                self._peaks.pop(fill.symbol, None)
                self._troughs.pop(fill.symbol, None)

    def _state(self) -> PortfolioState:
        positions = [position for position in self.positions.values() if position.quantity]
        return PortfolioState(
            cash=self.cash,
            positions=positions,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=sum(
                (
                    (
                        self.prices.get(position.symbol, position.average_entry_price)
                        - position.average_entry_price
                    )
                    * position.quantity
                    for position in positions
                ),
                Decimal("0"),
            ),
        )
