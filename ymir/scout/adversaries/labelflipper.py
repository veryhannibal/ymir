from functools import partial

import jax


def make_labelflipper(client, dataset, attack_from, attack_to):
    data = dataset.get_iter(
        "train",
        client.batch_size,
        filter=lambda y: y == attack_from,
        map=partial(labelflip_map, attack_from, attack_to)
    )
    client.update = update(client.opt, client.loss, data)


def labelflip_map(attack_from, attack_to, X, y):
    idfrom = y == attack_from
    y[idfrom] = attack_to
    return (X, y)


def update(opt, loss, data):
    """Do some local training and return the gradient"""
    @jax.jit
    def _apply(params, opt_state, X, y):
        grads = jax.grad(loss)(params, *next(data))
        updates, opt_state = opt.update(grads, opt_state, params)
        return grads, opt_state, updates
    return _apply