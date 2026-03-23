# PROJECT.md - LLM 파인튜닝/RAG 프로젝트 설정

## 프로젝트 정보

```yaml
project_name: "My Project"
description: "프로젝트 설명을 입력하세요"
version: "0.1.0"
project_type: "ml"
```

---

## 🤖 LLM 스택 설정

```yaml
ml:
  type: "fine-tuning"          # fine-tuning | rag | agents | inference

  domain: "nlp"

  framework: "PyTorch"

  llm:
    type: "fine-tuning"        # fine-tuning | rag | agents
    base_model: "llama"        # llama | mistral | gemma | phi | qwen
    model_size: "7B"           # 1B | 3B | 7B | 13B | 70B
    quantization: "4bit"       # none | 4bit | 8bit | GPTQ | AWQ

    # 파인튜닝 설정
    fine_tuning:
      method: "LoRA"           # LoRA | QLoRA | Full | Prefix Tuning
      lora_r: 16
      lora_alpha: 32
      target_modules: ["q_proj", "v_proj"]
      framework: "transformers"  # transformers | unsloth | axolotl

    # RAG 설정 (type: "rag" 시 사용)
    # rag:
    #   vector_db: "ChromaDB"  # ChromaDB | Pinecone | Weaviate | Qdrant | FAISS
    #   embedding: "BGE"       # BGE | E5 | OpenAI | Cohere
    #   chunking: "recursive"  # recursive | semantic | fixed
    #   chunk_size: 512
    #   retriever: "hybrid"    # dense | sparse | hybrid

    # 에이전트 설정 (type: "agents" 시 사용)
    # agents:
    #   framework: "LangChain"  # LangChain | LlamaIndex | CrewAI | AutoGen
    #   tools: []
    #   memory: "buffer"       # buffer | summary | vector

  training:
    distributed: false
    mixed_precision: true
    accelerator: "cuda"
    gradient_checkpointing: true
    flash_attention: true

  data:
    format: "jsonl"            # jsonl | parquet | hf_datasets
    storage: "local"           # local | S3 | HuggingFace
    version_control: "none"    # DVC | none

  mlops:
    experiment_tracking: "Weights & Biases"
    model_registry: false

  serving:
    framework: "vLLM"          # vLLM | TGI | Ollama | llama.cpp
    api: "OpenAI-compatible"   # OpenAI-compatible | custom
    containerization: true
    gpu_inference: true
```

---

## 팀 설정

```yaml
team_config:
  disabled_roles:
    - designer
    - frontend                 # API 서빙만 필요 시
    - accessibility
  auto_security_review: true
  default_mode: "hybrid"
```

## 프로젝트 컨벤션

```yaml
conventions:
  code_style:
    python:
      formatter: "black"
      linter: "ruff"
      type_checker: "pyright"

  commit:
    format: "conventional"

  branching:
    strategy: "github-flow"
    main: "main"
    feature: "feature/*"
```

## LLM 요구사항

```yaml
llm_requirements:
  training:
    gpu_required: true
    gpu_memory: 24             # GB (minimum, QLoRA 7B 기준)
    multi_gpu: false
    max_steps: 1000
    eval_steps: 100

  model:
    max_parameters: 7          # Billion
    quantization: "4bit"
    context_length: 4096       # 토큰
    max_new_tokens: 512

  data:
    privacy: "private"         # public | private | sensitive
    anonymization: true
    format_validation: true    # 데이터 포맷 검증

  evaluation:
    metrics: ["perplexity", "rouge", "bleu"]
    human_eval: false
    benchmark: []              # MMLU | HellaSwag | custom

  serving:
    latency_target: 500        # ms (첫 토큰)
    throughput: 50             # tokens/second
    concurrent_users: 10
    streaming: true
```

## 환경변수

```yaml
env_vars:
  required:
    - CUDA_VISIBLE_DEVICES
    - HF_TOKEN
  optional:
    - WANDB_API_KEY
    - OPENAI_API_KEY
    - DATA_DIR
    - MODEL_DIR
    - LOG_LEVEL
```

## 문서화 설정

```yaml
documentation:
  language: "ko"
  auto_generate:
    readme: true
    changelog: true
    model_card: true
  format:
    code: "Google Docstring"
```
