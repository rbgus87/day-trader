"""조건검색 전용 미니 WebSocket 클라이언트.

장전 1회만 연결 → LOGIN → ka10171 → ka10172 → 종료.
기존 ws_client.py와 독립 (상시 연결 아님).
day-trader/core/kiwoom_ws.py의 LOGIN 프로토콜 참조.
"""

import asyncio
import json
from loguru import logger

try:
    from websockets.client import connect as ws_connect
except ImportError:
    from websockets import connect as ws_connect


class ConditionSearchClient:
    """조건검색 전용 미니 WebSocket 클라이언트."""

    LOGIN_TIMEOUT = 5.0
    QUERY_TIMEOUT = 15.0

    def __init__(self, ws_url: str, access_token: str):
        self._ws_url = ws_url
        self._access_token = access_token
        self._ws = None

    async def connect(self) -> bool:
        """WebSocket 연결 + LOGIN 패킷 전송."""
        try:
            self._ws = await ws_connect(
                self._ws_url,
                ping_interval=20,
                ping_timeout=10,
            )
            logger.info("[CS] WebSocket 연결 완료")

            # LOGIN (Bearer 없이 토큰만 — day-trader 프로토콜과 동일)
            await self._ws.send(json.dumps({
                "trnm": "LOGIN",
                "token": self._access_token,
            }))
            logger.info("[CS] LOGIN 패킷 전송")

            # LOGIN 응답 대기 (PING 메시지 필터링)
            for _ in range(10):
                raw = await asyncio.wait_for(
                    self._ws.recv(), timeout=self.LOGIN_TIMEOUT
                )
                resp = json.loads(raw)
                trnm = resp.get("trnm", "")

                if trnm == "PING":
                    await self._ws.send(raw)  # 에코백
                    continue

                if trnm == "LOGIN":
                    rc = resp.get("return_code", -1)
                    if isinstance(rc, str):
                        rc = int(rc)
                    if rc != 0:
                        msg = resp.get("return_msg", "")
                        logger.error(f"[CS] LOGIN 실패 (code={rc}): {msg}")
                        return False
                    logger.info("[CS] LOGIN 성공")
                    return True

            logger.error("[CS] LOGIN 응답 타임아웃")
            return False

        except asyncio.TimeoutError:
            logger.error("[CS] LOGIN 응답 타임아웃")
            return False
        except Exception as e:
            logger.error(f"[CS] 연결 실패: {e}")
            return False

    async def fetch_condition_list(self) -> list[dict]:
        """ka10171 — 서버 저장 조건식 목록 조회.

        Returns:
            [{"seq": "0", "name": "swing_pre_cross"}, ...] 형태.
            실패 시 [].
        """
        if not self._ws:
            return []

        try:
            await self._ws.send(json.dumps({"trnm": "CNSRLST"}))
            logger.info("[CS] ka10171 조건식 목록 요청")

            for _ in range(20):
                raw = await asyncio.wait_for(
                    self._ws.recv(), timeout=self.QUERY_TIMEOUT
                )
                resp = json.loads(raw)
                trnm = resp.get("trnm", "")

                if trnm == "PING":
                    await self._ws.send(raw)
                    continue

                if trnm == "CNSRLST":
                    logger.debug(f"[CS] CNSRLST raw: {resp}")

                    items = resp.get("data", [])
                    result = []
                    for item in items:
                        if isinstance(item, list) and len(item) >= 2:
                            # ["0", "swing_pre_cross"] 형태
                            result.append({
                                "seq": str(item[0]),
                                "name": str(item[1]),
                            })
                        elif isinstance(item, dict):
                            # {"seq": "0", "name": "swing_pre_cross"} 형태
                            seq = item.get("seq") or item.get("0", "")
                            name = item.get("name") or item.get("1", "")
                            if seq or name:
                                result.append({
                                    "seq": str(seq),
                                    "name": str(name),
                                })

                    logger.info(f"[CS] 조건식 목록 수신: {len(result)}개")
                    return result

            logger.warning("[CS] ka10171 응답 타임아웃")
            return []

        except Exception as e:
            logger.error(f"[CS] ka10171 실패: {e}")
            return []

    async def execute_condition(self, seq: str) -> list[dict]:
        """ka10172 — 조건식 실행 (일반 모드).

        Args:
            seq: 조건식 일련번호

        Returns:
            [{"code": "005930", "name": "삼성전자"}, ...] 형태.
            실패 시 [].
        """
        if not self._ws:
            return []

        try:
            await self._ws.send(json.dumps({
                "trnm": "CNSRREQ",
                "seq": str(seq),
                "search_type": "0",      # 0: 일반, 1: 실시간
                "stex_tp": "K",          # K: KRX
                "cont_yn": "N",
                "next_key": "",
            }))
            logger.info(f"[CS] ka10172 조건식 실행: seq={seq}")

            all_stocks: list[dict] = []

            for _ in range(50):
                raw = await asyncio.wait_for(
                    self._ws.recv(), timeout=self.QUERY_TIMEOUT
                )
                resp = json.loads(raw)
                trnm = resp.get("trnm", "")

                if trnm == "PING":
                    await self._ws.send(raw)
                    continue

                if trnm == "CNSRREQ":
                    logger.debug(f"[CS] CNSRREQ raw: {resp}")

                    items = resp.get("data", [])
                    for item in items:
                        if not isinstance(item, dict):
                            continue

                        # 종목 코드 추출 (9001 키가 정답)
                        code = (
                            item.get("9001")
                            or item.get("jmcode")
                            or item.get("code")
                            or item.get("stk_cd")
                        )
                        # 종목명 추출 (302 키가 정답)
                        name = (
                            item.get("302")
                            or item.get("hts_kor_isnm")
                            or item.get("name")
                            or ""
                        )

                        if code:
                            code = str(code).strip()
                            # A005930 → 005930 (접두사 제거)
                            if code.startswith("A") and len(code) == 7:
                                code = code[1:]
                            if code and len(code) >= 6:
                                all_stocks.append({
                                    "code": code,
                                    "name": str(name).strip(),
                                })

                    # 연속 조회 여부
                    if resp.get("cont_yn", "N") != "Y":
                        break
                    logger.warning("[CS] 연속 조회 필요 — 첫 페이지만 사용")
                    break

            logger.info(f"[CS] 조건식 실행 완료: {len(all_stocks)}종목")
            return all_stocks

        except asyncio.TimeoutError:
            logger.warning("[CS] ka10172 응답 타임아웃")
            return []
        except Exception as e:
            logger.error(f"[CS] ka10172 실패: {e}")
            return []

    async def disconnect(self):
        """WebSocket 연결 종료."""
        if self._ws:
            try:
                await self._ws.close()
                logger.info("[CS] WebSocket 연결 종료")
            except Exception:
                pass
            self._ws = None


async def run_condition_search(
    ws_url: str,
    access_token: str,
    condition_name: str,
) -> list[dict]:
    """조건검색 실행 — 연결 → 쿼리 → 종료 원샷.

    Args:
        ws_url: WebSocket URL
        access_token: 키움 접근 토큰
        condition_name: 영웅문 저장 조건식 이름

    Returns:
        [{"code": "005930", "name": "삼성전자"}, ...] 형태. 실패 시 [].
    """
    if not ws_url or not access_token:
        logger.error("[CS] ws_url 또는 access_token 없음")
        return []

    client = ConditionSearchClient(ws_url, access_token)

    try:
        if not await client.connect():
            return []

        conditions = await client.fetch_condition_list()
        if not conditions:
            logger.warning("[CS] 조건식 목록 비어있음")
            return []

        target = next(
            (c for c in conditions if c["name"] == condition_name), None
        )
        if not target:
            available = [c["name"] for c in conditions]
            logger.error(
                f"[CS] '{condition_name}' 없음. 등록된 조건식: {available}"
            )
            return []

        logger.info(
            f"[CS] 조건식 매칭: '{condition_name}' (seq={target['seq']})"
        )

        return await client.execute_condition(target["seq"])

    except Exception as e:
        logger.error(f"[CS] 실행 오류: {e}", exc_info=True)
        return []

    finally:
        await client.disconnect()
