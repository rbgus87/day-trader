# PROJECT.md - PyTorch ML 프로젝트 설정

## 프로젝트 정보

```yaml
project_name: "My Project"
description: "프로젝트 설명을 입력하세요"
version: "0.1.0"
project_type: "ml"
```

---

## 🤖 PyTorch 스택 설정

```yaml
ml:
  type: "training"            # training | inference | research

  domain: "vision"            # vision | nlp | audio | multimodal | tabular

  framework: "PyTorch"

  pytorch:
    version: "2.x"
    lightning: true            # PyTorch Lightning 사용
    torchvision: true          # vision 도메인
    torchaudio: false          # audio 도메인

  training:
    distributed: false         # DDP / FSDP
    mixed_precision: true      # BF16 / FP16
    accelerator: "cuda"        # cuda | mps | tpu | cpu
    gradient_checkpointing: false

  # 데이터
  data:
    pipeline: "torchvision"    # torchvision | albumentations | custom
    dataloader_workers: 4
    prefetch_factor: 2
    storage: "local"           # local | S3 | GCS | HuggingFace
    format: "image_folder"     # image_folder | hf_datasets | custom

  # MLOps
  mlops:
    experiment_tracking: "Weights & Biases"  # W&B | MLflow | TensorBoard
    model_registry: false
    hyperparameter_tuning: "Optuna"  # Optuna | Ray Tune | none

  # 서빙
  serving:
    framework: "FastAPI"       # FastAPI | TorchServe | Triton
    export: "ONNX"             # ONNX | TorchScript | none
    containerization: true
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
      docstring: "google"

  commit:
    format: "conventional"

  branching:
    strategy: "github-flow"
    main: "main"
    feature: "feature/*"
```

## PyTorch 요구사항

```yaml
pytorch_requirements:
  training:
    gpu_required: true
    gpu_memory: 12             # GB (minimum)
    multi_gpu: false
    max_epochs: 100
    early_stopping: true

  model:
    architecture: "custom"     # ResNet | ViT | custom
    pretrained: true           # 사전 학습 모델 사용
    checkpoint_frequency: 5    # 에폭 단위

  data:
    train_split: 0.8
    val_split: 0.1
    test_split: 0.1
    augmentation: true

  evaluation:
    metrics: ["accuracy", "f1"]
    confusion_matrix: true
    visualization: true

  serving:
    latency_target: 50         # ms
    batch_inference: true
```

## 환경변수

```yaml
env_vars:
  required:
    - CUDA_VISIBLE_DEVICES
  optional:
    - WANDB_API_KEY
    - MLFLOW_TRACKING_URI
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
