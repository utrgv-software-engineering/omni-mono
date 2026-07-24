"""SFT (Supervised Fine-Tuning) training for omniagents.

This module provides a high-level API for supervised fine-tuning of local models
using session traces from omniagents.

SFT trains a model to imitate successful conversations by:
1. Collecting good examples (filtered by judgment, measure, or custom logic)
2. Training the model to reproduce assistant responses

Example:
    from omniagents.training import SFTTrainer, SFTTrainingConfig

    # Train from session traces
    trainer = SFTTrainer(model_name="Qwen/Qwen2.5-0.5B")
    result = trainer.train_from_traces(
        "my_project", "my_agent",
        judgment="acceptable",
    )
    result.save_model("./sft_trained")

    # Or export and train separately
    from omniagents.training import export_traces_for_sft
    dataset = export_traces_for_sft("my_project", "my_agent", judgment="acceptable")
    result = trainer.train(dataset)
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional, Any, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from datasets import Dataset
    from transformers import PreTrainedModel, PreTrainedTokenizer
    from trl import SFTTrainer as TRLSFTTrainer

try:
    from datasets import Dataset
except ImportError:
    Dataset = None  # type: ignore


@dataclass
class SFTTrainingConfig:
    """Configuration for SFT training.

    This wraps TRL's SFTConfig with sensible defaults for agent training.

    Attributes:
        # Training parameters
        num_train_epochs: Number of training epochs (default: 1)
        per_device_batch_size: Samples per batch (default: 2)
        gradient_accumulation_steps: Gradient accumulation (default: 4)
        learning_rate: Learning rate (default: 2e-5)
        max_grad_norm: Gradient clipping (default: 1.0)
        warmup_ratio: Warmup ratio (default: 0.1)

        # Sequence parameters
        max_length: Maximum sequence length (default: 2048)
        packing: Whether to pack sequences for efficiency (default: False)

        # Output
        output_dir: Directory for checkpoints and logs
        save_strategy: When to save ("no", "epoch", "steps")
        logging_steps: Log every N steps

        # Reporting
        report_to: Where to report metrics ("none", "wandb", "tensorboard")
    """

    # Training parameters
    num_train_epochs: int = 1
    per_device_batch_size: int = 2
    gradient_accumulation_steps: int = 4
    learning_rate: float = 2e-5
    max_grad_norm: float = 1.0
    warmup_ratio: float = 0.1

    # Sequence parameters
    max_length: int = 2048
    packing: bool = False

    # Output
    output_dir: str = "./sft_output"
    save_strategy: str = "no"
    logging_steps: int = 10

    # Reporting
    report_to: str = "none"

    def to_trl_config(self) -> "SFTConfig":
        """Convert to TRL's SFTConfig."""
        try:
            from trl import SFTConfig
        except ImportError:
            raise ImportError(
                "trl library is required for SFT training. "
                "Install with: pip install trl>=0.12.0"
            )

        return SFTConfig(
            output_dir=self.output_dir,
            num_train_epochs=self.num_train_epochs,
            per_device_train_batch_size=self.per_device_batch_size,
            gradient_accumulation_steps=self.gradient_accumulation_steps,
            learning_rate=self.learning_rate,
            max_grad_norm=self.max_grad_norm,
            warmup_ratio=self.warmup_ratio,
            max_length=self.max_length,
            packing=self.packing,
            save_strategy=self.save_strategy,
            logging_steps=self.logging_steps,
            report_to=self.report_to,
        )


@dataclass
class SFTTrainingResult:
    """Result of SFT training.

    Attributes:
        model_path: Path to the saved model (if saved)
        metrics: Training metrics from the trainer
        final_loss: Final training loss
        trainer: The underlying TRL SFTTrainer (for advanced use)
    """

    model_path: Optional[str] = None
    metrics: List[Dict[str, Any]] = field(default_factory=list)
    final_loss: Optional[float] = None
    trainer: Optional[Any] = None

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
        """Convert the trained model to Ollama format and register it.

        This converts the HuggingFace model (safetensors) to GGUF format using
        llama.cpp's convert_hf_to_gguf.py script, then registers it with Ollama.

        The chat template is automatically detected from the model architecture
        and fetched from a matching Ollama base model.

        Args:
            model_name: Name to register the model as in Ollama
            model_path: Path to the model (defaults to self.model_path)
            llama_cpp_path: Path to llama.cpp repo (searches common locations if not specified)
            quantization: GGUF quantization type (default: "q8_0"). Options: f32, f16, bf16, q8_0, etc.

        Returns:
            The Ollama model name that can be used with litellm/ollama_chat/

        Example:
            result = trainer.train(dataset)
            result.save_model("./trained_model")
            ollama_name = result.to_ollama("my-trained-agent")

            # Now use with omniagents
            agent.model = f"ollama_chat/{ollama_name}"
        """
        import subprocess
        import json

        path = model_path or self.model_path
        if path is None:
            raise ValueError(
                "No model path available. Call save_model() first or provide model_path."
            )

        path = Path(path).resolve()
        gguf_path = path / "model.gguf"
        modelfile_path = path / "Modelfile"

        # Find llama.cpp convert script
        convert_script = self._find_llama_cpp_convert(llama_cpp_path)

        print(f"Converting model to Ollama format: {model_name}")
        print(f"  Source: {path}")
        print(f"  Using: {convert_script}")

        try:
            # Step 1: Convert safetensors to GGUF using llama.cpp
            print(f"  Converting to GGUF format (quantization: {quantization})...")
            convert_cmd = [
                "python",
                str(convert_script),
                str(path),
                "--outtype",
                quantization,
                "--outfile",
                str(gguf_path),
            ]

            result = subprocess.run(
                convert_cmd,
                check=True,
                capture_output=True,
                text=True,
            )
            print(f"  Created GGUF: {gguf_path}")

            # Step 2: Get chat template from base model
            template, stop_tokens = self._get_ollama_template(path)

            # Step 3: Create Modelfile with template
            modelfile_content = f"FROM {gguf_path}\n\n"
            if template:
                modelfile_content += f'TEMPLATE """{template}"""\n\n'
            for stop in stop_tokens:
                modelfile_content += f'PARAMETER stop "{stop}"\n'

            modelfile_path.write_text(modelfile_content)

            # Step 4: Create Ollama model from GGUF
            print("  Registering with Ollama...")
            ollama_cmd = ["ollama", "create", model_name, "-f", str(modelfile_path)]

            result = subprocess.run(
                ollama_cmd,
                check=True,
                capture_output=True,
                text=True,
            )
            print(f"Successfully created Ollama model: {model_name}")
            return model_name

        except subprocess.CalledProcessError as e:
            error_msg = e.stderr or e.stdout or str(e)
            raise RuntimeError(
                f"Failed to create Ollama model. Error: {error_msg}"
            ) from e
        except FileNotFoundError as e:
            if "ollama" in str(e):
                raise RuntimeError(
                    "ollama command not found. Please install Ollama: https://ollama.com/download"
                ) from e
            raise
        finally:
            # Clean up the Modelfile (keep the GGUF for potential reuse)
            if modelfile_path.exists():
                modelfile_path.unlink()

    def _find_llama_cpp_convert(self, llama_cpp_path: Optional[str] = None) -> Path:
        """Find the llama.cpp convert_hf_to_gguf.py script."""
        import os

        # Check explicit argument first
        if llama_cpp_path:
            script = Path(llama_cpp_path) / "convert_hf_to_gguf.py"
            if script.exists():
                return script
            raise FileNotFoundError(
                f"convert_hf_to_gguf.py not found at {llama_cpp_path}"
            )

        # Check LLAMA_CPP_PATH environment variable
        env_path = os.environ.get("LLAMA_CPP_PATH")
        if env_path:
            script = Path(env_path) / "convert_hf_to_gguf.py"
            if script.exists():
                return script

        # Search common locations
        search_paths = [
            Path.home() / "code" / "llama.cpp",
            Path.home() / "llama.cpp",
            Path.home() / "projects" / "llama.cpp",
            Path("/opt/llama.cpp"),
            Path.cwd() / "llama.cpp",
        ]

        for base in search_paths:
            script = base / "convert_hf_to_gguf.py"
            if script.exists():
                return script

        raise FileNotFoundError(
            "llama.cpp not found. Please either:\n"
            "  1. Set LLAMA_CPP_PATH in your .env file\n"
            "  2. Clone to ~/code/llama.cpp (auto-detected)\n"
            "  3. Pass llama_cpp_path='/path/to/llama.cpp' to to_ollama()\n\n"
            "To install:\n"
            "  git clone https://github.com/ggml-org/llama.cpp.git\n"
            "  pip install -r llama.cpp/requirements.txt"
        )

    def _get_ollama_template(self, model_path: Path) -> tuple:
        """Get the Ollama chat template based on the model architecture.

        Reads config.json to detect the model architecture, maps it to a known
        Ollama base model, and fetches the template from that model.

        Returns:
            Tuple of (template_string, list_of_stop_tokens)
        """
        import subprocess
        import json

        # Map HuggingFace architectures to Ollama base models
        ARCH_TO_OLLAMA_MODEL = {
            "Qwen2ForCausalLM": "qwen2.5:0.5b",
            "Qwen2_5ForCausalLM": "qwen2.5:0.5b",
            "Qwen3ForCausalLM": "qwen3:0.6b",
            "LlamaForCausalLM": "llama3.2:1b",
            "MistralForCausalLM": "mistral:7b",
            "GemmaForCausalLM": "gemma:2b",
            "Gemma2ForCausalLM": "gemma2:2b",
            "PhiForCausalLM": "phi3:mini",
            "Phi3ForCausalLM": "phi3:mini",
        }

        # Default stop tokens for common architectures
        ARCH_TO_STOP_TOKENS = {
            "Qwen2ForCausalLM": ["<|im_start|>", "<|im_end|>"],
            "Qwen2_5ForCausalLM": ["<|im_start|>", "<|im_end|>"],
            "Qwen3ForCausalLM": ["<|im_start|>", "<|im_end|>"],
            "LlamaForCausalLM": ["<|eot_id|>"],
            "MistralForCausalLM": ["</s>"],
            "GemmaForCausalLM": ["<end_of_turn>"],
            "Gemma2ForCausalLM": ["<end_of_turn>"],
            "PhiForCausalLM": ["<|end|>"],
            "Phi3ForCausalLM": ["<|end|>"],
        }

        # Read model config to get architecture
        config_path = model_path / "config.json"
        if not config_path.exists():
            print("  Warning: config.json not found, using basic template")
            return None, []

        with open(config_path) as f:
            config = json.load(f)

        architectures = config.get("architectures", [])
        if not architectures:
            print("  Warning: No architecture found in config.json")
            return None, []

        arch = architectures[0]
        print(f"  Detected architecture: {arch}")

        # Get corresponding Ollama model
        ollama_model = ARCH_TO_OLLAMA_MODEL.get(arch)
        stop_tokens = ARCH_TO_STOP_TOKENS.get(arch, [])

        if not ollama_model:
            print(f"  Warning: Unknown architecture {arch}, using basic template")
            return None, stop_tokens

        # Try to fetch template from Ollama
        try:
            result = subprocess.run(
                ["ollama", "show", ollama_model, "--template"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                print(f"  Using template from: {ollama_model}")
                return result.stdout, stop_tokens
            else:
                # Model not available, try to pull it
                print(f"  Pulling base model for template: {ollama_model}")
                subprocess.run(
                    ["ollama", "pull", ollama_model],
                    capture_output=True,
                    timeout=300,
                )
                # Retry getting template
                result = subprocess.run(
                    ["ollama", "show", ollama_model, "--template"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout, stop_tokens
        except Exception as e:
            print(f"  Warning: Could not fetch template: {e}")

        return None, stop_tokens


class SFTTrainer:
    """High-level SFT trainer for omniagents.

    This class wraps TRL's SFTTrainer with omniagents-specific functionality:
    - Direct training from session traces with filtering
    - Integration with judgment/measure-based filtering
    - Simplified API for common use cases

    Example:
        from omniagents.training import SFTTrainer, SFTTrainingConfig

        # Create trainer
        trainer = SFTTrainer(model_name="Qwen/Qwen2.5-0.5B")

        # Train from session traces
        result = trainer.train_from_traces(
            "my_project", "my_agent",
            judgment="acceptable",
        )

        # Or from a dataset
        dataset = export_traces_for_sft(...)
        result = trainer.train(dataset)

        # Save the model
        result.save_model("./trained_model")
    """

    def __init__(
        self,
        model_name: str,
        *,
        config: Optional[SFTTrainingConfig] = None,
        torch_dtype: str = "bfloat16",
        device_map: str = "auto",
    ):
        """Initialize the SFT trainer.

        Args:
            model_name: HuggingFace model name or path (e.g., "Qwen/Qwen2.5-0.5B")
            config: Training configuration (default: SFTTrainingConfig())
            torch_dtype: Torch dtype for model ("bfloat16", "float16", "float32")
            device_map: Device map for model loading ("auto", "cpu", "cuda")
        """
        self.model_name = model_name
        self.config = config or SFTTrainingConfig()
        self.torch_dtype = torch_dtype
        self.device_map = device_map

        self._model = None
        self._tokenizer = None
        self._trl_trainer = None

    def _load_model(self):
        """Load the model and tokenizer."""
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError:
            raise ImportError(
                "transformers and torch are required. "
                "Install with: pip install transformers torch"
            )

        # Map dtype string to torch dtype
        dtype_map = {
            "bfloat16": torch.bfloat16,
            "float16": torch.float16,
            "float32": torch.float32,
        }
        dtype = dtype_map.get(self.torch_dtype, torch.bfloat16)

        print(f"Loading model: {self.model_name}...")
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            torch_dtype=dtype,
            device_map=self.device_map,
        )

        # Set pad token if needed
        if self._tokenizer.pad_token is None:
            self._tokenizer.pad_token = self._tokenizer.eos_token

        param_count = sum(p.numel() for p in self._model.parameters())
        print(f"Model loaded! Parameters: {param_count:,}")

    def train(
        self,
        dataset: "Dataset",
    ) -> SFTTrainingResult:
        """Train the model using SFT on a dataset.

        Args:
            dataset: HuggingFace Dataset with 'messages' column (conversational format)
                     or 'prompt'/'completion' columns

        Returns:
            SFTTrainingResult with trained model and metrics
        """
        try:
            from trl import SFTTrainer as TRLSFTTrainer
        except ImportError:
            raise ImportError(
                "trl library is required for SFT training. "
                "Install with: pip install trl>=0.12.0"
            )

        # Load model if not already loaded
        if self._model is None:
            self._load_model()

        print(f"Training on {len(dataset)} samples...")

        # Create TRL trainer
        trl_config = self.config.to_trl_config()
        print(f"SFT Configuration:")
        print(f"  Max sequence length: {trl_config.max_length}")
        print(f"  Learning rate: {trl_config.learning_rate}")
        print(f"  Epochs: {trl_config.num_train_epochs}")
        print(f"  Packing: {trl_config.packing}")

        self._trl_trainer = TRLSFTTrainer(
            model=self._model,
            args=trl_config,
            train_dataset=dataset,
            processing_class=self._tokenizer,
        )

        # Train!
        print("\nStarting SFT training...\n")
        self._trl_trainer.train()

        # Collect metrics
        metrics = []
        if hasattr(self._trl_trainer, "state") and hasattr(
            self._trl_trainer.state, "log_history"
        ):
            metrics = self._trl_trainer.state.log_history

        final_loss = None
        if metrics:
            for entry in reversed(metrics):
                if "loss" in entry:
                    final_loss = entry["loss"]
                    break

        return SFTTrainingResult(
            metrics=metrics,
            final_loss=final_loss,
            trainer=self._trl_trainer,
        )

    def train_from_traces(
        self,
        project_slug: str,
        agent_slug: str,
        *,
        judgment: Optional[Literal["acceptable", "unacceptable"]] = None,
        measure: Optional[Union[str, Callable]] = None,
        measure_passed: bool = True,
        filter_fn: Optional[Callable[[List[Dict[str, Any]]], bool]] = None,
        include_system: bool = True,
        max_sessions: Optional[int] = None,
    ) -> SFTTrainingResult:
        """Train directly from session traces.

        This combines export_traces_for_sft() and train() into a single call.

        Args:
            project_slug: Project identifier for the sessions database
            agent_slug: Agent identifier for the sessions database
            judgment: Filter by trace judgment ("acceptable" or "unacceptable")
            measure: Measure name or function to filter by
            measure_passed: Include sessions where measure passed (True) or failed (False)
            filter_fn: Custom predicate on history
            include_system: Include system messages
            max_sessions: Maximum sessions to include

        Returns:
            SFTTrainingResult with trained model and metrics

        Example:
            trainer = SFTTrainer(model_name="Qwen/Qwen2.5-0.5B")
            result = trainer.train_from_traces(
                "my_project", "my_agent",
                judgment="acceptable",
            )
            result.save_model("./sft_model")
        """
        # Export traces to dataset
        print(f"Exporting traces from {project_slug}/{agent_slug}...")
        dataset = export_traces_for_sft(
            project_slug,
            agent_slug,
            judgment=judgment,
            measure=measure,
            measure_passed=measure_passed,
            filter_fn=filter_fn,
            format="conversational",
            include_system=include_system,
            max_sessions=max_sessions,
        )

        if len(dataset) == 0:
            raise ValueError(
                f"No sessions found matching the filter criteria in {project_slug}/{agent_slug}. "
                "Try adjusting your filters or check that sessions exist."
            )

        print(f"Found {len(dataset)} conversations")
        return self.train(dataset)

    @property
    def model(self):
        """Access the underlying model."""
        return self._model

    @property
    def tokenizer(self):
        """Access the tokenizer."""
        return self._tokenizer


def train_sft(
    model_name: str,
    project_slug: str,
    agent_slug: str,
    *,
    config: Optional[SFTTrainingConfig] = None,
    judgment: Optional[Literal["acceptable", "unacceptable"]] = None,
    measure: Optional[Union[str, Callable]] = None,
    measure_passed: bool = True,
    filter_fn: Optional[Callable[[List[Dict[str, Any]]], bool]] = None,
    include_system: bool = True,
    max_sessions: Optional[int] = None,
    torch_dtype: str = "bfloat16",
    device_map: str = "auto",
) -> SFTTrainingResult:
    """Train a model with SFT using session traces.

    This is the simplest way to fine-tune a model on successful conversations.
    For more control, use the SFTTrainer class directly.

    Args:
        model_name: HuggingFace model name or path
        project_slug: Project identifier for sessions
        agent_slug: Agent identifier for sessions
        config: Training configuration (default: SFTTrainingConfig())
        judgment: Filter by trace judgment ("acceptable" or "unacceptable")
        measure: Measure name or function to filter by
        measure_passed: Include sessions where measure passed
        filter_fn: Custom predicate on history
        include_system: Include system messages
        max_sessions: Maximum sessions to include
        torch_dtype: Torch dtype for model
        device_map: Device map for model loading

    Returns:
        SFTTrainingResult with trained model and metrics

    Example:
        from omniagents.training import train_sft, SFTTrainingConfig

        config = SFTTrainingConfig(
            num_train_epochs=1,
            learning_rate=2e-5,
        )

        result = train_sft(
            model_name="Qwen/Qwen2.5-0.5B",
            project_slug="my_project",
            agent_slug="my_agent",
            judgment="acceptable",
            config=config,
        )

        result.save_model("./sft_trained")
    """
    trainer = SFTTrainer(
        model_name=model_name,
        config=config,
        torch_dtype=torch_dtype,
        device_map=device_map,
    )

    return trainer.train_from_traces(
        project_slug,
        agent_slug,
        judgment=judgment,
        measure=measure,
        measure_passed=measure_passed,
        filter_fn=filter_fn,
        include_system=include_system,
        max_sessions=max_sessions,
    )


def eval_results_to_sft_dataset(
    results: Any,  # EvalSuiteResults - avoid import cycle
    *,
    filter_passed: bool = True,
    filter_fn: Optional[Callable[[Any], bool]] = None,
    include_system: bool = True,
    include_tool_calls: bool = True,
) -> "Dataset":
    """
    Convert evaluation results to SFT training dataset.

    This is the key function for distillation - it extracts successful
    teacher runs and converts them to a format suitable for SFT training,
    preserving tool calls so the student can learn agentic behavior.

    Args:
        results: EvalSuiteResults from running suite.run(agent)
        filter_passed: If True, only include results where all measures passed
        filter_fn: Optional custom filter function that takes an EvalResult
        include_system: Include system messages in training data
        include_tool_calls: Include tool calls in OpenAI format (essential for
                           teaching tool use to the student model)

    Returns:
        HuggingFace Dataset with 'messages' column in OpenAI chat format

    Example:
        # Run teacher model to generate expert traces
        teacher_results = await suite.run(teacher_agent)

        # Convert successful runs to training dataset
        dataset = eval_results_to_sft_dataset(
            teacher_results,
            filter_passed=True,
            include_tool_calls=True,
        )

        # Train student model
        trainer = SFTTrainer("Qwen/Qwen2.5-0.5B-Instruct")
        result = trainer.train(dataset)
    """
    if Dataset is None:
        raise ImportError(
            "The 'datasets' package is required for eval_results_to_sft_dataset. "
            "Install it with: pip install datasets"
        )

    all_messages = []

    for result in results.results:
        # Apply filters
        if filter_passed and not result.passed:
            continue
        if filter_fn and not filter_fn(result):
            continue

        # Get history from result
        history = getattr(result, "history", None)
        if not history:
            continue

        # Convert history to OpenAI format
        if include_tool_calls:
            messages = _convert_history_to_openai_format(history, include_system)
        else:
            messages = []
            for msg in history:
                role = msg.get("role")
                if role not in ("system", "user", "assistant"):
                    continue
                if role == "system" and not include_system:
                    continue
                content = _extract_content(msg)
                if content:
                    messages.append({"role": role, "content": content})

        if messages:
            all_messages.append(messages)

    return Dataset.from_dict({"messages": all_messages})


def export_traces_for_sft(
    project_slug: str,
    agent_slug: str,
    *,
    # Filtering options
    judgment: Optional[Literal["acceptable", "unacceptable"]] = None,
    measure: Optional[Union[str, Callable]] = None,
    measure_passed: bool = True,
    filter_fn: Optional[Callable[[List[Dict[str, Any]]], bool]] = None,
    # Output format
    format: Literal["conversational", "prompt_completion"] = "conversational",
    include_system: bool = True,
    # Limits
    max_sessions: Optional[int] = None,
) -> "Dataset":
    """
    Export session traces for SFT training.

    This function loads sessions from the omniagents session database and
    optionally filters them using judgments, measures, or custom predicates.
    The output is a HuggingFace Dataset ready for SFTTrainer.

    Args:
        project_slug: Project identifier for the sessions database.
        agent_slug: Agent identifier for the sessions database.
        judgment: Filter by trace judgment from Studio ("acceptable" or "unacceptable").
                  Requires traces to have been analyzed in Studio.
        measure: Measure name (string) or measure function to replay on history.
                 Sessions are filtered based on whether the measure passes or fails.
        measure_passed: If True (default), include sessions where measure passed.
                        If False, include sessions where measure failed.
        filter_fn: Custom predicate function that takes a history (list of messages)
                   and returns True to include, False to exclude.
        format: Output format for SFTTrainer:
                - "conversational": {"messages": [{"role": ..., "content": ...}, ...]}
                - "prompt_completion": {"prompt": ..., "completion": ...}
        include_system: Include system messages in output (default True).
        max_sessions: Maximum number of sessions to include (after filtering).

    Returns:
        HuggingFace Dataset ready for SFTTrainer.

    Raises:
        ImportError: If the 'datasets' package is not installed.
        ValueError: If invalid parameters are provided.

    Example:
        # Export conversations marked as good in Studio UI
        dataset = export_traces_for_sft(
            "my_project", "my_agent",
            judgment="acceptable",
        )

        # Export conversations where a custom measure passed
        @measure
        def good_response(ctx):
            # ... evaluation logic
            return pass_reason() if ok else fail_reason("bad")

        dataset = export_traces_for_sft(
            "my_project", "my_agent",
            measure=good_response,
        )

        # Train with SFTTrainer
        from trl import SFTTrainer
        trainer = SFTTrainer(model="...", train_dataset=dataset)
        trainer.train()
    """
    if Dataset is None:
        raise ImportError(
            "The 'datasets' package is required for export_traces_for_sft. "
            "Install it with: pip install datasets"
        )

    # Import here to avoid circular imports
    from omniagents.core.session.history_db import list_sessions, load_history
    from omniagents.core.evaluation.context import build_eval_context
    from omniagents.core.evaluation.registry import get_measure

    # Load judgment data if filtering by judgment
    judgments_by_session: Dict[str, str] = {}
    if judgment is not None:
        judgments_by_session = _load_judgments(project_slug)

    # Resolve measure function if provided as string
    measure_fn: Optional[Callable] = None
    if measure is not None:
        if isinstance(measure, str):
            measure_fn = get_measure(measure)
        elif callable(measure):
            measure_fn = measure
        else:
            raise ValueError(
                f"measure must be a string (measure name) or callable, got {type(measure)}"
            )

    # List all sessions
    sessions = list_sessions(project_slug=project_slug, agent_slug=agent_slug)

    # Collect filtered histories
    filtered_histories: List[List[Dict[str, Any]]] = []

    for session_info in sessions:
        session_id = session_info["id"]

        # Skip archived sessions by default
        if session_info.get("archived", False):
            continue

        # Skip empty sessions
        if session_info.get("message_count", 0) == 0:
            continue

        # Filter by judgment if specified
        if judgment is not None:
            session_judgment = judgments_by_session.get(session_id)
            if session_judgment != judgment:
                continue

        # Load full history
        history = load_history(
            session_id, project_slug=project_slug, agent_slug=agent_slug
        )

        if not history:
            continue

        # Filter by measure if specified
        if measure_fn is not None:
            ctx = build_eval_context(history=history)
            try:
                result = measure_fn(ctx)
                passed = (
                    result.get("passed", False) if isinstance(result, dict) else False
                )
            except Exception:
                # If measure fails to run, skip this session
                continue

            if passed != measure_passed:
                continue

        # Apply custom filter
        if filter_fn is not None:
            try:
                if not filter_fn(history):
                    continue
            except Exception:
                continue

        filtered_histories.append(history)

        # Check max_sessions limit
        if max_sessions is not None and len(filtered_histories) >= max_sessions:
            break

    # Convert to dataset format
    if format == "conversational":
        data = _to_conversational_format(filtered_histories, include_system)
    elif format == "prompt_completion":
        data = _to_prompt_completion_format(filtered_histories, include_system)
    else:
        raise ValueError(
            f"Unknown format: {format}. Must be 'conversational' or 'prompt_completion'"
        )

    return Dataset.from_dict(data)


def _load_judgments(project_slug: str) -> Dict[str, str]:
    """Load judgments from trace analysis database.

    The trace analysis stores judgments by group_id, which often corresponds
    to session_id in omniagents.
    """
    from omniagents.core.paths import get_traces_dir

    judgments: Dict[str, str] = {}
    db_path = get_traces_dir() / f"{project_slug}.db"

    if not db_path.exists():
        return judgments

    try:
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT group_id, judgment FROM analysis WHERE judgment IS NOT NULL"
        )
        for row in cursor:
            judgments[row["group_id"]] = row["judgment"]
        conn.close()
    except Exception:
        pass

    return judgments


def _to_conversational_format(
    histories: List[List[Dict[str, Any]]],
    include_system: bool,
    include_tool_calls: bool = True,
) -> Dict[str, List[Any]]:
    """Convert histories to conversational format for SFTTrainer.

    Output format: {"messages": [[{"role": "user", "content": "..."}, ...], ...]}

    Args:
        histories: List of conversation histories
        include_system: Include system messages
        include_tool_calls: If True, convert tool calls to OpenAI format.
                           If False, only include text messages (original behavior).
    """
    all_messages: List[List[Dict[str, Any]]] = []

    for history in histories:
        if include_tool_calls:
            messages = _convert_history_to_openai_format(history, include_system)
        else:
            # Original behavior - text only
            messages = []
            for msg in history:
                role = msg.get("role")
                if role not in ("system", "user", "assistant"):
                    continue
                if role == "system" and not include_system:
                    continue

                content = _extract_content(msg)
                if content:
                    messages.append({"role": role, "content": content})

        if messages:
            all_messages.append(messages)

    return {"messages": all_messages}


def _convert_history_to_openai_format(
    history: List[Dict[str, Any]],
    include_system: bool = True,
) -> List[Dict[str, Any]]:
    """
    Convert omniagents history format to OpenAI chat format with tool calls.

    This is essential for SFT training on agentic tasks where the student
    needs to learn tool calling behavior from the teacher.

    omniagents format:
    - {"role": "system", "content": "..."}
    - {"role": "user", "content": "..."}
    - {"role": "assistant", "content": "...", "type": "message"}
    - {"type": "function_call", "name": "geocode", "arguments": {...}, "call_id": "..."}
    - {"type": "function_call_output", "call_id": "...", "output": "..."}

    OpenAI format:
    - {"role": "system", "content": "..."}
    - {"role": "user", "content": "..."}
    - {"role": "assistant", "content": "...", "tool_calls": [...]}
    - {"role": "tool", "tool_call_id": "...", "content": "..."}
    """
    import json as _json

    messages = []

    i = 0
    while i < len(history):
        item = history[i]

        if not isinstance(item, dict):
            i += 1
            continue

        role = item.get("role")
        item_type = item.get("type")

        # System message
        if role == "system":
            if include_system:
                content = item.get("content", "")
                if content:
                    messages.append({"role": "system", "content": content})
            i += 1
            continue

        # User message
        if role == "user":
            content = _extract_content(item)
            if content:
                messages.append({"role": "user", "content": content})
            i += 1
            continue

        # Assistant message (text response)
        if role == "assistant":
            content = _extract_content(item)

            # Look ahead for tool calls that follow this message
            tool_calls = []
            j = i + 1
            while j < len(history):
                next_item = history[j]
                if next_item.get("type") == "function_call":
                    call_id = next_item.get("call_id", f"call_{j}")
                    name = next_item.get("name", "")
                    args = next_item.get("arguments", {})
                    if isinstance(args, str):
                        args_str = args
                    else:
                        args_str = _json.dumps(args)

                    tool_calls.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": args_str,
                            },
                        }
                    )
                    j += 1
                elif next_item.get("type") == "function_call_output":
                    # Tool output - we'll handle after adding assistant message
                    break
                elif next_item.get("role") in ("user", "assistant"):
                    # Next turn
                    break
                else:
                    j += 1

            # Build assistant message
            assistant_msg: Dict[str, Any] = {"role": "assistant"}
            if content:
                assistant_msg["content"] = content
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls

            # Only add if there's content or tool calls
            if content or tool_calls:
                messages.append(assistant_msg)

            i = j  # Skip to after tool calls
            continue

        # Function call (standalone, not preceded by assistant message)
        if item_type == "function_call":
            # Create an assistant message with this tool call
            call_id = item.get("call_id", f"call_{i}")
            name = item.get("name", "")
            args = item.get("arguments", {})
            if isinstance(args, str):
                args_str = args
            else:
                args_str = _json.dumps(args)

            # Look for more consecutive tool calls
            tool_calls = [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": args_str,
                    },
                }
            ]

            j = i + 1
            while j < len(history):
                next_item = history[j]
                if next_item.get("type") == "function_call":
                    nc_id = next_item.get("call_id", f"call_{j}")
                    nc_name = next_item.get("name", "")
                    nc_args = next_item.get("arguments", {})
                    if isinstance(nc_args, str):
                        nc_args_str = nc_args
                    else:
                        nc_args_str = _json.dumps(nc_args)

                    tool_calls.append(
                        {
                            "id": nc_id,
                            "type": "function",
                            "function": {
                                "name": nc_name,
                                "arguments": nc_args_str,
                            },
                        }
                    )
                    j += 1
                else:
                    break

            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": tool_calls,
                }
            )
            i = j
            continue

        # Function call output (tool response)
        if item_type == "function_call_output":
            call_id = item.get("call_id", "")
            output = item.get("output", "")
            if isinstance(output, dict):
                output = _json.dumps(output)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": str(output),
                }
            )
            i += 1
            continue

        # Skip unknown items
        i += 1

    return messages


def _to_prompt_completion_format(
    histories: List[List[Dict[str, Any]]],
    include_system: bool,
) -> Dict[str, List[str]]:
    """Convert histories to prompt-completion format for SFTTrainer.

    Output format: {"prompt": [...], "completion": [...]}

    For multi-turn conversations, the prompt includes all messages up to
    the final user message, and completion is the final assistant response.
    """
    prompts: List[str] = []
    completions: List[str] = []

    for history in histories:
        # Find the last user message and last assistant message
        last_user_idx = -1
        last_assistant_idx = -1

        for i, msg in enumerate(history):
            role = msg.get("role")
            if role == "user":
                last_user_idx = i
            elif role == "assistant":
                last_assistant_idx = i

        # Skip if no user or assistant message
        if last_user_idx == -1 or last_assistant_idx == -1:
            continue

        # Skip if assistant message comes before last user message
        # (we want prompt -> completion flow)
        if last_assistant_idx < last_user_idx:
            continue

        # Build prompt from all messages up to and including last user message
        prompt_parts: List[str] = []
        for i, msg in enumerate(history):
            if i > last_user_idx:
                break

            role = msg.get("role")
            if role not in ("system", "user", "assistant"):
                continue
            if role == "system" and not include_system:
                continue

            content = _extract_content(msg)
            if content:
                if role == "system":
                    prompt_parts.append(f"System: {content}")
                elif role == "user":
                    prompt_parts.append(f"User: {content}")
                elif role == "assistant":
                    prompt_parts.append(f"Assistant: {content}")

        # Get completion (final assistant response)
        completion = _extract_content(history[last_assistant_idx])

        if prompt_parts and completion:
            prompts.append("\n\n".join(prompt_parts))
            completions.append(completion)

    return {"prompt": prompts, "completion": completions}


def _extract_content(msg: Dict[str, Any]) -> str:
    """Extract text content from a message."""
    content = msg.get("content")

    if isinstance(content, str):
        return content.strip()

    # Handle structured content (list of content parts)
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    text = part.get("text", "")
                    if text:
                        parts.append(text)
                elif part.get("type") == "output_text":
                    text = part.get("text", "")
                    if text:
                        parts.append(text)
            elif isinstance(part, str):
                parts.append(part)
        return " ".join(parts).strip()

    return ""
