import numpy as np
import jax

from . import server

"""
The multi-krum algorithm proposed in https://papers.nips.cc/paper/2017/hash/f4b9ec30ad9f68f89b29639786cb62ef-Abstract.html
"""
class Server(server.AggServer):
    def __init__(self, params, network, clip=3):
        self.clip = 3

    def update(self, all_grads):
        pass

    def scale(self, all_grads):
        n = len(all_grads)
        X = np.array([jax.flatten_util.ravel_pytree(g)[0] for g in all_grads])
        scores = np.zeros(n)
        distances = np.sum(X**2, axis=1)[:, None] + np.sum(X**2, axis=1)[None] - 2 * np.dot(X, X.T)
        for i in range(len(X)):
            scores[i] = np.sum(np.sort(distances[i])[1:((n - self.clip) - 1)])
        idx = np.argpartition(scores, n - self.clip)[:(n - self.clip)]
        alpha = np.zeros(n)
        alpha[idx] = 1
        return alpha