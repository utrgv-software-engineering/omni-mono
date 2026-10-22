from omniagents.core.eval.stratified_sampler import StratifiedBatchSampler


def test_stratified_sampler_balances_classes_with_seed():
    dataset = [{"label": True, "id": i} for i in range(6)] + [
        {"label": False, "id": i} for i in range(6, 12)
    ]
    sampler = StratifiedBatchSampler(
        trainset=dataset,
        minibatch_size=6,
        label_getter="label",
        pos_fraction=0.5,
        min_pos_per_batch=2,
        min_neg_per_batch=2,
        with_replacement=False,
        seed=123,
    )

    batch = sampler.next_minibatch_ids(None, None)
    assert len(batch) == 6
    positives = [idx for idx in batch if dataset[idx]["label"] is True]
    negatives = [idx for idx in batch if dataset[idx]["label"] is False]
    assert len(positives) == 3
    assert len(negatives) == 3


def test_stratified_sampler_handles_single_class_and_callable_label():
    class Item:
        def __init__(self, label):
            self.flag = label

    dataset = [Item(True) for _ in range(5)]

    sampler = StratifiedBatchSampler(
        trainset=dataset,
        minibatch_size=3,
        label_getter=lambda x: x.flag,
        pos_fraction=None,
        min_pos_per_batch=1,
        min_neg_per_batch=0,
        with_replacement=True,
        seed=42,
    )

    batch = sampler.next_minibatch_ids(None, None)
    assert len(batch) == 3
    assert all(0 <= idx < len(dataset) for idx in batch)
