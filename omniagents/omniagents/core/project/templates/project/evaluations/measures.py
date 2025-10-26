from omniagents.core.evaluation import evaluation_measure


@evaluation_measure
def always_pass(ctx):
    return {"passed": True}
