"""R§18.1(13) / R§13.4 — the six mandated query demonstrations, exercised
against a small synthetic registry (fast, no data-layer dependency). The
same code paths are exercised for real by
`experiments/registry_migration/register_qr_smoke_001.py` against the
actual QR-SMOKE-001 records (R§17); see that module and
`docs/reports/qr_infra_002_implementation.md` for the real ids."""
from __future__ import annotations

from _factories import mk_dataset_ref, record_kwargs


def test_six_query_demonstrations(registry):
    window_a = registry.record_experiment(
        **record_kwargs(
            reason_for_run="Window A",
            datasets=(mk_dataset_ref(dataset_id="hyperliquid.ohlcv.1h.BTC"),),
        )
    )
    window_a_rerun = registry.record_experiment(**record_kwargs(reason_for_run="Window A rerun"))
    window_b1 = registry.record_experiment(
        **record_kwargs(
            reason_for_run="Window B1",
            parent_experiment_id=window_a.record.experiment_id,
            change_from_parent="Binance proxy, funding disabled",
            datasets=(mk_dataset_ref(dataset_id="binance.ohlcv.1h.BTC", source_venue="Binance", native_or_proxy="proxy", proxy_for="Hyperliquid"),),
            backtest_config={"funding_mode": "disabled", "funding_notional_basis": "not_modelled"},
        )
    )
    other_strategy = registry.record_experiment(
        **record_kwargs(
            reason_for_run="unrelated strategy",
            strategy=record_kwargs()["strategy"].__class__(
                name="other_strategy", version="1.0", params={}, frequency="1h", target_execution_venue="Hyperliquid"
            ),
        )
    )
    failed = registry.record_experiment(
        **record_kwargs(
            reason_for_run="failed run",
            status="FAILED",
            status_reason="DataIntegrityError: boom",
            results=None,
            run_facts={},
            datasets=(),
            experiment_type="infrastructure",
            no_datasets_reason="failed before touching data",
            backtest_config={},
        )
    )
    rejected = registry.record_experiment(
        **record_kwargs(
            reason_for_run="rejected run",
            status="REJECTED",
            status_reason="bad Sharpe",
            results=None,
            run_facts={},
            datasets=(),
            experiment_type="infrastructure",
            no_datasets_reason="rejected before touching data",
            backtest_config={},
        )
    )

    # 1. all experiments for a strategy
    demo1 = registry.find_experiments(strategy_name="qr_smoke_001")
    ids1 = {fe.record.experiment_id for fe in demo1}
    assert ids1 == {
        window_a.record.experiment_id,
        window_a_rerun.record.experiment_id,
        window_b1.record.experiment_id,
        failed.record.experiment_id,
        rejected.record.experiment_id,
    }
    assert other_strategy.record.experiment_id not in ids1

    # 2. failed/rejected/invalid
    demo2 = registry.failed_or_rejected()
    ids2 = {fe.record.experiment_id for fe in demo2}
    assert ids2 == {failed.record.experiment_id, rejected.record.experiment_id}

    # 3. experiments using a dataset
    demo3 = registry.find_experiments(dataset_id="binance.ohlcv.1h.BTC")
    ids3 = {fe.record.experiment_id for fe in demo3}
    assert ids3 == {window_b1.record.experiment_id}

    # 4. children of Window A
    demo4 = registry.children_of(window_a.record.experiment_id)
    ids4 = {fe.record.experiment_id for fe in demo4}
    assert ids4 == {window_b1.record.experiment_id}

    # 5. funding disabled
    demo5 = registry.find_experiments(funding_disabled=True)
    ids5 = {fe.record.experiment_id for fe in demo5}
    assert ids5 == {window_b1.record.experiment_id}

    # 6. identical configurations / reruns
    demo6a = registry.exact_rerun_groups()
    demo6b = registry.semantic_duplicates()
    assert demo6a[window_a.record.exact_hash] == (window_a.record.experiment_id, window_a_rerun.record.experiment_id)
    assert all(len(v) >= 2 for v in demo6b.values())
