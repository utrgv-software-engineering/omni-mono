### 🎯 1. Motivation: What Failure Mode Are We Fixing?
*(Connects to the Analyze phase.)*

- Failure Mode: 
- Root Cause Analysis: 

### ✨ 2. The Change: What Was Improved?
*(Describes the Improve phase.)*

- Summary of Changes:
- Type of Change:
  - [ ] Prompt Refinement
  - [ ] RAG Strategy (Chunking, Retrieval, etc.)
  - [ ] Tool Definition / Schema
  - [ ] Model Change (e.g., swapped `gpt-4o-mini` → `gpt-4o`)
  - [ ] Evaluator Logic / LLM-as-Judge Prompt
  - [ ] Scenarios / Golden Dataset
  - [ ] Measures / Metrics / Failure Categories
  - [ ] Other: ________________

---

### 📊 3. Evaluation: How Do We Know It's Better?
Describe how you evaluated the change, including any tests or datasets used.

---

### 🧐 4. Qualitative Review: See the Difference
*(1–2 concrete examples so reviewers can inspect the behavior.)*

Input:
```
[example user query]
```

Before:
```
[paste prior output]
```

After:
```
[paste improved output]
```

---

### ✅ 5. CI & Deployment Checklist
- [ ] Evaluation Checks Passed Locally
- [ ] Golden Dataset Updated (evaluations/scenarios/)
  - [ ] Yes — added examples of this failure mode to prevent regressions
  - [ ] No — covered by existing cases
- [ ] Evaluator Alignment Checked
  - [ ] N/A — no evaluator changes
  - [ ] Yes — TPR/TNR (or accuracy) re-validated for updated LLM-as-Judge
- [ ] Handles Non-Determinism — improvement consistent across multiple runs
