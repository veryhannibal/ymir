import sys
from dataclasses import dataclass
from haiku._src.basic import Linear

import jax
from jax._src.numpy.lax_numpy import expand_dims
import jax.flatten_util
import jax.numpy as jnp
import haiku as hk
import optax
import jaxlib

from sklearn import mixture

from . import server

"""
STD-DAGMM algorithm proposed in https://arxiv.org/abs/1911.12560
"""

# Utility functions/classes

class DA(hk.Module):
    """Deep autoencoder"""
    def __init__(self, in_len, name=None):
        super().__init__(name=name)
        self.encoder = hk.Sequential([
            hk.Linear(60), jax.nn.relu,
            hk.Linear(30), jax.nn.relu,
            hk.Linear(10), jax.nn.relu,
            hk.Linear(1)
        ])
        self.decoder = hk.Sequential([
            hk.Linear(10), jax.nn.tanh,
            hk.Linear(30), jax.nn.tanh,
            hk.Linear(60), jax.nn.tanh,
            hk.Linear(in_len)
        ])

    def __call__(self, X):
        enc = self.encoder(X)
        return enc, self.decoder(enc)


def loss(net):
    """Deep autoencoder MSE loss"""
    @jax.jit
    def _apply(params, x):
        _, z = net.apply(params, x)
        return jnp.mean(optax.l2_loss(z, x))
    return _apply


def da_update(opt, loss):
    """Update function for the autoencoder"""
    @jax.jit
    def _apply(params, opt_state, batch):
        grads = jax.grad(loss)(params, batch)
        updates, opt_state = opt.update(grads, opt_state, params)
        params = optax.apply_updates(params, updates)
        return params, opt_state
    return _apply


@jax.jit
def relative_euclidean_distance(a, b):
    return jnp.linalg.norm(a - b, ord=2) / jnp.clip(jnp.linalg.norm(a, ord=2), a_min=1e-10)


def predict(params, net, gmm, X):
    enc, dec = net.apply(params, X)
    z = jnp.array([[
        jnp.squeeze(e),
        relative_euclidean_distance(x, d),
        optax.cosine_similarity(x, d),
        jnp.std(x)
    ] for x, e, d in zip(X, enc, dec)])
    return gmm.score_samples(z)


# Algorithm functions/classes


class Server(server.AggServer):
    def __init__(self, params, network):
        self.batch_sizes = jnp.array([c.batch_size for c in network.clients])
        x = jnp.array([jax.flatten_util.ravel_pytree(params)[0]])
        self.da = hk.without_apply_rng(hk.transform(lambda x: DA(x[0].shape[0])(x)))
        rng = jax.random.PRNGKey(42)
        self.params = self.da.init(rng, x)
        opt = optax.adamw(0.001, weight_decay=0.0001)
        self.opt_state = opt.init(self.params)
        self.da_update = da_update(opt, loss(self.da))

        self.gmm = mixture.GaussianMixture(4, random_state=0, warm_start=True)

    def update(self, all_grads):
        grads = jnp.array([jax.flatten_util.ravel_pytree(g)[0].tolist() for g in all_grads])
        self.params, self.opt_state = self.da_update(self.params, self.opt_state, grads)
        enc, dec = self.da.apply(self.params, grads)
        z = jnp.array([[
            jnp.squeeze(e),
            relative_euclidean_distance(x, d),
            optax.cosine_similarity(x, d),
            jnp.std(x)
        ] for x, e, d in zip(grads, enc, dec)])
        self.gmm = self.gmm.fit(z)

    def scale(self, all_grads):
        grads = jnp.array([jax.flatten_util.ravel_pytree(g)[0].tolist() for g in all_grads])
        energies = predict(self.params, self.da, self.gmm, grads)
        std = jnp.std(energies)
        avg = jnp.mean(energies)
        mask = jnp.where((energies >= avg - std) * (energies <= avg + std), 1, 0)
        total_dc = jnp.sum(self.batch_sizes * mask)
        return (self.batch_sizes / total_dc) * mask