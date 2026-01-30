"""Training utilities for omniagents.

This module re-exports training utilities for convenient imports.

Example (basic GRPO):
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

Example (curriculum learning):
    from omniagents.training import (
        CurriculumStage,
        train_grpo_curriculum,
        GRPOTrainingConfig,
    )

    curriculum = [
        CurriculumStage(
            name="basic_tool_use",
            measures=["used_geocode", "used_get_weather"],
            tags=["simple"],
            epochs=1,
        ),
        CurriculumStage(
            name="multi_step",
            measures=["multiple_geocode_calls", "multiple_weather_calls"],
            tags=["comparison"],
            epochs=2,
            include_previous_measures=True,
        ),
    ]

    result = train_grpo_curriculum(
        agent=agent,
        suite=suite,
        curriculum=curriculum,
        config=GRPOTrainingConfig(num_generations=2),
    )

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
    # Curriculum Learning
    CurriculumStage,
    StageResult,
    CurriculumTrainingResult,
    train_grpo_curriculum,
    # SFT Training
    SFTTrainingConfig,
    SFTTrainingResult,
    SFTTrainer,
    train_sft,
    export_traces_for_sft,
    eval_results_to_sft_dataset,
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
    # Curriculum Learning
    "CurriculumStage",
    "StageResult",
    "CurriculumTrainingResult",
    "train_grpo_curriculum",
    # SFT Training
    "SFTTrainingConfig",
    "SFTTrainingResult",
    "SFTTrainer",
    "train_sft",
    "export_traces_for_sft",
    "eval_results_to_sft_dataset",
]
