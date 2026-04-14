"""strategy/archive — 2026-04-14 이후 운영 비활성화된 전략들.

이 디렉토리는 pytest collection에서 제외되며, 실전/백테스트 어느 경로에서도
instantiate되지 않는다. 실험 재개 시 참고용으로 보존.

archive 사유:
- flow_strategy       (수급추종): 2026-03-30 이후 미사용
- pullback_strategy   (눌림목): 2026-03-30 이후 미사용
- gap_strategy        (갭앤고): 2026-03-30 이후 미사용
- open_break_strategy (시가돌파): 2026-03-30 이후 미사용, ORB 폐기
- big_candle_strategy (세력캔들): 2026-03-30 이후 미사용
"""
