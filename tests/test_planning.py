import pytest
import numpy as np
import pandas as pd

from karpiu.models import MMM
from karpiu.simulation import make_mmm_daily_data
from karpiu.planning import TargetMaximizer, generate_cost_report
from karpiu.utils import adstock_process

@pytest.mark.parametrize(
    "with_events",
    [True, False],
    ids=["w_events", "wo_events"],
)
@pytest.mark.parametrize(
    "seasonality, fs_orders",
    [
        ([365.25], [3]),
        (None, None),
    ],
    ids=["w_seasonality", "wo_seasonality"],
)
def test_target_maximizer_init(with_events, seasonality, fs_orders):
    # data_args
    seed = 2022
    n_steps = 365 * 3
    channels_coef = [0.053, 0.15, 0.19, 0.175, 0.15]
    channels = ["promo", "radio", "search", "social", "tv"]
    features_loc = np.array([2000, 5000, 3850, 3000, 7500])
    features_scale = np.array([550, 2500, 500, 1000, 3500])
    scalability = np.array([3.0, 1.25, 0.8, 1.3, 1.5])
    start_date = "2019-01-01"
    adstock_args = {
    "n_steps": 28,
    "peak_step": np.array([10, 8, 5, 3, 2]),
    "left_growth": np.array([0.05, 0.08, 0.1, 0.5, 0.75]),
    "right_growth": np.array([-0.03, -0.6, -0.5, -0.1, -0.25]),
    }
    best_params = {
    "damped_factor": 0.9057,
    "level_sm_input": 0.01,
    }
    np.random.seed(seed)

    with_yearly_seasonality = False
    if seasonality is not None and len(seasonality) > 0:
        with_yearly_seasonality = True

    df, scalability_df, adstock_df, event_cols = make_mmm_daily_data(
        channels_coef=channels_coef,
        channels=channels,
        features_loc=features_loc,
        features_scale=features_scale,
        scalability=scalability,
        n_steps=n_steps,
        start_date=start_date,
        adstock_args=adstock_args,
        with_yearly_seasonality=with_yearly_seasonality,
        country="US" if with_events else None,
    )
    mmm = MMM(
        kpi_col="sales",
        date_col="date",
        spend_cols=channels,
        event_cols=event_cols,
        seed=seed,
        adstock_df=adstock_df,
        seasonality=[7, 365.25],
        fs_orders=[2, 3],
    )
    mmm.derive_saturation(df=df, scalability_df=scalability_df)
    mmm.set_hyper_params(params=best_params)
    mmm.fit(df, num_warmup=100, num_sample=100, chains=4)

    budget_start = "2020-01-01"
    budget_end = "2020-01-31"

    # test the prediction function preserve with orbit method and karpiu numpy method
    maximizer = TargetMaximizer(
        model=mmm,
        budget_start=budget_start,
        budget_end=budget_end,
        optim_channel=channels,
    )

    coef_matrix = maximizer.optim_coef_matrix
    adstock_matrix =maximizer.optim_adstock_matrix
    input_spend_matrix = df.loc[:, channels].values
    input_spend_matrix = input_spend_matrix[maximizer.calc_mask]
    # zero out before and after with adstock periods; add the background spend 
    # for testing background spend correctness
    input_spend_matrix[:maximizer.n_max_adstock] = 0.
    input_spend_matrix[-maximizer.n_max_adstock:] = 0.
    input_spend_matrix += maximizer.bkg_spend_matrix

    # adstock, log1p, saturation
    transformed_regressors_matrix = adstock_process(input_spend_matrix, adstock_matrix)
    transformed_regressors_matrix = np.log1p(transformed_regressors_matrix / maximizer.optim_sat_array)
    
    reg_comp = np.sum(coef_matrix * transformed_regressors_matrix, axis=-1)
    # from maximizer parameters
    pred_comp_from_optim = np.exp(reg_comp + maximizer.base_comp)

    # from karpiu/orbit method
    pred_df = mmm.predict(df)
    pred_comp = pred_df.loc[maximizer.calc_mask, "prediction"].values
    pred_comp = pred_comp[maximizer.n_max_adstock: ]

    assert np.allclose(pred_comp_from_optim, pred_comp)

def test_target_maximizer():
    # data_args
    seed = 2022
    n_steps = 365 * 3
    channels_coef = [0.053, 0.15, 0.19, 0.175, 0.15]
    channels = ["promo", "radio", "search", "social", "tv"]
    features_loc = np.array([2000, 5000, 3850, 3000, 7500])
    features_scale = np.array([550, 2500, 500, 1000, 3500])
    scalability = np.array([3.0, 1.25, 0.8, 1.3, 1.5])
    start_date = "2019-01-01"
    adstock_args = {
    "n_steps": 28,
    "peak_step": np.array([10, 8, 5, 3, 2]),
    "left_growth": np.array([0.05, 0.08, 0.1, 0.5, 0.75]),
    "right_growth": np.array([-0.03, -0.6, -0.5, -0.1, -0.25]),
    }
    best_params = {
    "damped_factor": 0.9057,
    "level_sm_input": 0.01,
    }
    np.random.seed(seed)

    df, scalability_df, adstock_df, event_cols = make_mmm_daily_data(
        channels_coef=channels_coef,
        channels=channels,
        features_loc=features_loc,
        features_scale=features_scale,
        scalability=scalability,
        n_steps=n_steps,
        start_date=start_date,
        adstock_args=adstock_args,
        with_yearly_seasonality=True,
        with_weekly_seasonality=True,
        country="US",
    )

    mmm = MMM(
        kpi_col="sales",
        date_col="date",
        spend_cols=channels,
        event_cols=event_cols,
        seed=seed,
        adstock_df=adstock_df,
        seasonality=[7, 365.25],
        fs_orders=[2, 3],
    )
    mmm.derive_saturation(df=df, scalability_df=scalability_df)
    mmm.set_hyper_params(params=best_params)
    mmm.fit(df, num_warmup=1000, num_sample=1000, chains=4)
    budget_start = "2021-01-01"
    budget_end = "2021-01-31"
    optim_channels = mmm.get_spend_cols()
    # to be safe in beta version, use sorted list of channels
    optim_channels.sort()

    maximizer = TargetMaximizer(
        model=mmm,
        budget_start=budget_start,
        budget_end=budget_end,
        optim_channel=optim_channels,
    )
    optim_spend_df = maximizer.optimize(maxiter=1000, eps=1e-3)

    optim_spend_matrix = maximizer.get_current_state()
    init_spend_matrix = maximizer.get_init_state()
    # always spend all budget in target maximization; assure the total preserves
    assert np.allclose(np.sum(optim_spend_matrix), np.sum(init_spend_matrix))

    cost_report = generate_cost_report(
        model=mmm,
        channels=optim_channels,
        start=budget_start,
        end=budget_end,
        pre_spend_df=df,
        post_spend_df=optim_spend_df,
    )

    pre_ac = cost_report["pre-opt-avg-cost"].values
    pre_mc = cost_report["pre-opt-marginal-cost"].values
    post_ac = cost_report["post-opt-avg-cost"].values
    post_mc = cost_report["post-opt-marginal-cost"].values
    assert np.all(pre_mc >= pre_ac)
    assert np.all(post_mc >= post_ac)

    # check 2: all marginal cost should be close; within 10% of median
    abs_delta_perc = np.abs(post_mc / np.nanmean(post_mc) - 1.00)
    assert np.all(abs_delta_perc < 0.1)

    cv = np.nanstd(post_mc) / np.nanmean(post_mc)
    assert np.all(cv < 0.1)

    # check2: total predicted response must be higher than current
    optim_pred = mmm.predict(optim_spend_df)
    init_pred = mmm.predict(df)
    measurement_mask = (df["date"] >= maximizer.calc_start) & (
        df["date"] <= maximizer.calc_end
    )
    total_optim_pred = np.sum(optim_pred.loc[measurement_mask, "prediction"].values)
    total_init_pred = np.sum(init_pred.loc[measurement_mask, "prediction"].values)
    assert total_optim_pred - total_init_pred > 0
