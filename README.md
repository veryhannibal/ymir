# Ymir
JAX-based Federated learning library + repository of my FL research works

## Installation
As prerequisite, the `jax` and `jaxlib` libraries must be installed, we omit them from the
included `requirements.txt` as the installed library is respective to the system used. We direct
to first follow https://github.com/google/jax#installation then proceed with this section.

Afterwards, the build tool bazel must be installed, we direct you to follow https://bazel.build/

Finally, any of programs in the `samples` and `research` folders may be run/built using bazel.
Simply execute a bazel command directed towards the path of the file (without extensions) you wish to run,
for example, to run the federated averaging sample, execute:

```sh
bazel run samples/fedavg
```

And for the main experiment from the viceroy research project, execute:

```sh
bazel run research/viceroy/main
```

## Usage
We provide examples of the library's usage in the `samples` folder. Though, generally
a program involves initializing shared values and the network architecture, then initialization
of our `Captain` object, and finally calling step from that object.

The following is a generic example snippet
```python
# setup
dataset = ymir.mp.datasets.load(DATASET)
data = dataset.fed_split(batch_sizes, DIST_LIST)
train_eval = dataset.get_iter("train", 10_000)
test_eval = dataset.get_iter("test")

net = hk.without_apply_rng(hk.transform(lambda x: ymir.mp.nets.Net(dataset.classes)(x)))
opt = optax.sgd(0.01)
params = net.init(jax.random.PRNGKey(42), next(test_eval)[0])
opt_state = opt.init(params)
loss = ymir.mp.losses.cross_entropy_loss(net, dataset.classes)
network = ymir.mp.network.Network(opt, loss)
network.add_controller("main", is_server=True)
for d in data:
    network.add_host("main", ymir.regiment.Scout(opt_state, d, CLIENT_EPOCHS))

model = ymir.garrison.AGG_ALG.Captain(params, opt, opt_state, network)

# Train/eval loop.
for round in range(TOTAL_EPOCHS):
    model.step()
```
