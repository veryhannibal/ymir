from functools import partial
from numpy.core.numeric import full

import pandas as pd

import jax
import haiku as hk
import optax

from tqdm import tqdm, trange

import ymir

"""
Evaluation of heterogeneous techniques applied to viceroy.
"""


if __name__ == "__main__":
    xls = pd.ExcelWriter("results.xlsx", mode='a', if_sheet_exists="replace")
    LOCAL_EPOCHS = 10
    TOTAL_ROUNDS = 500
    adv_percent = [0.3, 0.5]
    for comp_alg in ["fedmax", "fedprox"]:
        full_results = pd.DataFrame(columns=["Dataset", "Compression", "Aggregation", "Attack"] + [f"{a:.0%} Adv." for a in adv_percent])
        for dataset_name in ["mnist", "kddcup99"]:
            dataset = ymir.mp.datasets.load(dataset_name)
            for alg in ["foolsgold", "krum", "viceroy"]:
                for attack in ["labelflip", "onoff labelflip", "onoff freerider", "bad mouther"]:
                    cur = {"Dataset": dataset_name, "Compression": comp_alg, "Aggregation": alg, "Attack": attack}
                    for adv_p in adv_percent:
                        print(f"{dataset_name}, {comp_alg}-{alg}, {adv_p:.0%} {attack} adversaries")
                        if dataset_name == "kddcup99":
                            num_endpoints = 20
                            distribution = [[(i + 1 if i >= 11 else i) % dataset.classes, 11] for i in range(num_endpoints)]
                            attack_from, attack_to = 0, 11
                        else:
                            num_endpoints = 10
                            distribution = [[i % dataset.classes] for i in range(num_endpoints)]
                            attack_from, attack_to = 0, 1
                        num_adversaries = round(num_endpoints * adv_p)
                        num_clients = num_endpoints - num_adversaries

                        batch_sizes = [8 for _ in range(num_endpoints)]
                        data = dataset.fed_split(batch_sizes, distribution)
                        train_eval = dataset.get_iter("train", 10_000)
                        test_eval = dataset.get_iter("test")

                        selected_model = lambda: ymir.mp.models.LeNet_300_100(dataset.classes)
                        net = hk.without_apply_rng(hk.transform(lambda x: selected_model()(x)))
                        net_act = hk.without_apply_rng(hk.transform(lambda x: selected_model().act(x)))
                        opt = optax.sgd(0.01)
                        params = net.init(jax.random.PRNGKey(42), next(test_eval)[0])
                        opt_state = opt.init(params)
                        if comp_alg == "fedmax":
                            loss = ymir.mp.losses.fedmax_loss(net, net_act, dataset.classes)
                            client_opt, client_opt_state = opt, opt_state
                        else:
                            loss = ymir.mp.losses.cross_entropy_loss(net, dataset.classes)
                            client_opt = ymir.mp.optimizers.pgd(0.01, 1)
                            client_opt_state = client_opt.init(params)
    
                        network = ymir.mp.network.Network(client_opt, loss)
                        network.add_controller(
                            "main",
                            con_class={
                                "onoff labelflip": partial(
                                    ymir.scout.adversaries.OnOffController,
                                    alg=alg,
                                    num_adversaries=num_adversaries,
                                    max_alpha=1/num_endpoints if alg in ['fed_avg', 'std_dagmm'] else 1,
                                    sharp=alg in ['fed_avg', 'std_dagmm', 'krum']
                                ),
                                "labelflip": ymir.mp.network.Controller,
                                "scaling backdoor": partial(ymir.scout.adversaries.ScalingController, alg=alg, num_adversaries=num_adversaries),
                                "onoff freerider": partial(
                                    ymir.scout.adversaries.OnOffFRController,
                                    alg=alg,
                                    num_adversaries=num_adversaries,
                                    max_alpha=1/num_endpoints if alg in ['fed_avg', 'std_dagmm'] else 1,
                                    sharp=alg in ['fed_avg', 'std_dagmm', 'krum'],
                                    params=params
                                ),
                                "freerider": partial(
                                    ymir.scout.adversaries.FRController, attack_type="delta", num_adversaries=num_adversaries, params=params
                                ),
                                "mouther": partial(
                                    ymir.scout.adversaries.MoutherController, num_adversaries=num_adversaries, victim=0, attack_type=attack
                                )
                            }[attack],
                            is_server=True
                        )
                        for i in range(num_clients):
                            network.add_host("main", ymir.scout.Client(client_opt_state, data[i], LOCAL_EPOCHS))
                        adv_class = {
                            "labelflip": lambda o, _, b, e: ymir.scout.adversaries.LabelFlipper(o, dataset, b, e, attack_from, attack_to),
                            "scaling backdoor": lambda o, _, b, e: ymir.scout.adversaries.Backdoor(o, dataset, b, e, attack_from, attack_to),
                            "onoff labelflip": lambda o, d, b, e: ymir.scout.adversaries.OnOffLabelFlipper(o, d, dataset, b, e, attack_from, attack_to),
                            "onoff freerider": ymir.scout.Client,
                            "mouther": ymir.scout.Client,
                        }[attack]
                        for i in range(num_adversaries):
                            network.add_host("main", adv_class(client_opt_state, data[i + num_clients], batch_sizes[i + num_clients], LOCAL_EPOCHS))
                        controller = network.get_controller("main")
                        if attack != "labelflip":
                            controller.init(params)
                        if "onoff" not in attack:
                            controller.attacking = True

                        model = ymir.Coordinate(alg, opt, opt_state, params, network)
                        meter = ymir.mp.metrics.Neurometer(
                            net,
                            {'train': train_eval, 'test': test_eval},
                            ['accuracy', 'asr'],
                            attack_from=attack_from,
                            attack_to=attack_to
                        )

                        print("Done, beginning training.")

                        # Train/eval loop.
                        pbar = trange(TOTAL_ROUNDS)
                        for _ in pbar:
                            results = meter.add_record(model.params)
                            pbar.set_postfix({'ACC': f"{results['test accuracy']:.3f}", 'ASR': f"{results['test asr']:.3f}", 'ATT': f"{controller.attacking}"})
                            model.fit()
                        asrs = meter.get_results()['test asr']
                        accs = meter.get_results()['test accuracy']
                        cur[f"{adv_p:.0%} Adv."] = f"ACC: {accs.mean()} ({accs.std()}), ASR: {asrs.mean()} ({asrs.std()})"
                        print(f"""Results are {cur[f"{adv_p:.0%} Adv."]}""")
                    full_results = full_results.append(cur, ignore_index=True)
        full_results.to_excel(xls, sheet_name=comp_alg)
    xls.save()