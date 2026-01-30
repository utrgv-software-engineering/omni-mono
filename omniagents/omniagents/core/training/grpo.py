"""GRPO (Group Relative Policy Optimization) training for omniagents.

This module provides a high-level API for training agents using GRPO
with omniagents evaluation measures as the reward signal.

GRPO is an RL algorithm that:
1. Generates multiple rollouts for each problem (with tool use)
2. Scores them with rewards (from omniagents measures)
3. Computes advantages by comparing within each problem's group
4. Updates the model to make better solutions more likely

The key benefit is that you can train agents with tools using only
pass/fail evaluation signals!
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union, TYPE_CHECKING
import subprocess

from .rewards import measure_to_reward, MeasureRewardAdapter, combine_rewards
from .dataset import eval_suite_to_hf_dataset


# Response schema for Qwen2.5 models (similar to TRL's qwen3_schema but without <think> blocks)
# This allows TRL's GRPOTrainer to parse tool calls from Qwen2.5 models.
# Format: <tool_call>\n{"name": "func", "arguments": {...}}\n</tool_call>
QWEN25_RESPONSE_SCHEMA = {
    "x-regex": r"^(?P<content>.*?)(?=(?:<tool_call>|<\|im_end\|>|$))(?P<tool_calls>(?:<tool_call>.+?</tool_call>\s*)+)?\s*(?:<\|im_end\|>|$)",
    "type": "object",
    "properties": {
        "role": {"const": "assistant"},
        "content": {"type": "string"},
        "tool_calls": {
            "type": "array",
            "x-regex-iterator": r"<tool_call>\s*(.+?)\s*</tool_call>",
            "items": {
                "x-parser": "json",
                "x-parser-args": {"transform": "{type: 'function', function: @}"},
                "type": "object",
                "properties": {
                    "type": {"const": "function"},
                    "function": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "arguments": {
                                "type": "object",
                                "additionalProperties": {},
                            },
                        },
                    },
                },
            },
        },
    },
}


if TYPE_CHECKING:
    from datasets import Dataset
    from transformers import PreTrainedModel, PreTrainedTokenizer
    from trl import GRPOTrainer as TRLGRPOTrainer
    from omniagents.notebook.evaluation import EvalSuite
    from agents import Agent


def _extract_hf_model_name(model: str) -> str:
    """Extract HuggingFace model name from various formats.

    Supports:
    - Direct HF model: "Qwen/Qwen3-0.6B"
    - LiteLLM/Ollama: "litellm/ollama_chat/hf.co/Qwen/Qwen3-0.6B-GGUF:Q8_0"
    - Ollama HF: "hf.co/Qwen/Qwen3-0.6B-GGUF:Q8_0"

    Returns the canonical HuggingFace model name.
    """
    if model is None:
        raise ValueError("Agent must have a model specified for training")

    # Strip litellm/ollama_chat prefix
    if model.startswith("litellm/ollama_chat/"):
        model = model[len("litellm/ollama_chat/"):]

    # Handle hf.co prefix (Ollama HuggingFace models)
    if model.startswith("hf.co/"):
        model = model[len("hf.co/"):]
        # Format: Qwen/Qwen3-0.6B-GGUF:Q8_0
        # Strip quantization tag
        if ":" in model:
            model = model.split(":")[0]
        # Strip -GGUF suffix
        if model.endswith("-GGUF"):
            model = model[:-5]

    return model


class ToolSchemaWrapper(dict):
    """A wrapper that makes a JSON schema look like a callable tool to TRL.

    This class:
    1. Subclasses dict so isinstance(tool, dict) returns True for transformers
    2. Has __name__ attribute so TRL's tool.__name__ works
    3. Is callable so TRL can execute the underlying tool function

    This bypasses the Google-style docstring limitation in transformers by
    using pre-parsed JSON schemas from the openai-agents SDK (which supports
    Google/NumPy/Sphinx docstrings via griffe).
    """

    def __init__(self, schema: Dict[str, Any], func: Callable):
        super().__init__(schema)
        self._func = func
        self.__name__ = schema["function"]["name"]
        self.__doc__ = schema["function"].get("description", "")

    def __call__(self, *args, **kwargs):
        return self._func(*args, **kwargs)


def _extract_tools_as_json_schemas(agent: "Agent") -> List[ToolSchemaWrapper]:
    """Extract tool JSON schemas from an Agent for TRL's GRPOTrainer.

    This extracts the pre-parsed JSON schemas from FunctionTool objects,
    bypassing the Google-style docstring limitation in transformers.
    The openai-agents SDK uses griffe which auto-detects docstring style
    (Google/NumPy/Sphinx) and stores the schema in FunctionTool.params_json_schema.

    Returns ToolSchemaWrapper objects that:
    - Are dict subclasses (so transformers uses them as JSON schemas directly)
    - Have __name__ attribute (so TRL can build its tool dict)
    - Are callable (so TRL can execute the tools)
    """
    if not agent.tools:
        return []

    wrappers = []
    for tool in agent.tools:
        # FunctionTool from agents SDK already has the parsed schema
        if hasattr(tool, 'params_json_schema'):
            schema = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.params_json_schema
                }
            }
            # Get the original function for direct invocation by TRL
            if hasattr(tool, '_original_func'):
                func = tool._original_func
            else:
                raise ValueError(
                    f"Tool '{tool.name}' missing _original_func attribute. "
                    f"Make sure tools are created with omniagents' @function_tool decorator."
                )

            wrappers.append(ToolSchemaWrapper(schema, func))

    return wrappers


def _get_agent_instructions(agent: "Agent") -> Optional[str]:
    """Extract instructions from an Agent.

    Instructions can be a string or a callable. For training, we only
    support static string instructions.
    """
    instructions = agent.instructions
    if instructions is None:
        return None
    if callable(instructions):
        raise ValueError(
            "Dynamic instructions (callable) are not supported for training. "
            "Please use static string instructions."
        )
    return instructions


@dataclass
class GRPOTrainingConfig:
    """Configuration for GRPO training.

    This wraps TRL's GRPOConfig with sensible defaults for agent training.

    Attributes:
        # Training parameters
        num_train_epochs: Number of training epochs (default: 1)
        per_device_batch_size: Problems per batch (default: 2)
        gradient_accumulation_steps: Gradient accumulation (default: 2)
        learning_rate: Learning rate (default: 1e-5)
        max_grad_norm: Gradient clipping (default: 1.0)

        # GRPO-specific parameters
        num_generations: Number of rollouts per problem (default: 4)
        max_completion_length: Maximum tokens to generate (default: 1024).
            For agents with tools, this must be large enough to fit ALL tool
            calls plus the final response. Each tool call uses ~50-70 tokens.
            256 is too small for multi-step tool use.
        temperature: Sampling temperature for diversity (default: 0.8)
        mask_truncated_completions: Exclude truncated completions from loss
            calculation to prevent learning from garbage (default: True)

        # Tool-related parameters
        max_tool_calling_iterations: Max tool call rounds (default: 5)

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
    gradient_accumulation_steps: int = 2
    learning_rate: float = 1e-5
    max_grad_norm: float = 1.0

    # GRPO-specific parameters
    num_generations: int = 4
    max_completion_length: int = 1024
    temperature: float = 0.8
    mask_truncated_completions: bool = True

    # Memory optimization
    gradient_checkpointing: bool = False  # Trade compute for memory (useful for 8GB VRAM)
    torch_dtype: str = "bfloat16"  # Model dtype: "bfloat16", "float16", "float32"

    # Tool-related parameters
    max_tool_calling_iterations: int = 5

    # Output
    output_dir: str = "./grpo_output"
    save_strategy: str = "no"
    logging_steps: int = 5

    # Reporting
    report_to: str = "none"

    def to_trl_config(self) -> "GRPOConfig":
        """Convert to TRL's GRPOConfig."""
        try:
            from trl import GRPOConfig
        except ImportError:
            raise ImportError(
                "trl library is required for GRPO training. "
                "Install with: pip install trl>=0.12.0"
            )

        effective_batch = self.per_device_batch_size * self.gradient_accumulation_steps
        if effective_batch % self.num_generations != 0:
            adjusted_accum = self.num_generations
            print(f"Note: Adjusting gradient_accumulation_steps from {self.gradient_accumulation_steps} "
                  f"to {adjusted_accum} to be divisible by num_generations={self.num_generations}")
        else:
            adjusted_accum = self.gradient_accumulation_steps

        return GRPOConfig(
            output_dir=self.output_dir,
            num_train_epochs=self.num_train_epochs,
            per_device_train_batch_size=self.per_device_batch_size,
            gradient_accumulation_steps=adjusted_accum,
            learning_rate=self.learning_rate,
            max_grad_norm=self.max_grad_norm,
            num_generations=self.num_generations,
            max_completion_length=self.max_completion_length,
            max_tool_calling_iterations=self.max_tool_calling_iterations,
            temperature=self.temperature,
            mask_truncated_completions=self.mask_truncated_completions,
            gradient_checkpointing=self.gradient_checkpointing,
            save_strategy=self.save_strategy,
            logging_steps=self.logging_steps,
            report_to=self.report_to,
        )


@dataclass
class GRPOTrainingResult:
    """Result of GRPO training.

    Attributes:
        model_path: Path to the saved model (if saved)
        metrics: Training metrics from the trainer
        final_loss: Final training loss
        trainer: The underlying TRL GRPOTrainer (for advanced use)
        tools: Tool schemas used during training (for inference)
        instructions: System instructions used during training (for inference)
    """

    model_path: Optional[str] = None
    metrics: List[Dict[str, Any]] = field(default_factory=list)
    final_loss: Optional[float] = None
    trainer: Optional[Any] = None
    tools: Optional[List[Dict[str, Any]]] = None
    instructions: Optional[str] = None
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
            result = train_grpo(agent, suite, ...)
            result.save_model("./trained_model")
            ollama_name = result.to_ollama("my-trained-agent")

            # Now use with omniagents
            agent.model = f"litellm/ollama_chat/{ollama_name}"
        """
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
                "python", str(convert_script),
                str(path),
                "--outtype", quantization,
                "--outfile", str(gguf_path),
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
                # Escape for Python string
                escaped_template = template.replace('\\', '\\\\').replace('"', '\\"')
                modelfile_content += f'TEMPLATE """{template}"""\n\n'
            for stop in stop_tokens:
                modelfile_content += f'PARAMETER stop "{stop}"\n'

            modelfile_path.write_text(modelfile_content)

            # Step 3: Create Ollama model from GGUF
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
            raise FileNotFoundError(f"convert_hf_to_gguf.py not found at {llama_cpp_path}")

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

    def _get_ollama_template(self, model_path: Path) -> tuple[Optional[str], list[str]]:
        """Get the Ollama chat template based on the model architecture.

        Reads config.json to detect the model architecture, maps it to a known
        Ollama base model, and fetches the template from that model.

        Returns:
            Tuple of (template_string, list_of_stop_tokens)
        """
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
                pull_result = subprocess.run(
                    ["ollama", "pull", ollama_model],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if pull_result.returncode == 0:
                    # Retry getting template
                    result = subprocess.run(
                        ["ollama", "show", ollama_model, "--template"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        print(f"  Using template from: {ollama_model}")
                        return result.stdout, stop_tokens
        except subprocess.TimeoutExpired:
            print(f"  Warning: Timeout fetching template from {ollama_model}")
        except Exception as e:
            print(f"  Warning: Failed to get template: {e}")

        return None, stop_tokens

    def _prepare_for_inference(self) -> None:
        """Prepare the model for inference (eval mode, disable gradient checkpointing)."""
        if self._inference_ready:
            return

        if self.trainer is None:
            raise ValueError("No trainer available - training may have failed")

        model = self.trainer.model
        model.eval()
        if hasattr(model, 'gradient_checkpointing_disable'):
            model.gradient_checkpointing_disable()

        self._inference_ready = True

    def generate(
        self,
        prompt: str,
        *,
        max_new_tokens: int = 256,
        temperature: float = 0.7,
        repetition_penalty: float = 1.2,
        tools: Optional[List[Dict[str, Any]]] = None,
        instructions: Optional[str] = None,
    ) -> str:
        """Generate a response using the trained model.

        This is a convenience method for quick testing after training.
        For production use, convert to Ollama with `to_ollama()`.

        Args:
            prompt: The user prompt to respond to
            max_new_tokens: Maximum tokens to generate (default: 256)
            temperature: Sampling temperature (default: 0.7)
            repetition_penalty: Penalty for repetition (default: 1.2)
            tools: Override tool schemas (default: use training tools)
            instructions: Override system instructions (default: use training instructions)

        Returns:
            The generated response text

        Example:
            result = train_grpo(agent, suite, ...)
            response = result.generate("Compare the weather in London and Paris.")
            print(response)
        """
        import torch

        self._prepare_for_inference()

        model = self.trainer.model
        tokenizer = self.trainer.processing_class

        # Use provided overrides or fall back to training values
        effective_tools = tools if tools is not None else self.tools
        effective_instructions = instructions if instructions is not None else self.instructions

        # Build messages
        messages = []
        if effective_instructions:
            messages.append({"role": "system", "content": effective_instructions})
        messages.append({"role": "user", "content": prompt})

        # Apply chat template with tools if available
        template_kwargs = {
            "add_generation_prompt": True,
            "tokenize": True,
            "return_dict": True,
            "return_tensors": "pt",
        }
        if effective_tools:
            template_kwargs["tools"] = effective_tools

        inputs = tokenizer.apply_chat_template(messages, **template_kwargs)
        inputs = {k: v.to(model.device) for k, v in inputs.items()}

        # Generate
        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=True,
                temperature=temperature,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
                repetition_penalty=repetition_penalty,
            )

        # Decode only the new tokens
        response = tokenizer.decode(
            outputs[0][inputs["input_ids"].shape[-1]:],
            skip_special_tokens=True
        )

        # Strip thinking tags if present (Qwen3 uses <think>...</think>)
        if "</think>" in response:
            response = response.split("</think>")[-1].strip()

        return response


class GRPOTrainer:
    """High-level GRPO trainer for omniagents agents.

    This class wraps TRL's GRPOTrainer with omniagents-specific functionality:
    - Uses Agent's tools, instructions, and model
    - Automatic conversion of measures to reward functions
    - Integration with EvalSuite for training data

    Example:
        from omniagents import Agent, function_tool
        from omniagents.notebook import EvalSuite, measure
        from omniagents.training import GRPOTrainer, GRPOTrainingConfig

        @function_tool
        def calculate(expr: str) -> str:
            '''Evaluate a math expression.'''
            return str(eval(expr))

        agent = Agent(
            name="Math Agent",
            model="Qwen/Qwen3-0.6B",
            tools=[calculate],
            instructions="You are a math assistant. Use the calculator tool.",
        )

        @measure
        def correct_answer(ctx):
            # ... grading logic ...
            return pass_reason("Correct") or fail_reason("Incorrect")

        suite = EvalSuite.from_records(data, input_fn=..., expect_fn=...)

        trainer = GRPOTrainer(
            agent=agent,
            reward_measures=["correct_answer"],
        )
        result = trainer.train(suite)
    """

    def __init__(
        self,
        agent: "Agent",
        reward_measures: List[Union[str, Callable]],
        *,
        config: Optional[GRPOTrainingConfig] = None,
        reward_weights: Optional[List[float]] = None,
        torch_dtype: str = "bfloat16",
        device_map: str = "auto",
    ):
        """Initialize the GRPO trainer.

        Args:
            agent: The omniagents Agent to train (must have model, can have tools/instructions)
            reward_measures: List of measure names or functions to use as rewards
            config: Training configuration (default: GRPOTrainingConfig())
            reward_weights: Optional weights for combining multiple rewards
            torch_dtype: Torch dtype for model ("bfloat16", "float16", "float32")
            device_map: Device map for model loading ("auto", "cpu", "cuda")
        """
        self.agent = agent
        self.reward_measures = reward_measures
        self.config = config or GRPOTrainingConfig()
        self.reward_weights = reward_weights
        self.torch_dtype = torch_dtype
        self.device_map = device_map

        # Extract model name from agent
        self.model_name = _extract_hf_model_name(agent.model)

        # Extract tools as JSON schemas from agent
        self.tools = _extract_tools_as_json_schemas(agent)

        # Extract instructions from agent
        self.instructions = _get_agent_instructions(agent)

        self._model = None
        self._tokenizer = None
        self._reward_fn = None
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

        if self._tokenizer.pad_token is None:
            self._tokenizer.pad_token = self._tokenizer.eos_token

        # Decoder-only models require left-padding for correct generation
        self._tokenizer.padding_side = "left"

        # Set response_schema for models that TRL doesn't natively support
        # This prevents TRL from calling add_response_schema() which only supports Qwen3
        if self.tools and not getattr(self._tokenizer, "response_schema", None):
            model_lower = self.model_name.lower()
            if "qwen2.5" in model_lower or "qwen2-" in model_lower:
                print(f"Setting Qwen2.5 response schema for tool parsing...")
                self._tokenizer.response_schema = QWEN25_RESPONSE_SCHEMA

        param_count = sum(p.numel() for p in self._model.parameters())
        print(f"Model loaded! Parameters: {param_count:,}")

        if self.tools:
            print(f"Agent tools: {[t['function']['name'] for t in self.tools]}")
        if self.instructions:
            print(f"Agent instructions: {self.instructions[:100]}...")

    def _build_reward_fn(self):
        """Build the combined reward function from measures."""
        reward_adapters = []
        for measure in self.reward_measures:
            adapter = measure_to_reward(measure)
            reward_adapters.append(adapter)

        if len(reward_adapters) == 1:
            self._reward_fn = reward_adapters[0]
        else:
            self._reward_fn = combine_rewards(
                *reward_adapters,
                weights=self.reward_weights,
            )

    def train(
        self,
        suite: "EvalSuite",
    ) -> GRPOTrainingResult:
        """Train the agent using GRPO.

        Args:
            suite: EvalSuite containing training data

        Returns:
            GRPOTrainingResult with trained model and metrics
        """
        try:
            from trl import GRPOTrainer as TRLGRPOTrainer
        except ImportError:
            raise ImportError(
                "trl library is required for GRPO training. "
                "Install with: pip install trl>=0.12.0"
            )

        if self._model is None:
            self._load_model()

        if self._reward_fn is None:
            self._build_reward_fn()

        # Convert suite to HF dataset with conversational format
        print(f"Preparing dataset from {len(suite)} cases...")
        train_dataset = eval_suite_to_hf_dataset(
            suite,
            system_prompt=self.instructions,
        )
        print(f"Dataset ready: {len(train_dataset)} samples")

        # Create TRL trainer with tools
        trl_config = self.config.to_trl_config()
        print(f"GRPO Configuration:")
        print(f"  Generations per prompt: {trl_config.num_generations}")
        print(f"  Max completion length: {trl_config.max_completion_length}")
        print(f"  Temperature: {trl_config.temperature}")
        print(f"  Learning rate: {trl_config.learning_rate}")
        if self.tools:
            print(f"  Tools: {[t['function']['name'] for t in self.tools]}")

        # Build trainer kwargs
        trainer_kwargs = {
            "model": self._model,
            "args": trl_config,
            "train_dataset": train_dataset,
            "processing_class": self._tokenizer,
            "reward_funcs": self._reward_fn,
        }

        # Add tools if the agent has them
        if self.tools:
            trainer_kwargs["tools"] = self.tools

        self._trl_trainer = TRLGRPOTrainer(**trainer_kwargs)

        # Train!
        print("\nStarting GRPO training...\n")
        self._trl_trainer.train()

        # Collect metrics
        metrics = []
        if hasattr(self._trl_trainer, "state") and hasattr(self._trl_trainer.state, "log_history"):
            metrics = self._trl_trainer.state.log_history

        final_loss = None
        if metrics:
            for entry in reversed(metrics):
                if "loss" in entry:
                    final_loss = entry["loss"]
                    break

        return GRPOTrainingResult(
            metrics=metrics,
            final_loss=final_loss,
            trainer=self._trl_trainer,
            tools=self.tools,
            instructions=self.instructions,
        )

    @property
    def model(self):
        """Access the underlying model."""
        return self._model

    @property
    def tokenizer(self):
        """Access the tokenizer."""
        return self._tokenizer


def train_grpo(
    agent: "Agent",
    suite: "EvalSuite",
    reward_measures: List[Union[str, Callable]],
    *,
    config: Optional[GRPOTrainingConfig] = None,
    reward_weights: Optional[List[float]] = None,
    torch_dtype: Optional[str] = None,  # Defaults to config.torch_dtype
    device_map: str = "auto",
) -> GRPOTrainingResult:
    """Train an agent with GRPO using omniagents evaluation.

    This is the simplest way to train an agent with GRPO. For more control,
    use the GRPOTrainer class directly.

    Args:
        agent: The omniagents Agent to train
        suite: EvalSuite containing training data
        reward_measures: List of measure names or functions to use as rewards
        config: Training configuration (default: GRPOTrainingConfig())
        reward_weights: Optional weights for combining multiple rewards
        torch_dtype: Torch dtype for model
        device_map: Device map for model loading

    Returns:
        GRPOTrainingResult with trained model and metrics

    Example:
        from omniagents import Agent, function_tool
        from omniagents.notebook import EvalSuite, measure, pass_reason, fail_reason
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

        @measure
        def correct_answer(ctx):
            response = ctx.final_assistant_message.text or ""
            expected = ctx.expect.get('answer')
            if expected in response:
                return pass_reason("Correct")
            return fail_reason("Incorrect")

        suite = EvalSuite.from_records(
            data[:100],
            input_fn=lambda r: r['question'],
            expect_fn=lambda r: {'answer': r['answer']},
            measures=["correct_answer"],
        )

        config = GRPOTrainingConfig(
            num_generations=4,
            num_train_epochs=1,
            learning_rate=1e-5,
        )

        result = train_grpo(
            agent=agent,
            suite=suite,
            reward_measures=["correct_answer"],
            config=config,
        )

        # Save the trained model
        result.save_model("./trained_model")

        # Convert to Ollama for fast inference
        result.to_ollama("my-math-agent")
    """
    if config is None:
        config = GRPOTrainingConfig()

    # Use config's torch_dtype if not explicitly provided
    effective_dtype = torch_dtype if torch_dtype is not None else config.torch_dtype

    trainer = GRPOTrainer(
        agent=agent,
        reward_measures=reward_measures,
        config=config,
        reward_weights=reward_weights,
        torch_dtype=effective_dtype,
        device_map=device_map,
    )

    return trainer.train(suite)
