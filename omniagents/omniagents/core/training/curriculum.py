"""Curriculum learning for GRPO agent training.

This module provides curriculum learning support, allowing you to train agents
in stages with progressively more complex objectives.

Key benefits of curriculum learning:
1. **Stability**: Master basics before tackling complex tasks
2. **Sample efficiency**: Clearer reward signals at each stage
3. **Debugging**: Easier to identify which skills need improvement
4. **Transfer**: Skills from earlier stages transfer to later stages

Example usage:
    from omniagents.training import (
        CurriculumStage,
        train_grpo_curriculum,
        GRPOTrainingConfig,
    )

    # Define curriculum stages
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
        CurriculumStage(
            name="response_quality",
            measures=["mentions_both_cities", "makes_comparison"],
            epochs=1,
            include_previous_measures=True,
        ),
    ]

    result = train_grpo_curriculum(
        agent=agent,
        suite=suite,
        curriculum=curriculum,
        config=config,
    )
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union, TYPE_CHECKING
import subprocess

from .rewards import MeasureRewardAdapter, measure_to_reward, combine_rewards
from .dataset import eval_suite_to_hf_dataset
from .grpo import (
    GRPOTrainingConfig,
    GRPOTrainingResult,
    GRPOTrainer,
    _extract_hf_model_name,
    _extract_tools_as_json_schemas,
    _get_agent_instructions,
    QWEN25_RESPONSE_SCHEMA,
)

if TYPE_CHECKING:
    from datasets import Dataset
    from transformers import PreTrainedModel, PreTrainedTokenizer
    from trl import GRPOTrainer as TRLGRPOTrainer
    from omniagents.notebook.evaluation import EvalSuite, TestCase
    from agents import Agent


@dataclass
class CurriculumStage:
    """Configuration for a single curriculum stage.

    Each stage defines:
    - Which measures to use as rewards
    - Which training cases to include (via tags)
    - How many epochs to train
    - Whether to accumulate measures from previous stages

    Attributes:
        name: Unique identifier for this stage
        measures: List of measure names to use as rewards
        tags: Filter training cases by these tags (None = all cases)
        epochs: Number of training epochs for this stage (default: 1)
        weights: Optional per-measure weights (default: equal weights)
        include_previous_measures: If True, include measures from all previous
            stages in the reward function (default: False)
        advance_threshold: Optional pass rate threshold to advance early.
            If the model achieves this pass rate on the stage's measures,
            skip remaining epochs and advance to next stage.
        learning_rate: Optional stage-specific learning rate. If not specified,
            uses the config's learning rate.

    Example:
        # Basic stage with tag filtering
        stage1 = CurriculumStage(
            name="basic_tool_use",
            measures=["used_geocode", "used_get_weather"],
            tags=["simple"],
            epochs=1,
        )

        # Advanced stage that builds on previous
        stage2 = CurriculumStage(
            name="multi_step",
            measures=["multiple_geocode_calls", "multiple_weather_calls"],
            tags=["comparison"],
            epochs=2,
            include_previous_measures=True,  # Also reward basic tool use
            weights=[0.3, 0.3, 0.2, 0.2],    # Custom weights including prev
        )
    """

    name: str
    measures: List[str]
    tags: Optional[List[str]] = None
    epochs: int = 1
    weights: Optional[List[float]] = None
    include_previous_measures: bool = False
    advance_threshold: Optional[float] = None
    learning_rate: Optional[float] = None


@dataclass
class StageResult:
    """Result from training a single curriculum stage.

    Attributes:
        name: Name of the stage
        epochs_completed: Number of epochs actually completed (may be less than
            planned if advance_threshold was reached)
        final_loss: Final training loss for this stage
        metrics: Training metrics from TRL
        pass_rate: Pass rate on stage measures (if evaluated)
        advanced_early: Whether the stage advanced early due to threshold
    """

    name: str
    epochs_completed: int
    final_loss: Optional[float] = None
    metrics: List[Dict[str, Any]] = field(default_factory=list)
    pass_rate: Optional[float] = None
    advanced_early: bool = False

    def _repr_html_(self) -> str:
        """Rich HTML display for Jupyter notebooks."""
        import html

        status_color = (
            "#22c55e" if self.pass_rate and self.pass_rate >= 0.8 else "#f59e0b"
        )
        early_badge = (
            ' <span style="color: #22c55e; font-size: 0.8em;">(advanced early)</span>'
            if self.advanced_early
            else ""
        )

        return f"""
        <div style="border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px; margin: 4px 0;">
            <div style="font-weight: bold; color: {status_color};">
                {html.escape(self.name)}{early_badge}
            </div>
            <div style="font-size: 0.9em; color: #6b7280; margin-top: 4px;">
                Epochs: {self.epochs_completed} |
                Loss: {f"{self.final_loss:.4f}" if self.final_loss else 'N/A'} |
                Pass rate: {f"{self.pass_rate:.0%}" if self.pass_rate is not None else 'N/A'}
            </div>
        </div>
        """


@dataclass
class CurriculumTrainingResult:
    """Result of curriculum-based GRPO training.

    This class encapsulates results from training with multiple curriculum
    stages, providing per-stage metrics and the final trained model.

    Attributes:
        stages: Results from each curriculum stage
        total_epochs: Total epochs across all stages
        final_loss: Final loss from the last stage
        trainer: The underlying TRL trainer (for advanced use)
        tools: Tool schemas used during training
        instructions: System instructions used during training
    """

    stages: List[StageResult] = field(default_factory=list)
    total_epochs: int = 0
    final_loss: Optional[float] = None
    trainer: Optional[Any] = None
    # ToolSchemaWrapper instances (dict-like JSON schemas) or plain dicts.
    tools: Optional[List[Any]] = None
    instructions: Optional[str] = None
    model_path: Optional[str] = None
    _inference_ready: bool = field(default=False, repr=False)

    def save_model(self, path: str) -> str:
        """Save the trained model to a path.

        Args:
            path: Directory to save the model

        Returns:
            The path where the model was saved
        """
        if self.trainer is None:
            raise ValueError("No trainer available - training may have failed")

        path = str(Path(path).resolve())
        self.trainer.save_model(path)
        self.model_path = path
        return path

    def to_ollama(
        self,
        model_name: str,
        *,
        model_path: Optional[str] = None,
        llama_cpp_path: Optional[str] = None,
        quantization: str = "q8_0",
    ) -> str:
        """Convert the trained model to Ollama format.

        See GRPOTrainingResult.to_ollama() for full documentation.
        """
        # Delegate to GRPOTrainingResult's implementation
        grpo_result = GRPOTrainingResult(
            model_path=model_path or self.model_path,
            trainer=self.trainer,
            tools=self.tools,
            instructions=self.instructions,
        )
        return grpo_result.to_ollama(
            model_name,
            model_path=model_path,
            llama_cpp_path=llama_cpp_path,
            quantization=quantization,
        )

    def _prepare_for_inference(self) -> None:
        """Prepare the model for inference."""
        if self._inference_ready:
            return

        if self.trainer is None:
            raise ValueError("No trainer available")

        model = self.trainer.model
        model.eval()
        if hasattr(model, "gradient_checkpointing_disable"):
            model.gradient_checkpointing_disable()

        self._inference_ready = True

    def generate(
        self,
        prompt: str,
        *,
        max_new_tokens: int = 256,
        temperature: float = 0.7,
        repetition_penalty: float = 1.2,
    ) -> str:
        """Generate a response using the trained model.

        See GRPOTrainingResult.generate() for full documentation.
        """
        grpo_result = GRPOTrainingResult(
            trainer=self.trainer,
            tools=self.tools,
            instructions=self.instructions,
            _inference_ready=self._inference_ready,
        )
        result = grpo_result.generate(
            prompt,
            max_new_tokens=max_new_tokens,
            temperature=temperature,
            repetition_penalty=repetition_penalty,
        )
        self._inference_ready = grpo_result._inference_ready
        return result

    def plot_curriculum_progress(self) -> None:
        """Plot training progress across curriculum stages.

        Requires matplotlib. Shows loss and pass rate progression.
        """
        try:
            import matplotlib.pyplot as plt
        except ImportError:
            raise ImportError(
                "matplotlib required for plotting. Install with: pip install matplotlib"
            )

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))

        stage_names = [s.name for s in self.stages]
        losses = [s.final_loss or 0 for s in self.stages]
        pass_rates = [s.pass_rate or 0 for s in self.stages]

        # Loss plot
        ax1.bar(stage_names, losses, color="steelblue")
        ax1.set_ylabel("Final Loss")
        ax1.set_title("Loss by Curriculum Stage")
        ax1.tick_params(axis="x", rotation=45)

        # Pass rate plot
        colors = [
            "#22c55e" if pr >= 0.8 else "#f59e0b" if pr >= 0.5 else "#ef4444"
            for pr in pass_rates
        ]
        ax2.bar(stage_names, pass_rates, color=colors)
        ax2.set_ylabel("Pass Rate")
        ax2.set_title("Pass Rate by Curriculum Stage")
        ax2.set_ylim(0, 1)
        ax2.axhline(y=0.8, color="gray", linestyle="--", alpha=0.5)
        ax2.tick_params(axis="x", rotation=45)

        plt.tight_layout()
        plt.show()

    def _repr_html_(self) -> str:
        """Rich HTML display for Jupyter notebooks."""
        import html

        stages_html = ""
        for stage in self.stages:
            status_color = (
                "#22c55e" if stage.pass_rate and stage.pass_rate >= 0.8 else "#f59e0b"
            )
            early_badge = (
                ' <span style="color: #22c55e; font-size: 0.8em;">(early)</span>'
                if stage.advanced_early
                else ""
            )
            pass_rate_str = (
                f"{stage.pass_rate:.0%}" if stage.pass_rate is not None else "N/A"
            )
            loss_str = (
                f"{stage.final_loss:.4f}" if stage.final_loss is not None else "N/A"
            )

            stages_html += f"""
            <tr style="border-bottom: 1px solid #f3f4f6;">
                <td style="padding: 6px 12px;">{html.escape(stage.name)}{early_badge}</td>
                <td style="padding: 6px 12px; text-align: center;">{stage.epochs_completed}</td>
                <td style="padding: 6px 12px; text-align: center;">{loss_str}</td>
                <td style="padding: 6px 12px; text-align: center; color: {status_color};">{pass_rate_str}</td>
            </tr>
            """

        return f"""
        <div style="font-family: system-ui, sans-serif; margin: 8px 0;">
            <div style="font-size: 18px; font-weight: bold; margin-bottom: 8px;">
                Curriculum Training Complete
            </div>
            <div style="color: #6b7280; margin-bottom: 12px;">
                {len(self.stages)} stages | {self.total_epochs} total epochs | Final loss: {f'{self.final_loss:.4f}' if self.final_loss else 'N/A'}
            </div>
            <table style="width: 100%; border-collapse: collapse; border: 1px solid #e5e7eb; border-radius: 8px;">
                <thead style="background: #f9fafb;">
                    <tr>
                        <th style="padding: 6px 12px; text-align: left;">Stage</th>
                        <th style="padding: 6px 12px; text-align: center;">Epochs</th>
                        <th style="padding: 6px 12px; text-align: center;">Loss</th>
                        <th style="padding: 6px 12px; text-align: center;">Pass Rate</th>
                    </tr>
                </thead>
                <tbody>
                    {stages_html}
                </tbody>
            </table>
        </div>
        """

    def __repr__(self) -> str:
        return f"CurriculumTrainingResult({len(self.stages)} stages, {self.total_epochs} epochs)"


def _filter_suite_by_tags(
    suite: "EvalSuite",
    tags: Optional[List[str]],
) -> "EvalSuite":
    """Create a filtered copy of an EvalSuite containing only cases with matching tags.

    Args:
        suite: The original EvalSuite
        tags: Tags to filter by (None = return all cases)

    Returns:
        A new EvalSuite with only matching cases
    """
    if tags is None:
        return suite

    # Import here to avoid circular imports
    from omniagents.notebook.evaluation import EvalSuite as EvalSuiteClass, TestCase

    filtered = EvalSuiteClass(
        name=f"{suite.name} (filtered)", description=suite.description
    )
    for case in suite.cases:
        if case.tags and any(t in case.tags for t in tags):
            filtered.cases.append(case)

    return filtered


def _accumulate_measures(
    curriculum: List[CurriculumStage],
    current_stage: CurriculumStage,
) -> List[str]:
    """Accumulate measures from all stages up to and including current stage.

    Args:
        curriculum: Full curriculum list
        current_stage: The current stage being trained

    Returns:
        List of all measures to use (deduplicated, order preserved)
    """
    all_measures = []
    seen = set()

    for stage in curriculum:
        for measure in stage.measures:
            if measure not in seen:
                all_measures.append(measure)
                seen.add(measure)

        if stage.name == current_stage.name:
            break

    return all_measures


def train_grpo_curriculum(
    agent: "Agent",
    suite: "EvalSuite",
    curriculum: List[CurriculumStage],
    *,
    config: Optional[GRPOTrainingConfig] = None,
    torch_dtype: Optional[str] = None,
    device_map: str = "auto",
    evaluate_after_stage: bool = True,
) -> CurriculumTrainingResult:
    """Train an agent with GRPO using curriculum learning.

    This function trains the agent through multiple stages, where each stage
    focuses on specific skills before advancing to more complex ones.

    Args:
        agent: The omniagents Agent to train
        suite: EvalSuite containing all training data (filtered per stage by tags)
        curriculum: List of CurriculumStage configurations
        config: Base training configuration (per_device_batch_size, etc.)
        torch_dtype: Torch dtype for model loading
        device_map: Device map for model loading
        evaluate_after_stage: Run evaluation after each stage to measure progress

    Returns:
        CurriculumTrainingResult with per-stage results and trained model

    Example:
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
            agent=training_agent,
            suite=full_suite,
            curriculum=curriculum,
            config=GRPOTrainingConfig(
                num_generations=2,
                max_completion_length=1024,
            ),
        )

        result.save_model("./trained_model")
        result.to_ollama("my-agent")
    """
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from trl import GRPOTrainer as TRLGRPOTrainer, GRPOConfig
    except ImportError as e:
        raise ImportError(
            f"Required libraries not installed: {e}. "
            "Install with: pip install torch transformers trl>=0.12.0"
        )

    if config is None:
        config = GRPOTrainingConfig()

    effective_dtype = torch_dtype if torch_dtype is not None else config.torch_dtype

    # Extract model info from agent
    model_name = _extract_hf_model_name(agent.model)
    tools = _extract_tools_as_json_schemas(agent)
    instructions = _get_agent_instructions(agent)

    # Load model once (will be trained across all stages)
    dtype_map = {
        "bfloat16": torch.bfloat16,
        "float16": torch.float16,
        "float32": torch.float32,
    }
    dtype = dtype_map.get(effective_dtype, torch.bfloat16)

    print(f"Loading model: {model_name}...")
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype=dtype,
        device_map=device_map,
    )

    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "left"

    # Set response schema for Qwen2.5 models
    if tools and not getattr(tokenizer, "response_schema", None):
        model_lower = model_name.lower()
        if "qwen2.5" in model_lower or "qwen2-" in model_lower:
            print(f"Setting Qwen2.5 response schema for tool parsing...")
            tokenizer.response_schema = QWEN25_RESPONSE_SCHEMA

    param_count = sum(p.numel() for p in model.parameters())
    print(f"Model loaded! Parameters: {param_count:,}")

    if tools:
        print(f"Agent tools: {[t['function']['name'] for t in tools]}")

    # Train through curriculum stages
    stage_results: List[StageResult] = []
    total_epochs = 0
    trl_trainer = None

    print(f"\n{'='*60}")
    print(f"CURRICULUM TRAINING: {len(curriculum)} stages")
    print(f"{'='*60}\n")

    for stage_idx, stage in enumerate(curriculum):
        print(f"\n[Stage {stage_idx + 1}/{len(curriculum)}] {stage.name}")
        print("-" * 40)

        # Filter dataset by tags
        stage_suite = _filter_suite_by_tags(suite, stage.tags)
        if len(stage_suite) == 0:
            print(f"  Warning: No cases match tags {stage.tags}, skipping stage")
            stage_results.append(
                StageResult(
                    name=stage.name,
                    epochs_completed=0,
                    final_loss=None,
                    pass_rate=None,
                )
            )
            continue

        print(f"  Training cases: {len(stage_suite)} (tags: {stage.tags or 'all'})")

        # Build measures list (potentially cumulative)
        if stage.include_previous_measures and stage_idx > 0:
            measures = _accumulate_measures(curriculum, stage)
            print(f"  Measures (cumulative): {measures}")
        else:
            measures = stage.measures
            print(f"  Measures: {measures}")

        # Build reward function
        reward_adapters = [measure_to_reward(m) for m in measures]

        # Determine weights
        if stage.weights:
            weights = stage.weights
        elif stage.include_previous_measures:
            # Default: give more weight to current stage's measures
            num_prev = len(measures) - len(stage.measures)
            num_curr = len(stage.measures)
            if num_prev > 0:
                prev_weight = 0.3 / num_prev  # 30% total to previous
                curr_weight = 0.7 / num_curr  # 70% to current
                weights = [prev_weight] * num_prev + [curr_weight] * num_curr
            else:
                weights = None
        else:
            weights = None

        reward_fn: Union[MeasureRewardAdapter, Callable]
        if len(reward_adapters) == 1:
            reward_fn = reward_adapters[0]
        else:
            reward_fn = combine_rewards(*reward_adapters, weights=weights)

        # Create stage-specific config
        stage_config = GRPOConfig(
            output_dir=f"{config.output_dir}/stage_{stage_idx}_{stage.name}",
            num_train_epochs=stage.epochs,
            per_device_train_batch_size=config.per_device_batch_size,
            gradient_accumulation_steps=config.gradient_accumulation_steps,
            learning_rate=stage.learning_rate or config.learning_rate,
            max_grad_norm=config.max_grad_norm,
            num_generations=config.num_generations,
            max_completion_length=config.max_completion_length,
            max_tool_calling_iterations=config.max_tool_calling_iterations,
            temperature=config.temperature,
            mask_truncated_completions=config.mask_truncated_completions,
            gradient_checkpointing=config.gradient_checkpointing,
            save_strategy=config.save_strategy,
            logging_steps=config.logging_steps,
            report_to=config.report_to,
        )

        # Convert suite to dataset
        stage_dataset = eval_suite_to_hf_dataset(
            stage_suite, system_prompt=instructions
        )
        print(f"  Dataset ready: {len(stage_dataset)} samples")

        # Build trainer
        trainer_kwargs = {
            "model": model,
            "args": stage_config,
            "train_dataset": stage_dataset,
            "processing_class": tokenizer,
            "reward_funcs": reward_fn,
        }
        if tools:
            trainer_kwargs["tools"] = tools

        trl_trainer = TRLGRPOTrainer(**trainer_kwargs)

        # Train this stage
        print(f"  Training for {stage.epochs} epoch(s)...")
        trl_trainer.train()

        # Collect metrics
        metrics = []
        if hasattr(trl_trainer, "state") and hasattr(trl_trainer.state, "log_history"):
            metrics = trl_trainer.state.log_history

        final_loss = None
        for entry in reversed(metrics):
            if "loss" in entry:
                final_loss = entry["loss"]
                break

        # TODO: Optionally evaluate pass rate after stage
        # This would require running the model on the stage's test cases
        pass_rate = None

        epochs_completed = stage.epochs
        advanced_early = False

        # Record stage result
        stage_results.append(
            StageResult(
                name=stage.name,
                epochs_completed=epochs_completed,
                final_loss=final_loss,
                metrics=metrics,
                pass_rate=pass_rate,
                advanced_early=advanced_early,
            )
        )

        total_epochs += epochs_completed
        print(f"  Completed: loss={f'{final_loss:.4f}' if final_loss else 'N/A'}")

        # Update model reference for next stage (model is modified in-place)
        model = trl_trainer.model

    # Get final loss
    final_loss = stage_results[-1].final_loss if stage_results else None

    print(f"\n{'='*60}")
    print(f"CURRICULUM TRAINING COMPLETE")
    print(f"  Stages: {len(stage_results)}")
    print(f"  Total epochs: {total_epochs}")
    print(f"  Final loss: {f'{final_loss:.4f}' if final_loss else 'N/A'}")
    print(f"{'='*60}\n")

    return CurriculumTrainingResult(
        stages=stage_results,
        total_epochs=total_epochs,
        final_loss=final_loss,
        trainer=trl_trainer,
        tools=tools,
        instructions=instructions,
    )
