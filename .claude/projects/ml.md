# PROJECT.md - AI/ML 프로젝트 설정

## 프로젝트 정보

```yaml
project_name: "My Project"
description: "프로젝트 설명을 입력하세요"
version: "0.1.0"
project_type: "ml"
```

---

## 🤖 ML 스택 설정

```yaml
ml:
  type: "training"            # training | inference | pipeline | research

  domain: "nlp"               # nlp | vision | audio | multimodal | tabular

  # 프레임워크
  framework: "PyTorch"        # PyTorch | TensorFlow | JAX | scikit-learn

  # PyTorch 설정
  pytorch:
    lightning: true           # PyTorch Lightning 사용
    version: "2.x"

  # 학습 설정
  training:
    distributed: false        # 분산 학습
    mixed_precision: true     # FP16/BF16
    accelerator: "cuda"       # cuda | mps | tpu | cpu

  # MLOps
  mlops:
    experiment_tracking: "MLflow"    # MLflow | Weights & Biases | Neptune
    model_registry: true
    pipeline: "none"          # Airflow | Kubeflow | Prefect | none

  # 서빙
  serving:
    framework: "FastAPI"      # FastAPI | TorchServe | TensorFlow Serving
    containerization: true
    gpu_inference: false

  # LLM 특화 (domain: "nlp" 시)
  llm:
    type: "fine-tuning"       # fine-tuning | rag | agents | from-scratch
    base_model: ""            # llama | mistral | gpt | custom
    quantization: "4bit"      # none | 4bit | 8bit
    framework: "transformers" # transformers | vllm | llama.cpp

  # 데이터
  data:
    storage: "S3"             # S3 | GCS | Azure Blob | Local
    format: "parquet"         # parquet | csv | tfrecord | hf_datasets
    version_control: "DVC"    # DVC | LakeFS | none
```

---

## 팀 설정

```yaml
team_config:
  disabled_roles:
    - designer                # ML은 UI 디자인 불필요
    - frontend                # API 서빙만 필요 시
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

## ML 요구사항

```yaml
ml_requirements:
  # 학습 리소스
  training:
    gpu_required: true
    gpu_memory: 24            # GB (minimum)
    multi_gpu: false
    distributed: false

  # 모델 크기
  model:
    max_parameters: 7         # Billion
    quantization: "4bit"      # none | 4bit | 8bit

  # 데이터
  data:
    privacy: "public"         # public | private | sensitive
    anonymization: false
    retention_days: 90

  # 서빙
  serving:
    latency_target: 100       # ms
    throughput: 100           # requests/second
    batch_inference: true
```

## 환경변수

```yaml
env_vars:
  required:
    - AWS_ACCESS_KEY_ID
    - AWS_SECRET_ACCESS_KEY
  optional:
    - MLFLOW_TRACKING_URI
    - WANDB_API_KEY
    - HF_TOKEN
    - OPENAI_API_KEY
```

## 문서화 설정

```yaml
documentation:
  language: "ko"
  auto_generate:
    readme: true
    changelog: true
    model_card: true
```
