import pytest

import numpy as np

import elfi

from elfi.methods.parameter_inference import ParameterInference


def test_no_model_parameters(simple_model):
    simple_model.parameter_names = []

    with pytest.raises(Exception):
        ParameterInference(simple_model, [])


@pytest.mark.usefixtures('with_all_clients')
def test_smc(ma2):
    thresholds = [.5, .2]
    N = 1000
    smc = elfi.SMC(ma2['d'], batch_size=20000)
    res = smc.sample(N, thresholds=thresholds)
    dens = res.populations[0].outputs[smc.prior_logpdf]
    # Test that the density is uniform
    assert np.allclose(dens, dens[0])

    # Test that weighted mean is computed
    w = res.weights
    assert w is not None

    samples = res.samples_array
    means = res.sample_means_array
    assert np.allclose(means, np.average(samples, 0, w))
    assert not np.allclose(means, np.mean(samples, 0))

    # Test result summaries
    res.summary()
    res.summary_sample_means_all_populations()


# A superficial test to compensate for test_inference.test_BOLFI not being run on Travis
@pytest.mark.usefixtures('with_all_clients')
def test_BOLFI_short(ma2, distribution_test):

    # Log discrepancy tends to work better
    log_d = elfi.Operation(np.log, ma2['d'])

    bolfi = elfi.BOLFI(log_d, initial_evidence=10, update_interval=10, batch_size=5,
                       bounds={'t1':(-2,2), 't2':(-1, 1)})
    n = 20
    res = bolfi.infer(n)
    assert bolfi.target_model.n_evidence == n
    acq_x = bolfi.target_model._gp.X

    # Test that you can continue the inference where we left off
    res = bolfi.infer(n+5)
    assert bolfi.target_model.n_evidence == n+5
    assert np.array_equal(bolfi.target_model._gp.X[:n,:], acq_x)

    post = bolfi.extract_posterior()

    distribution_test(post, rvs=(acq_x[0,:], acq_x[1:2,:], acq_x[2:4,:]))

    n_samples = 10
    n_chains = 2
    res_sampling = bolfi.sample(n_samples, n_chains=n_chains)
    assert res_sampling.samples_array.shape[1] == 2
    assert len(res_sampling.samples_array) == n_samples//2 * n_chains

    # check the cached predictions for RBF
    x = np.random.random((1, 2))
    bolfi.target_model.is_sampling = True

    pred_mu, pred_var = bolfi.target_model._gp.predict(x)
    pred_cached_mu, pred_cached_var = bolfi.target_model.predict(x)
    assert(np.allclose(pred_mu, pred_cached_mu))
    assert(np.allclose(pred_var, pred_cached_var))

    grad_mu, grad_var = bolfi.target_model._gp.predictive_gradients(x)
    grad_cached_mu, grad_cached_var = bolfi.target_model.predictive_gradients(x)
    assert(np.allclose(grad_mu[:,:,0], grad_cached_mu))
    assert(np.allclose(grad_var, grad_cached_var))
