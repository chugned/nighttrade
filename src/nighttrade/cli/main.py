"""``trading-bot`` command-line interface.

Commands:

* ``demo``     — run the canonical AAPL decision scenario from PLAN.md
* ``paper``    — run a paper-trading session on deterministic mock data
* ``backtest`` — run a backtest with realistic execution and report metrics
* ``train``    — train the ML model and walk-forward validate it
* ``simulate`` — full end-to-end pipeline: train -> backtest -> reports
* ``config``   — show (and validate) the active configuration

Everything runs offline against a deterministic mock data source by default.
No command can place a real trade.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .. import __version__
from ..accounting import build_accounting_report, export_tax_csv
from ..approval import TradeProposal, request_approval
from ..backtest import Backtester
from ..config import ConfigError, load_config
from ..cross_section import compute_factors, rank_universe
from ..demo import (
    DEMO_MACRO_SCENARIO,
    DEMO_REFERENCE_PRICE,
    build_demo_candles,
    build_demo_orderbook,
)
from ..exchanges import generate_random_walk
from ..exchanges.credentials import load_sandbox_credentials
from ..exchanges.sandbox import build_sandbox_client
from ..market_hours import describe as describe_market
from ..market_hours import next_market_open, session_at
from ..ml import PredictiveModel, build_dataset
from ..models import Action, ModelKind, Side
from ..observatory import (
    LearningSession,
    LiveMockFeed,
    ObservatoryDB,
    Observer,
    write_daily_report,
)
from ..observatory.database import DEFAULT_DB_PATH
from ..observatory.prediction_tracker import build_prediction_memory
from ..paper import PaperBroker
from ..pipeline import AnalysisPipeline
from ..reporting import (
    backtest_report_dict,
    backtest_report_markdown,
    build_daily_report,
    daily_report_dict,
    daily_report_markdown,
    decision_report_dict,
    decision_report_markdown,
    render_backtest,
    render_daily_report,
    render_decision,
    render_walkforward,
    save_json,
    save_text,
)
from ..research import ResearchLab
from ..risk import RiskEngine
from ..runtime import apply_runtime, get_logger
from ..safety.guard import forbid_real_trading
from ..validation import walk_forward_validate
from ..watchlist import (
    WatchlistScreener,
    build_mock_universe,
    demo_universe_symbols,
    load_watchlist_config,
)

app = typer.Typer(
    add_completion=False,
    help="nighttrade — educational trading research & paper-trading platform. "
    "Cannot place real trades.",
)
_console = Console()
_log = get_logger("cli")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_REPORTS = _REPO_ROOT / "reports"
_MODELS = _REPO_ROOT / "artifacts"


def _setup(profile: Optional[str]):
    """Load config and apply runtime (logging + deterministic seeding)."""
    try:
        config = load_config(profile)
    except ConfigError as exc:
        _console.print(f"[bold red]Config error:[/bold red] {exc}")
        raise typer.Exit(code=1)
    apply_runtime(
        config.runtime.log_level, config.runtime.deterministic, config.runtime.random_seed
    )
    return config


def _mock_candles(config, n_bars: int, drift: float, volatility: float):
    """Deterministic mock candle series for paper / backtest / training."""
    return generate_random_walk(
        symbol=config.symbol,
        n_bars=n_bars,
        start_price=230.0,
        drift=drift,
        volatility=volatility,
        seed=config.runtime.random_seed,
    )


@app.command()
def version() -> None:
    """Print the nighttrade version."""
    _console.print(f"nighttrade {__version__}")


@app.command()
def config(profile: Optional[str] = typer.Option(None, help="Config profile.")) -> None:
    """Show and validate the active configuration."""
    cfg = _setup(profile)
    _console.print(f"[bold green]Config OK[/bold green] — profile '{cfg.profile}'")
    table = Table(title="Active configuration", header_style="bold")
    table.add_column("Key")
    table.add_column("Value")
    rows = [
        ("symbol", cfg.symbol),
        ("safety.paper_trading", str(cfg.safety.paper_trading)),
        ("safety.live_trading_enabled", str(cfg.safety.live_trading_enabled)),
        ("runtime.allow_network", str(cfg.runtime.allow_network)),
        ("runtime.deterministic", str(cfg.runtime.deterministic)),
        ("macro.source", cfg.macro.source),
        ("ml.model_kind", cfg.ml.model_kind),
        ("fusion.action_threshold", str(cfg.fusion.action_threshold)),
        ("risk.fee_bps", str(cfg.risk.fee_bps)),
        ("risk.max_daily_loss_pct", str(cfg.risk.max_daily_loss_pct)),
        ("paper.starting_cash", str(cfg.paper.starting_cash)),
    ]
    for k, v in rows:
        table.add_row(k, v)
    _console.print(table)


@app.command()
def demo(
    profile: Optional[str] = typer.Option(None, help="Config profile."),
    save: bool = typer.Option(False, help="Write JSON + Markdown reports."),
) -> None:
    """Run the canonical AAPL decision demo from PLAN.md."""
    cfg = _setup(profile)
    _console.rule("[bold]nighttrade — canonical decision demo")
    candles = build_demo_candles()
    orderbook = build_demo_orderbook()
    pipeline = AnalysisPipeline(cfg)
    result = pipeline.analyze(
        candles,
        orderbook,
        reference_price=DEMO_REFERENCE_PRICE,
        macro_scenario=DEMO_MACRO_SCENARIO,
    )
    render_decision(result, _console)
    if save:
        jp = save_json(decision_report_dict(result), _REPORTS / "demo.json")
        mp = save_text(decision_report_markdown(result), _REPORTS / "demo.md")
        _console.print(f"[green]Saved[/green] {jp} and {mp}")


@app.command()
def paper(
    profile: Optional[str] = typer.Option(None, help="Config profile."),
    bars: int = typer.Option(400, help="Number of mock bars to trade."),
) -> None:
    """Run a paper-trading session on deterministic mock data."""
    cfg = _setup(profile)
    _console.rule("[bold]nighttrade — paper-trading session")
    candles = _mock_candles(cfg, bars, drift=0.0004, volatility=0.005)
    result = Backtester(cfg).run(candles)
    m = result.metrics
    _console.print(
        f"Paper session over {m.bars} bars — " f"[bold]{m.total_trades}[/bold] simulated trades."
    )
    render_backtest(result, _console)
    _console.print(
        f"Final paper equity: [bold]{m.ending_equity:,.2f}[/bold] "
        f"{cfg.paper.base_currency} (started {m.starting_equity:,.2f})"
    )


@app.command()
def backtest(
    profile: Optional[str] = typer.Option(None, help="Config profile."),
    bars: int = typer.Option(600, help="Number of mock bars to backtest."),
    save: bool = typer.Option(False, help="Write JSON + Markdown reports."),
) -> None:
    """Run a backtest with realistic execution and report metrics."""
    cfg = _setup(profile)
    _console.rule("[bold]nighttrade — backtest")
    candles = _mock_candles(cfg, bars, drift=0.0003, volatility=0.006)
    result = Backtester(cfg).run(candles)
    render_backtest(result, _console)
    if save:
        jp = save_json(backtest_report_dict(result), _REPORTS / "backtest.json")
        mp = save_text(backtest_report_markdown(result), _REPORTS / "backtest.md")
        _console.print(f"[green]Saved[/green] {jp} and {mp}")


@app.command()
def train(
    profile: Optional[str] = typer.Option(None, help="Config profile."),
    bars: int = typer.Option(1200, help="Number of mock bars for training."),
) -> None:
    """Train the ML model and run walk-forward validation."""
    cfg = _setup(profile)
    _console.rule("[bold]nighttrade — ML training & walk-forward validation")
    candles = _mock_candles(cfg, bars, drift=0.0002, volatility=0.006)

    dataset = build_dataset(candles, cfg)
    _console.print(f"Dataset: {len(dataset)} samples, " f"class balance {dataset.class_balance}")
    model = PredictiveModel(ModelKind(cfg.ml.model_kind), cfg.runtime.random_seed)
    train_result = model.fit(dataset)
    _console.print(
        f"In-sample: accuracy {train_result.accuracy:.3f}, "
        f"AUC {train_result.auc:.3f} "
        f"[dim](in-sample numbers are diagnostic only)[/dim]"
    )

    report = walk_forward_validate(candles, cfg)
    render_walkforward(report, _console)

    path = model.save(_MODELS / "model.pkl")
    _console.print(f"[green]Model saved[/green] -> {path}")


@app.command()
def simulate(
    profile: Optional[str] = typer.Option(None, help="Config profile."),
    bars: int = typer.Option(1200, help="Number of mock bars."),
) -> None:
    """Full end-to-end run: train -> walk-forward -> backtest -> reports."""
    cfg = _setup(profile)
    _console.rule("[bold]nighttrade — full simulation")
    candles = _mock_candles(cfg, bars, drift=0.0003, volatility=0.006)

    # 1. Train an ML model on the earlier portion of the data.
    split = int(len(candles) * 0.6)
    dataset = build_dataset(candles[:split], cfg)
    model = PredictiveModel(ModelKind(cfg.ml.model_kind), cfg.runtime.random_seed)
    model.fit(dataset)
    _console.print(f"[1/3] Trained {model.version}")

    # 2. Walk-forward validate.
    report = walk_forward_validate(candles[:split], cfg)
    _console.print(f"[2/3] Walk-forward mean test accuracy " f"{report.mean_test_accuracy:.3f}")
    render_walkforward(report, _console)

    # 3. Backtest the remainder with the trained model (out-of-sample).
    result = Backtester(cfg, model).run(candles[split:])
    _console.print("[3/3] Out-of-sample backtest:")
    render_backtest(result, _console)

    save_json(backtest_report_dict(result), _REPORTS / "simulate.json")
    save_text(backtest_report_markdown(result), _REPORTS / "simulate.md")
    _console.print(f"[green]Reports saved[/green] -> {_REPORTS}")


@app.command()
def watchlist(
    profile: Optional[str] = typer.Option(None, help="Config profile."),
    universe: str = typer.Option(
        "config", help="'config' (watchlist.symbols) or 'demo' (mixed set)."
    ),
) -> None:
    """Screen the multi-asset watchlist for liquidity / quality."""
    cfg = _setup(profile)
    _console.rule("[bold]nighttrade — watchlist screening")
    symbols = demo_universe_symbols() if universe == "demo" else cfg.watchlist.symbols
    data = build_mock_universe(symbols, seed=cfg.runtime.random_seed)
    screener = WatchlistScreener(cfg.watchlist)

    table = Table(title="Asset screening", header_style="bold")
    for col in ("Symbol", "Status", "24h volume", "Spread", "Book notional", "1h move"):
        table.add_column(col)
    rejects = []
    for r in screener.screen(data):
        m = r.metrics
        style = "green" if r.approved else "red"
        table.add_row(
            r.symbol,
            f"[{style}]{r.status}[/{style}]",
            f"${m.volume_24h_usd:,.0f}",
            f"{m.spread_bps:.1f} bps",
            f"${m.book_notional_usd:,.0f}",
            f"{m.move_1h_pct * 100:+.1f}%",
        )
        if not r.approved:
            rejects.append(r)
    _console.print(table)
    for r in rejects:
        _console.print(f"[red]{r.symbol} rejected:[/red] " + "; ".join(r.rejections))
    approved = screener.approved_symbols(data)
    _console.print(f"\nTradeable universe: [bold]{approved}[/bold]")


@app.command()
def approve(
    profile: Optional[str] = typer.Option(None, help="Config profile."),
) -> None:
    """Run the canonical decision and require manual approval to paper-execute."""
    cfg = _setup(profile)
    _console.rule("[bold]nighttrade — manual approval")

    candles = build_demo_candles()
    orderbook = build_demo_orderbook()
    result = AnalysisPipeline(cfg).analyze(
        candles, orderbook, reference_price=DEMO_REFERENCE_PRICE, macro_scenario=DEMO_MACRO_SCENARIO
    )
    decision = result.decision

    broker = PaperBroker(cfg.paper.starting_cash, cfg.paper.base_currency)
    risk = RiskEngine(cfg.risk, cfg.paper.starting_cash)

    if decision.action is Action.HOLD:
        _console.print("Decision is HOLD — nothing to approve.")
        return

    equity = cfg.paper.starting_cash
    sizing = risk.size(equity, decision.entry, decision.stop)
    liquidity = orderbook.depth("ask")
    preview = risk.execute(
        "preview",
        decision.symbol,
        Side.BUY,
        sizing.quantity,
        decision.entry,
        liquidity,
        candles[-1].timestamp,
    )

    micro = result.microstructure
    liq_warn = None
    if micro.thin_liquidity:
        liq_warn = "thin orderbook liquidity"
    elif micro.spread_bps and micro.spread_bps > cfg.microstructure.wide_spread_bps:
        liq_warn = f"wide spread ({micro.spread_bps:.1f} bps)"

    proposal = TradeProposal(
        symbol=decision.symbol,
        action=decision.action,
        entry=decision.entry,
        stop=decision.stop,
        target=decision.target,
        confidence=decision.confidence,
        quantity=sizing.quantity,
        risk_amount=sizing.risk_amount,
        expected_slippage_cost=preview.slippage * preview.quantity,
        expected_fee=preview.fee,
        reasoning=decision.reasoning,
        liquidity_warning=liq_warn,
        kill_switch_active=result.kill_switch.active,
        kill_switch_reasons=result.kill_switch.reasons,
        execution_mode="simulated",
    )

    outcome = request_approval(proposal, cfg.approval, _console)
    if not outcome.approved:
        _console.print(f"[yellow]Trade NOT executed:[/yellow] {outcome.reason}")
        return

    fill = broker.submit_market_order(
        "approved",
        decision.symbol,
        Side.BUY,
        sizing.quantity,
        decision.entry,
        liquidity,
        cfg.risk,
        candles[-1].timestamp,
    )
    _console.print(
        f"[green]PAPER-EXECUTED[/green] (simulated): bought "
        f"{fill.quantity:.6f} {decision.symbol} @ {fill.price:,.2f} "
        f"(slippage {fill.slippage:,.2f}, fee {fill.fee:,.2f})"
    )
    _console.print(f"Cash remaining: {broker.cash:,.2f} {cfg.paper.base_currency}")


@app.command()
def accounting(
    profile: Optional[str] = typer.Option(None, help="Config profile."),
    bars: int = typer.Option(600, help="Mock bars for the paper session."),
    save: bool = typer.Option(False, help="Export a tax-reporting CSV."),
) -> None:
    """Accounting report for a paper session (+ optional tax CSV export)."""
    cfg = _setup(profile)
    _console.rule("[bold]nighttrade — accounting report")
    candles = _mock_candles(cfg, bars, drift=0.0004, volatility=0.005)
    result = Backtester(cfg).run(candles)
    report = build_accounting_report(
        result.trades, cfg.paper.starting_cash, result.metrics.ending_equity
    )

    table = Table(title="Simulated accounting", header_style="bold")
    table.add_column("Item")
    table.add_column("Amount", justify="right")
    table.add_row("Simulated profit", f"{report.simulated_profit:+,.2f}")
    table.add_row("Simulated loss", f"{report.simulated_loss:+,.2f}")
    table.add_row("Estimated fees", f"{report.estimated_fees:,.2f}")
    table.add_row("Net PnL", f"{report.net_pnl:+,.2f}")
    table.add_row("Return", f"{report.return_pct:+.2f}%")
    _console.print(table)

    if report.per_asset:
        per = Table(title="Per-asset PnL", header_style="bold")
        for col in ("Asset", "Trades", "W/L", "Net PnL", "Fees"):
            per.add_column(col)
        for sym, a in report.per_asset.items():
            per.add_row(
                sym, str(a.trades), f"{a.wins}/{a.losses}", f"{a.net_pnl:+,.2f}", f"{a.fees:,.2f}"
            )
        _console.print(per)

    _console.print("[dim]Simulated paper data — not tax advice, not a filing.[/dim]")
    if save:
        path = export_tax_csv(result.trades, _REPORTS / "tax_report.csv")
        _console.print(f"[green]Tax CSV exported[/green] -> {path}")


@app.command("daily-report")
def daily_report(
    profile: Optional[str] = typer.Option(None, help="Config profile."),
    bars: int = typer.Option(600, help="Mock bars for the paper session."),
    save: bool = typer.Option(False, help="Write JSON + Markdown reports."),
) -> None:
    """Generate the daily operations report for a paper session."""
    cfg = _setup(profile)
    candles = _mock_candles(cfg, bars, drift=0.0003, volatility=0.006)
    result = Backtester(cfg).run(candles)
    report = build_daily_report(result, label=candles[-1].timestamp.date().isoformat())
    render_daily_report(report, _console)
    if save:
        jp = save_json(daily_report_dict(report), _REPORTS / "daily.json")
        mp = save_text(daily_report_markdown(report), _REPORTS / "daily.md")
        _console.print(f"[green]Saved[/green] {jp} and {mp}")


@app.command("sandbox-check")
def sandbox_check(
    profile: Optional[str] = typer.Option(None, help="Config profile."),
) -> None:
    """Verify the sandbox setup and prove real execution is disabled."""
    cfg = _setup(profile)
    _console.rule("[bold]nighttrade — sandbox / safety check")

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("k", style="bold")
    table.add_column("v")
    table.add_row("sandbox.enabled", str(cfg.sandbox.enabled))
    table.add_row("sandbox.broker", f"{cfg.sandbox.broker} (PAPER account)")
    table.add_row("require_read_only_keys", str(cfg.sandbox.require_read_only_keys))
    table.add_row("reject_live_keys", str(cfg.sandbox.reject_live_keys))
    table.add_row("runtime.allow_network", str(cfg.runtime.allow_network))
    _console.print(table)

    creds = load_sandbox_credentials(cfg.sandbox.broker)
    _console.print(f"Paper-account credentials configured: {creds is not None}")

    try:
        client = build_sandbox_client(cfg)
    except Exception as exc:  # noqa: BLE001 - report any setup failure
        client = None
        _console.print(f"[yellow]Sandbox client not built:[/yellow] {exc}")
    mode = "broker paper account connected" if client else "local paper simulation (no broker)"
    _console.print(f"Execution: [bold]{mode}[/bold]")

    # Prove the real-execution path is structurally disabled.
    proof = []
    try:
        forbid_real_trading("sandbox-check")
    except NotImplementedError as exc:
        proof.append(f"forbid_real_trading() raises: {exc}")
    try:
        PaperBroker(cfg.paper.starting_cash).connect_live()
    except NotImplementedError as exc:
        proof.append(f"PaperBroker.connect_live() raises: {exc}")
    proof.append("sandbox execution is restricted to a paper-URL allowlist")
    proof.append("API keys tied to a live brokerage account are rejected on connect")
    _console.print(
        Panel(
            "\n".join(f"✓ {p}" for p in proof),
            title="Real execution is structurally disabled",
            border_style="green",
        )
    )


def _load_observer_model():
    """Load the trained ML model if one has been saved, else None."""
    model_path = _MODELS / "model.pkl"
    if not model_path.exists():
        return None
    try:
        model = PredictiveModel.load(model_path)
        _console.print(f"Loaded ML model: {model.version}")
        return model
    except Exception as exc:  # noqa: BLE001
        _console.print(f"[yellow]ML model not loaded:[/yellow] {exc}")
        return None


@app.command()
def learn(
    profile: Optional[str] = typer.Option(None, help="Config profile."),
    days: int = typer.Option(30, help="Length of the learning window, in days."),
    interval: int = typer.Option(300, help="Seconds between observation cycles."),
) -> None:
    """Run the multi-day Paper Trading Learning Observatory (Ctrl+C to stop).

    Observes the watchlist for ``--days`` days, tracking learning phases,
    progress, prediction reliability and a Paper Strategy Readiness score.
    Resumes the same window on restart. Paper / simulation only.
    """
    cfg = _setup(profile)
    _console.rule(f"[bold]nighttrade — {days}-Day Paper Trading Learning Observatory")
    _console.print(
        "Paper / simulation only. No real trading, wallets, or "
        "money movement. Ctrl+C to pause; the window resumes on "
        "restart.\n"
    )
    db = ObservatoryDB()
    session = LearningSession.resume_or_create(db, target_days=days, interval_seconds=interval)
    observer = Observer(
        cfg,
        load_watchlist_config(),
        db=db,
        feed=LiveMockFeed(),
        model=_load_observer_model(),
        learning_session=session,
    )
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    _console.print(
        f"Learning session: day {session.day_number(now)}/{days}, "
        f"phase '{session.phase(now)}', {session.cycles_completed} cycles done."
    )
    _console.print(
        f"Observing {len(observer.watchlist_config.symbols)} symbols "
        f"every {interval}s. Open the dashboard to watch progress.\n"
    )
    observer.run_forever(interval)
    _console.print(
        "\n[green]Learning observer stopped.[/green] "
        "Re-run 'trading-bot learn' to resume the window."
    )


@app.command()
def observe(
    profile: Optional[str] = typer.Option(None, help="Config profile."),
    interval: int = typer.Option(300, help="Seconds between observation cycles."),
    live: bool = typer.Option(
        False,
        "--live",
        help="Use REAL stock data (yfinance) instead of the " "deterministic mock feed.",
    ),
    max_symbols: Optional[int] = typer.Option(
        None, help="Cap the live universe to the first N symbols."
    ),
) -> None:
    """Run the 24/7 Market Safety Observer (Ctrl+C to stop).

    Each cycle fetches data, runs every analysis, paper-simulates, stores
    predictions, evaluates older predictions against reality, and scores
    market safety. Observation / paper simulation only — no real orders.

    With ``--live`` the observer monitors the S&P 500 — the ~500 most liquid
    US stocks — on live Yahoo Finance intraday data; otherwise it uses the
    deterministic mock feed.
    """
    cfg = _setup(profile)
    _console.rule("[bold]nighttrade — Market Safety Observer")
    _console.print(
        "Paper / simulation only. No real orders, wallets, or "
        "money movement. Press Ctrl+C to stop.\n"
    )

    model = None
    model_path = _MODELS / "model.pkl"
    if model_path.exists():
        try:
            model = PredictiveModel.load(model_path)
            _console.print(f"Loaded ML model: {model.version}")
        except Exception as exc:  # noqa: BLE001
            _console.print(f"[yellow]ML model not loaded:[/yellow] {exc}")

    if live:
        from ..config.schema import WatchlistConfig
        from ..observatory import YFinanceFeed
        from ..watchlist import liquid_universe

        universe = liquid_universe(limit=max_symbols)
        _console.print(
            f"Live mode: fetching real intraday data for "
            f"{len(universe)} S&P 500 stocks "
            f"[dim](first download takes a minute)…[/dim]"
        )
        feed = YFinanceFeed(universe)
        feed.refresh_now()
        available = feed.available_symbols()
        if not available:
            _console.print(
                "[bold red]No live data available[/bold red] — "
                "market data could not be fetched. Aborting."
            )
            raise typer.Exit(code=1)
        watchlist_cfg = WatchlistConfig(symbols=available)
        _console.print(
            f"[green]Live feed ready[/green] — "
            f"{len(available)}/{len(universe)} symbols have data."
        )
    else:
        feed = LiveMockFeed()
        watchlist_cfg = load_watchlist_config()

    observer = Observer(cfg, watchlist_cfg, db=ObservatoryDB(), feed=feed, model=model)
    _console.print(
        f"Observing {len(observer.watchlist_config.symbols)} symbols "
        f"every {interval}s. Database: {DEFAULT_DB_PATH}"
    )
    observer.run_forever(interval)
    _console.print("\n[green]Observer stopped cleanly.[/green]")


@app.command()
def dashboard(
    host: str = typer.Option("127.0.0.1", help="Bind host."),
    port: int = typer.Option(8000, help="Bind port."),
) -> None:
    """Launch the visual Market Safety dashboard (FastAPI + web UI)."""
    import uvicorn

    _console.rule("[bold]nighttrade — Market Safety Dashboard")
    _console.print(f"Dashboard: [bold]http://{host}:{port}[/bold]  " f"(reads {DEFAULT_DB_PATH})")
    _console.print("Read-only observatory view. Ctrl+C to stop.\n")
    uvicorn.run("nighttrade.dashboard.app:app", host=host, port=port, log_level="warning")


@app.command("report-daily")
def report_daily(
    day: Optional[str] = typer.Option(None, help="Day YYYY-MM-DD (default: today)."),
) -> None:
    """Generate the daily observatory markdown report."""
    _setup(None)
    db = ObservatoryDB()
    path = write_daily_report(db, day)
    db.close()
    _console.print(f"[green]Daily report written[/green] -> {path}")
    _console.print(path.read_text(encoding="utf-8"))


@app.command()
def status() -> None:
    """Show the current observatory status (bot, safety score, counts)."""
    _setup(None)
    db = ObservatoryDB()
    run = db.current_bot_run()
    safety = db.latest_safety_score()
    memory = build_prediction_memory(db.outcomes(limit=5000))

    table = Table(title="Observatory status", header_style="bold", show_header=False)
    table.add_column("k", style="bold")
    table.add_column("v")
    if run:
        live = run["status"] == "running"
        table.add_row(
            "Bot",
            ("RUNNING" if live else run["status"].upper())
            + f" (run #{run['id']}, {run['cycles']} cycles)",
        )
        table.add_row("Last heartbeat", str(run.get("last_heartbeat_ts")))
    else:
        table.add_row("Bot", "never started — run 'trading-bot observe'")
    if safety:
        table.add_row("Market safety score", f"{safety['score']}/100")
        table.add_row("Status / condition", f"{safety['status']} / {safety['condition']}")
    table.add_row("Snapshots stored", str(db.count("market_snapshots")))
    table.add_row("Predictions stored", str(db.count("predictions")))
    table.add_row("Predictions evaluated", str(memory.total))
    table.add_row(
        "Prediction accuracy",
        f"{memory.overall_accuracy * 100:.0f}% "
        f"({'reliable' if memory.is_reliable else 'UNRELIABLE'})",
    )
    table.add_row("Paper trades closed", str(db.count("paper_trades")))
    _console.print(table)
    db.close()


@app.command("watchlist-check")
def watchlist_check() -> None:
    """Screen the configs/watchlist.yaml symbols for liquidity / quality."""
    from datetime import datetime, timezone

    _setup(None)
    _console.rule("[bold]nighttrade — watchlist check")
    wl = load_watchlist_config()
    feed = LiveMockFeed()
    now = datetime.now(timezone.utc)
    screener = WatchlistScreener(wl)

    table = Table(title="Watchlist screening", header_style="bold")
    for col in ("Symbol", "Status", "Price", "24h volume", "Spread", "Book notional"):
        table.add_column(col)
    approved = 0
    for symbol in wl.symbols:
        tick = feed.tick_at(symbol, now)
        book = feed.orderbook_at(symbol, now)
        candles = feed.candles_at(symbol, now, n_bars=120)
        r = screener.screen_one(symbol, tick, book, candles)
        approved += int(r.approved)
        style = "green" if r.approved else "red"
        m = r.metrics
        table.add_row(
            symbol,
            f"[{style}]{r.status}[/{style}]",
            f"{m.price:,.4f}",
            f"${m.volume_24h_usd:,.0f}",
            f"{m.spread_bps:.1f} bps",
            f"${m.book_notional_usd:,.0f}",
        )
        if not r.approved:
            _console.print(f"[red]{symbol}:[/red] " + "; ".join(r.rejections))
    _console.print(table)
    _console.print(f"{approved}/{len(wl.symbols)} symbols cleared the filters.")


@app.command()
def rank(
    profile: Optional[str] = typer.Option(None, help="Config profile."),
    live: bool = typer.Option(
        False, "--live", help="Rank the live S&P 500 (the ~500 most liquid US stocks)."
    ),
    top: int = typer.Option(20, help="How many stocks to show per basket."),
    max_symbols: Optional[int] = typer.Option(
        None, help="Cap the live universe to the first N symbols."
    ),
) -> None:
    """Rank the universe cross-sectionally — relative-strength stock selection.

    Every stock's factors (momentum, trend, mean-reversion, low-vol, ML) are
    z-scored *across the whole universe* and blended; the universe is then
    ranked and split into a long basket (top) and a short/avoid basket.
    """
    from datetime import datetime, timezone

    cfg = _setup(profile)
    _console.rule("[bold]nighttrade — cross-sectional ranking")
    now = datetime.now(timezone.utc)

    if live:
        from ..observatory import YFinanceFeed
        from ..watchlist import liquid_universe

        universe = liquid_universe(limit=max_symbols)
        _console.print(
            f"Fetching live data for {len(universe)} S&P 500 stocks "
            f"[dim](first download takes a minute)…[/dim]"
        )
        feed = YFinanceFeed(universe)
        feed.refresh_now()
        symbols = feed.available_symbols()
    else:
        feed = LiveMockFeed()
        symbols = load_watchlist_config().symbols

    model = _load_observer_model()
    factors = []
    for sym in symbols:
        try:
            candles = feed.candles_at(sym, now, n_bars=300)
        except Exception:  # noqa: BLE001
            continue
        ml_score = None
        if model is not None and candles:
            try:
                ml_score = model.predict_signal(candles, cfg).score
            except Exception:  # noqa: BLE001
                ml_score = None
        snap = compute_factors(sym, candles, cfg.cross_section, ml_score)
        if snap is not None:
            factors.append(snap)

    if len(factors) < 2:
        _console.print("[red]Not enough stocks with data to rank.[/red]")
        raise typer.Exit(code=1)

    ranked = rank_universe(factors, cfg.cross_section, now)
    has_ml = "ml" in ranked.weights
    weight_str = "  ".join(f"{k} {v:.2f}" for k, v in ranked.weights.items())
    _console.print(
        f"Ranked [bold]{len(ranked.stocks)}[/bold] stocks "
        f"({len(ranked.excluded)} excluded by liquidity gate)."
    )
    _console.print(f"[dim]Factor weights: {weight_str}[/dim]\n")

    def _table(title: str, rows, style: str) -> None:
        tbl = Table(title=title, header_style="bold")
        cols = ["#", "Symbol", "Price", "Score z", "Mom", "Trend", "Rev", "LowVol"]
        if has_ml:
            cols.append("ML")
        cols.append("Basket")
        for col in cols:
            tbl.add_column(col)
        for s in rows:
            fz = s.factor_z
            cells = [
                str(s.rank),
                s.symbol,
                f"{s.price:,.2f}",
                f"{s.composite_z:+.2f}",
                f"{fz['momentum']:+.2f}",
                f"{fz['trend']:+.2f}",
                f"{fz['reversion']:+.2f}",
                f"{fz['low_vol']:+.2f}",
            ]
            if has_ml:
                cells.append(f"{fz['ml']:+.2f}")
            cells.append(f"[{style}]{s.basket}[/{style}]")
            tbl.add_row(*cells)
        _console.print(tbl)

    _table(f"Top {top} — strongest relative to the universe", ranked.top(top), "green")
    _console.print()
    _table(f"Bottom {top} — weakest relative to the universe", ranked.bottom(top), "red")
    _console.print(
        f"\nLong basket: [bold green]{len(ranked.long_basket)}[/bold green] "
        f"stocks  |  Short/avoid basket: "
        f"[bold red]{len(ranked.short_basket)}[/bold red] stocks"
    )
    _console.print(
        "[dim]Cross-sectional ranking is relative — it says which "
        "stocks look strong vs the universe, not that the market "
        "will rise. Paper / research only.[/dim]"
    )


@app.command()
def research(
    profile: Optional[str] = typer.Option(None, help="Config profile."),
    years: int = typer.Option(3, help="Years of real daily history to test on."),
    symbols: Optional[str] = typer.Option(
        None, help="Comma-separated tickers (default: the watchlist)."
    ),
    limit: Optional[int] = typer.Option(None, help="Cap the number of symbols tested."),
    sweep: bool = typer.Option(
        False,
        "--sweep",
        help="Sweep ATR stop/target multipliers (Phase 1) " "instead of the baseline report.",
    ),
    meta: bool = typer.Option(
        False,
        "--meta",
        help="Evaluate the meta-labelling model (Phase 4) "
        "out-of-sample instead of the baseline report.",
    ),
) -> None:
    """Run the research lab — backtest + purged walk-forward on REAL history.

    The measurement harness from the strategy plan: it downloads years of real
    daily bars (cached), backtests and walk-forward-validates the strategy, and
    delivers an honest baseline verdict. 'No edge' is the expected default —
    every later change must be proven here, out-of-sample, before going live.

    With ``--sweep`` it instead sweeps the ATR stop/target multipliers,
    optimizes in-sample and validates the winner out-of-sample.
    """
    cfg = _setup(profile)
    _console.rule("[bold]nighttrade — research lab")
    syms = (
        [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if symbols
        else load_watchlist_config().symbols
    )
    if limit:
        syms = syms[:limit]
    lab = ResearchLab(cfg)

    if sweep:
        _console.print(
            f"Sweeping ATR stop/target multipliers across "
            f"[bold]{len(syms)}[/bold] symbols, {years}y history…"
        )
        rep = lab.sweep_stops(syms, years=years)
        if not rep.grid or rep.best is None:
            _console.print("[red]" + (rep.notes[0] if rep.notes else "no data") + "[/red]")
            return
        grid_table = Table(title="ATR stop/target sweep — in-sample", header_style="bold")
        for col in ("Stop xATR", "Reward:Risk", "Return", "Win rate", "Trades"):
            grid_table.add_column(col)
        for p in sorted(rep.grid, key=lambda x: -x.return_pct):
            best = " ← best" if p is rep.best else ""
            grid_table.add_row(
                f"{p.stop_mult:.1f}",
                f"{p.reward_risk:.1f}:1",
                f"{p.return_pct:+.1f}%",
                f"{p.win_rate * 100:.0f}%",
                f"{p.trades}{best}",
            )
        _console.print(grid_table)
        b = rep.best
        _console.print(
            Panel(
                f"[bold]Best in-sample:[/bold] stop {b.stop_mult:.1f}xATR, "
                f"reward:risk {b.reward_risk:.1f}:1  →  {b.return_pct:+.1f}%\n"
                f"[bold]Out-of-sample:[/bold]  {rep.oos_return:+.1f}%  "
                f"(current config: {rep.baseline_oos_return:+.1f}%)\n"
                + "\n".join(f"• {n}" for n in rep.notes),
                title="Sweep verdict",
                border_style="cyan",
            )
        )
        return

    if meta:
        _console.print(
            f"Triple-barrier labelling + meta-model evaluation "
            f"across [bold]{len(syms)}[/bold] symbols, {years}y…"
        )
        rep = lab.evaluate_meta(syms, years=years)
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("k", style="bold")
        table.add_column("v")
        table.add_row("Train samples", f"{rep.train_samples:,}")
        table.add_row("Test samples", f"{rep.test_samples:,}")
        table.add_row("Base win rate (test)", f"{rep.base_rate * 100:.1f}%")
        table.add_row("Meta-model accuracy", f"{rep.accuracy * 100:.1f}%")
        table.add_row("Accepted-trade precision", f"{rep.precision * 100:.1f}%")
        table.add_row("Coverage (trades accepted)", f"{rep.coverage * 100:.1f}%")
        table.add_row("Precision lift vs base", f"{rep.lift * 100:+.1f} pts")
        _console.print(table)
        style = "cyan" if rep.lift > 0.03 else "yellow"
        _console.print(
            Panel(
                "\n".join(f"• {n}" for n in rep.notes),
                title="Meta-model verdict",
                border_style=style,
            )
        )
        return

    _console.print(
        f"Backtesting [bold]{len(syms)}[/bold] symbols on {years}y "
        f"of real daily history "
        f"[dim](first run downloads + caches)…[/dim]"
    )
    report = lab.run(syms, years=years)

    table = Table(title="Per-symbol baseline", header_style="bold")
    for col in (
        "Symbol",
        "Bars",
        "Backtest return",
        "Win rate",
        "Trades",
        "WF accuracy",
        "Overfit gap",
    ):
        table.add_column(col)
    for r in report.symbols:
        bt = r.backtest
        has_wf = r.walkforward.n_folds > 0
        table.add_row(
            r.symbol,
            str(r.bars),
            f"{bt.total_return_pct:+.1f}%",
            f"{bt.win_rate * 100:.0f}%",
            str(bt.total_trades),
            f"{r.wf_accuracy * 100:.1f}%" if has_wf else "—",
            f"{r.walkforward.mean_overfit_gap * 100:+.0f}%" if has_wf else "—",
        )
    _console.print(table)

    agg = Table(show_header=False, box=None, padding=(0, 2))
    agg.add_column("k", style="bold")
    agg.add_column("v")
    agg.add_row("Mean backtest return", f"{report.mean_return_pct:+.2f}%")
    agg.add_row("Mean win rate", f"{report.mean_win_rate * 100:.1f}%")
    agg.add_row("Mean Sharpe-like", f"{report.mean_sharpe:.2f}")
    agg.add_row("Mean walk-forward accuracy", f"{report.mean_wf_accuracy * 100:.1f}%")
    agg.add_row("Mean overfit gap", f"{report.mean_overfit_gap * 100:+.1f}%")
    agg.add_row("Total backtested trades", str(report.total_trades))
    agg.add_row("Leakage-flagged symbols", str(report.leakage_flags))
    _console.print(agg)

    style = {
        "SUSPECTED LEAKAGE": "red",
        "OVERFIT": "red",
        "NO DATA": "red",
        "MARGINAL EDGE — UNPROVEN": "cyan",
    }.get(report.verdict, "yellow")
    _console.print(
        Panel(
            "\n".join([f"[bold]{report.verdict}[/bold]"] + [f"• {n}" for n in report.notes]),
            title="Baseline verdict",
            border_style=style,
        )
    )
    if report.skipped:
        _console.print(f"[dim]Skipped (too little history): " f"{', '.join(report.skipped)}[/dim]")


@app.command("market-hours")
def market_hours() -> None:
    """Show the current US equity market session (regular / pre / post / closed)."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    session = session_at(now)
    _console.rule("[bold]nighttrade — US market hours")
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("k", style="bold")
    table.add_column("v")
    table.add_row("Now (UTC)", now.strftime("%Y-%m-%d %H:%M:%S"))
    table.add_row("Session", session.value.upper().replace("_", "-"))
    table.add_row("Tradeable", "yes (paper)" if session.is_tradeable else "no")
    table.add_row("State", describe_market(now))
    if not session.is_tradeable:
        nxt = next_market_open(now)
        table.add_row("Next regular open", nxt.strftime("%Y-%m-%d %H:%M UTC"))
    _console.print(table)
    _console.print("[dim]Paper trades are placed only during the REGULAR " "session.[/dim]")


if __name__ == "__main__":  # pragma: no cover
    app()
