"""Training utilities for omniagents.

This module provides tools to train agents using reinforcement learning
(specifically GRPO) with omniagents evaluation infrastructure as the reward signal,
as well as supervised fine-tuning (SFT) using session traces.

Key components:
- train_grpo: High-level API for GRPO agent training
- train_grpo_curriculum: GRPO with curriculum learning (staged training)
- GRPOTrainingConfig: Configuration for GRPO training
- GRPOTrainingResult: Result with save_model() and to_ollama() methods
- CurriculumStage: Configuration for a single curriculum stage
- measure_to_reward: Convert @measure functions to GRPO reward functions
- eval_suite_to_hf_dataset: Convert EvalSuite to HuggingFace Dataset
- export_traces_for_sft: Export session traces for supervised fine-tuning

Example usage:
    from omniagents import Agent, function_tool
    from omniagents.notebook import EvalSuite, measure, EvalContext, pass_reason, fail_reason
    from omniagents.training import train_grpo, GRPOTrainingConfig

    # Define tools
    @function_tool
    def calculate(expr: str) -> str:
        '''Evaluate a math expression.'''
        return str(eval(expr))

    # Create agent
    agent = Agent(
        name="Math Agent",
        model="Qwen/Qwen3-0.6B",
        tools=[calculate],
        instructions="You are a math assistant. Use the calculator tool.",
    )

    # Define a measure (same as evaluation)
    @measure
    def correct_answer(ctx: EvalContext):
        response = ctx.final_assistant_message.text if ctx.final_assistant_message else ""
        expected = ctx.expect.get('expected_answer')
        if response.strip() == expected:
            return pass_reason("Correct")
        return fail_reason(f"Expected: {expected}")

    # Create evaluation suite
    suite = EvalSuite.from_records(data, input_fn=..., expect_fn=...)

    # Train with GRPO
    config = GRPOTrainingConfig(num_generations=4, num_train_epochs=1)
    result = train_grpo(
        agent=agent,
        suite=suite,
        reward_measures=["correct_answer"],
        config=config,
    )

    # Save and convert for deployment
    result.save_model("./trained_model")
    result.to_ollama("my-math-agent")

    # Export traces for SFT
    from omniagents.training import export_traces_for_sft
    dataset = export_traces_for_sft(
        "my_project", "my_agent",
        judgment="acceptable",  # Filter by Studio judgment
    )
"""

from .rewards import measure_to_reward, MeasureRewardAdapter, combine_rewards
from .dataset import (
    eval_suite_to_hf_dataset,
    eval_cases_to_hf_dataset,
    records_to_hf_dataset,
)
from .grpo import GRPOTrainingConfig, GRPOTrainingResult, train_grpo, GRPOTrainer
from .curriculum import (
    CurriculumStage,
    StageResult,
    CurriculumTrainingResult,
    train_grpo_curriculum,
)
from .sft import (
    export_traces_for_sft,
    eval_results_to_sft_dataset,
    SFTTrainingConfig,
    SFTTrainingResult,
    SFTTrainer,
    train_sft,
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
    "EchoGRPOTrainer",
    # Sandbox training environments
    "SandboxTrainingEnvironment",
    "make_environment_factory",
    "sandbox_verifier_reward",
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


def __getattr__(name):
    # Lazily expose the ECHO trainer so importing this package does not require
    # `trl` to be installed (EchoGRPOTrainer subclasses trl.GRPOTrainer).
    if name == "EchoGRPOTrainer":
        from .echo import EchoGRPOTrainer

        return EchoGRPOTrainer
    if name in (
        "SandboxTrainingEnvironment",
        "make_environment_factory",
        "sandbox_verifier_reward",
    ):
        from . import sandbox_env

        return getattr(sandbox_env, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
