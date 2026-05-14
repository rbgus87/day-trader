"""tests/test_universe_writer.py — universe.yaml 자동 갱신 검증.

`_write_universe_yaml`은 atomic write + 기존 파일을 .bak로 보존한다.
조건검색 성공 시 매번 호출되므로 형식/round-trip이 안정적이어야 한다.
"""

from pathlib import Path

import yaml

from pipeline.screener_scheduler import write_universe_yaml as _write_universe_yaml


def _sample_top() -> list[dict]:
    return [
        {"ticker": "005930", "name": "삼성전자", "market": "kospi"},
        {"ticker": "035720", "name": "카카오", "market": "kospi"},
        {"ticker": "247540", "name": "에코프로비엠", "market": "kosdaq"},
    ]


def test_write_creates_new_file(tmp_path: Path) -> None:
    target = tmp_path / "universe.yaml"
    _write_universe_yaml(_sample_top(), target)

    assert target.exists()
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert "stocks" in data
    assert len(data["stocks"]) == 3
    assert data["stocks"][0] == {
        "ticker": "005930", "name": "삼성전자", "market": "kospi",
    }


def test_write_backs_up_existing(tmp_path: Path) -> None:
    target = tmp_path / "universe.yaml"
    target.write_text("stocks:\n- ticker: '000000'\n", encoding="utf-8")

    _write_universe_yaml(_sample_top(), target)

    bak = tmp_path / "universe.yaml.bak"
    assert bak.exists()
    assert "000000" in bak.read_text(encoding="utf-8")
    # 새 파일에는 새 데이터
    new_data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert len(new_data["stocks"]) == 3


def test_write_replaces_existing_bak(tmp_path: Path) -> None:
    target = tmp_path / "universe.yaml"
    bak = tmp_path / "universe.yaml.bak"
    target.write_text("stocks:\n- ticker: 'V2'\n", encoding="utf-8")
    bak.write_text("stocks:\n- ticker: 'OLD_BAK'\n", encoding="utf-8")

    _write_universe_yaml(_sample_top(), target)

    # bak가 V2(직전 원본)로 교체되어야 함 — OLD_BAK는 사라짐
    bak_content = bak.read_text(encoding="utf-8")
    assert "V2" in bak_content
    assert "OLD_BAK" not in bak_content


def test_write_atomic_no_tmp_left(tmp_path: Path) -> None:
    target = tmp_path / "universe.yaml"
    _write_universe_yaml(_sample_top(), target)

    # tmp 파일이 남아있으면 안 됨
    assert not (tmp_path / "universe.yaml.tmp").exists()


def test_write_ticker_is_str(tmp_path: Path) -> None:
    """선두 0이 있는 ticker(예: '028050')는 문자열로 보존되어야 한다."""
    target = tmp_path / "universe.yaml"
    _write_universe_yaml(
        [{"ticker": "028050", "name": "삼성E&A", "market": "kospi"}],
        target,
    )

    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["stocks"][0]["ticker"] == "028050"
    assert isinstance(data["stocks"][0]["ticker"], str)


def test_write_handles_missing_optional_fields(tmp_path: Path) -> None:
    """name/market 없는 항목도 안전하게 빈 문자열/unknown 으로 채워 저장."""
    target = tmp_path / "universe.yaml"
    _write_universe_yaml([{"ticker": "999999"}], target)

    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["stocks"][0] == {
        "ticker": "999999", "name": "", "market": "unknown",
    }
