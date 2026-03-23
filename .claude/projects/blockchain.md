# PROJECT.md - Blockchain/Web3 프로젝트 설정

## 프로젝트 정보

```yaml
project_name: "My Project"
description: "프로젝트 설명을 입력하세요"
version: "0.1.0"
project_type: "blockchain"
```

---

## ⛓️ Blockchain 스택 설정

```yaml
blockchain:
  type: "dapp"                # dapp | protocol | token | nft | defi

  chain: "Ethereum"           # Ethereum | Solana | Polygon | Arbitrum | Base

  # 스마트 컨트랙트
  contract:
    language: "Solidity"      # Solidity | Rust (Solana) | Move
    framework: "Foundry"      # Foundry | Hardhat | Anchor (Solana)
    version: "0.8.20"

  # Foundry 설정
  foundry:
    testing: true
    fuzzing: true
    formal_verification: false

  # Hardhat 설정 (framework: "Hardhat" 시 사용)
  # hardhat:
  #   typescript: true
  #   plugins:
  #     - hardhat-deploy
  #     - hardhat-gas-reporter

  # 프론트엔드
  frontend:
    framework: "Next.js"
    wallet: "wagmi"           # wagmi | ethers | web3.js | @solana/web3.js
    ui: "RainbowKit"          # RainbowKit | ConnectKit | Web3Modal

  # 인프라
  infrastructure:
    node_provider: "Alchemy"  # Alchemy | Infura | QuickNode | Self-hosted
    ipfs: "Pinata"            # Pinata | Infura | NFT.Storage
    indexer: "The Graph"      # The Graph | Goldsky | Custom

  networks:
    mainnet: false
    testnet: true
    local: true               # Anvil / Hardhat node

  security:
    audit_required: true
    bug_bounty: false
    upgradeable: false        # Proxy pattern
```

---

## 팀 설정

```yaml
team_config:
  disabled_roles: []
  # 보안이 특히 중요한 도메인
  role_priority:
    - security
  auto_security_review: true
  default_mode: "step"        # 블록체인은 신중한 진행 권장
```

## 프로젝트 컨벤션

```yaml
conventions:
  code_style:
    solidity:
      version: "0.8.20"
      formatter: "forge fmt"
    typescript:
      indent: 2
      quotes: "single"
      semicolon: false

  commit:
    format: "conventional"

  branching:
    strategy: "github-flow"
    main: "main"
    feature: "feature/*"
```

## Blockchain 요구사항

```yaml
blockchain_requirements:
  # 보안
  security:
    audit_required: true
    formal_verification: false
    multisig: false
    timelock: true

  # 가스 최적화
  gas:
    optimization_level: "high"  # low | medium | high
    gas_limit: 3000000

  # 업그레이드
  upgradeability:
    pattern: "none"           # none | transparent | uups | diamond
    admin_control: "multisig" # eoa | multisig | governance

  # 컴플라이언스
  compliance:
    kyc: false
    aml: false
    region_restrictions: []
```

## 환경변수

```yaml
env_vars:
  required:
    - PRIVATE_KEY
    - RPC_URL
  optional:
    - ETHERSCAN_API_KEY
    - ALCHEMY_API_KEY
    - COINMARKETCAP_API_KEY
```

## 문서화 설정

```yaml
documentation:
  language: "ko"
  auto_generate:
    readme: true
    changelog: true
    natspec: true             # Solidity NatSpec
```
