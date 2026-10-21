@evaluation_measure
def {function_name}(ctx):
    judge = ctx.judge_result('{sanitized}')
    if not judge:
        return fail_reason('no_judge_result')
    return judge.to_result()
