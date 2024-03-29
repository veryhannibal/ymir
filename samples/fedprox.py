import re
import jax
import haiku as hk
import optax
from absl import app

from tqdm import tqdm, trange

import ymir

"""
Example of FedProx on the MNIST dataset
"""


def main(_):
    # setup
    print("Setting up the system...")
    num_endpoints = 10
    local_epochs = 10
    dataset = ymir.mp.datasets.load('mnist')
    batch_sizes = [8 for _ in range(num_endpoints)]
    data = dataset.fed_split(batch_sizes, ymir.mp.distributions.lda)
    train_eval = dataset.get_iter("train", 10_000)
    test_eval = dataset.get_iter("test")

    net = hk.without_apply_rng(hk.transform(lambda x: ymir.mp.models.LeNet_300_100(dataset.classes, x)))
    opt = ymir.mp.optimizers.pgd(0.01, 1, local_epochs=local_epochs)
    params = net.init(jax.random.PRNGKey(42), next(test_eval)[0])
    opt_state = opt.init(params)
    loss = ymir.mp.losses.cross_entropy_loss(net, dataset.classes)
    network = ymir.mp.network.Network()
    network.add_controller("main", server=True)
    for d in data:
        network.add_host("main", ymir.regiment.Scout(opt, opt_state, loss, d, local_epochs))

    server_opt = optax.sgd(0.01)
    server_opt_state = server_opt.init(params)

    model = ymir.garrison.fedavg.Captain(params, server_opt, server_opt_state, network)
    meter = ymir.mp.metrics.Neurometer(net, {'train': train_eval, 'test': test_eval})

    print("Done, beginning training.")

    # Train/eval loop.
    for _ in (pbar := trange(500)):
        results = meter.measure(model.params, ['test'])
        pbar.set_postfix({'ACC': f"{results['test acc']:.3f}"})
        model.step()


if __name__ == "__main__":
    app.run(main)