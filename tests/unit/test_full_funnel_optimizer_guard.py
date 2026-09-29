"""The post-hoc split relies on two Meridian private APIs; pin them on 2.1.x."""

import inspect

import meridian
from meridian.analysis import optimizer


def test_private_optimizer_apis_are_unchanged():
    assert meridian.__version__.startswith("2.1."), (
        "Re-check OptimizerFacade._direct_incremental against "
        "BudgetOptimizer._create_budget_dataset, then update this guard."
    )
    sig = list(
        inspect.signature(
            optimizer.BudgetOptimizer._get_incremental_outcome_tensors
        ).parameters
    )
    assert sig == ["self", "hist_spend", "spend", "new_data", "optimal_frequency"]
    sig = list(inspect.signature(optimizer._expand_selected_times).parameters)
    assert sig == ["model_context", "start_date", "end_date", "new_data"]
    assert (
        "analyzer" in inspect.signature(optimizer.BudgetOptimizer.__init__).parameters
    )
