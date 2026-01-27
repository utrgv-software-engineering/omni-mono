"""Training utilities for omniagents.

This module re-exports training utilities for convenient imports.

Example:
    from omniagents import Agent, function_tool
    from omniagents.training import train_grpo, GRPOTrainingConfig

    @function_tool
    def calculate(expr: str) -> str:
        '''Evaluate a math expression.'''
        return str(eval(expr))

    agent = Agent(
        name="Math Agent",
        model="Qwen/Qwen3-0.6B",
        tools=[calculate],
        instructions="Solve math problems using the calculator.",
    )

    result = train_grpo(
        agent=agent,
        suite=eval_suite,
        reward_measures=["correct_answer"],
    )

    result.save_model("./trained_model")
    result.to_ollama("my-math-agent")

See omniagents.core.training for full documentation.
"""

from omniagents.core.training import (
    # Reward adapters
    measure_to_reward,
    MeasureRewardAdapter,
    combine_rewards,
    # Dataset converters
    eval_suite_to_hf_dataset,
    eval_cases_to_hf_dataset,
    records_to_hf_dataset,
    # GRPO Training
    GRPOTrainingConfig,
    GRPOTrainingResult,
    train_grpo,
    GRPOTrainer,
    # SFT Training
    SFTTrainingConfig,
    SFTTrainingResult,
    SFTTrainer,
    train_sft,
    export_traces_for_sft,
)

__all__ = [
    # Reward adapters
    "measure_to_reward",
    "MeasureRewardAdapter",
    "combine_rewards",
    # Dataset converters
    "eval_suite_to_hf_dataset",
    "eval_cases_to_hf_dataset",
    "records_to_hf_dataset",
    # GRPO Training
    "GRPOTrainingConfig",
    "GRPOTrainingResult",
    "train_grpo",
    "GRPOTrainer",
    # SFT Training
    "SFTTrainingConfig",
    "SFTTrainingResult",
    "SFTTrainer",
    "train_sft",
    "export_traces_for_sft",
]
