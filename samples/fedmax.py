import re
import jax
import haiku as hk
import optax

from tqdm import tqdm, trange

import ymir

"""
Example of federated averaging on the MNIST dataset
"""


if __name__ == "__main__":
    # setup
    print("Setting up the system...")
    num_endpoints = 20
    dataset = ymir.mp.datasets.load('kddcup99')
    batch_sizes = [64 for _ in range(num_endpoints)]
    data = dataset.fed_split(batch_sizes, [[(i + 1 if i >= 11 else i) % dataset.classes, 11] for i in range(num_endpoints)])
    train_eval = dataset.get_iter("train", 10_000)
    test_eval = dataset.get_iter("test")

    selected_model = lambda: ymir.mp.models.Logistic(dataset.classes)
    net = hk.without_apply_rng(hk.transform(lambda x: selected_model()(x)))
    net_act = hk.without_apply_rng(hk.transform(lambda x: selected_model().act(x)))
    opt = optax.sgd(0.01)
    params = net.init(jax.random.PRNGKey(42), next(test_eval)[0])
    opt_state = opt.init(params)
    loss = ymir.mp.losses.fedmax_loss(net, net_act, dataset.classes)
    network = ymir.mp.network.Network(opt, loss)
    network.add_controller("main", is_server=True)
    for d in data:
        network.add_host("main", ymir.scout.Client(opt_state, d, 60))

    model = ymir.Coordinate("fed_avg", opt, opt_state, params, network)
    meter = ymir.mp.metrics.Neurometer(net, {'train': train_eval, 'test': test_eval}, ['accuracy'])

    print("Done, beginning training.")

    # Train/eval loop.
    TOTAL_ROUNDS = 6_01
    pbar = trange(TOTAL_ROUNDS)
    for round in pbar:
        results = meter.add_record(model.params)
        pbar.set_postfix({'ACC': f"{results['test accuracy']:.3f}"})
        model.fit()